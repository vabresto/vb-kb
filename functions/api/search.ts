import {
  clampInt,
  ensureRequiredEnv,
  extractPathname,
  fetchProtectedPath,
  isPathAllowed,
  isSafePath,
  json,
  parsePrefixes,
  truncate,
  validatePublicBaseUrl,
  type ProtectedDocsEnv,
} from "../_lib/protected-docs";

type SearchIndexDoc = {
  location?: unknown;
  title?: unknown;
  text?: unknown;
};

type SearchResult = {
  title: string;
  location: string;
  path: string;
  url: string;
  snippet: string;
  score: number;
};

const DEFAULT_SEARCH_INDEX_PATH = "/search/search_index.json";

export const onRequestGet: PagesFunction<ProtectedDocsEnv> = async ({
  request,
  env,
}) => {
  const missingEnv = ensureRequiredEnv(env);
  if (missingEnv.length > 0) {
    return json(
      {
        error: `Missing required environment variables: ${missingEnv.join(", ")}`,
      },
      500
    );
  }
  const baseUrlError = validatePublicBaseUrl(env.PUBLIC_BASE_URL);
  if (baseUrlError) return json({ error: baseUrlError }, 500);

  const requestUrl = new URL(request.url);
  const query = (requestUrl.searchParams.get("q") || "").trim();
  const limit = clampInt(requestUrl.searchParams.get("limit"), 1, 25, 8);
  const maxChars = clampInt(requestUrl.searchParams.get("maxChars"), 120, 5000, 320);
  const pathPrefix = (requestUrl.searchParams.get("pathPrefix") || "").trim();
  const indexPath = (env.SEARCH_INDEX_PATH || DEFAULT_SEARCH_INDEX_PATH).trim();

  if (!query) return json({ error: "Missing q" }, 400);
  if (query.length < 2) return json({ error: "q must be at least 2 characters" }, 400);
  if (!isSafePath(indexPath)) {
    return json({ error: "SEARCH_INDEX_PATH is invalid" }, 500);
  }

  const indexPathname = extractPathname(indexPath);
  if (indexPathname.startsWith("/api/")) {
    return json({ error: "SEARCH_INDEX_PATH must not point to /api/*" }, 500);
  }

  const allowedPrefixes = parsePrefixes(env.ALLOWED_PREFIXES);
  let requestedPrefix = "";
  if (pathPrefix) {
    if (!isSafePath(pathPrefix)) return json({ error: "Invalid pathPrefix" }, 400);
    requestedPrefix = extractPathname(pathPrefix);
    if (requestedPrefix.startsWith("/api/")) {
      return json({ error: "pathPrefix cannot target /api/*" }, 400);
    }
    if (!isPathAllowed(requestedPrefix, allowedPrefixes)) {
      return json(
        {
          error: `pathPrefix not allowed. Allowed prefixes: ${allowedPrefixes.join(", ")}`,
        },
        403
      );
    }
  }

  let upstream: Awaited<ReturnType<typeof fetchProtectedPath>>;
  try {
    upstream = await fetchProtectedPath(env, indexPath, {
      userAgent: "DexProtectedDocsSearch/1.0",
      accept: "application/json,text/plain,*/*;q=0.1",
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Invalid SEARCH_INDEX_PATH";
    return json({ error: message }, 500);
  }

  if (upstream.response.status === 404) {
    return json({ error: "Search index not found" }, 404);
  }
  if (!upstream.response.ok) {
    return json({ error: `Upstream error: ${upstream.response.status}` }, 502);
  }

  const raw = await upstream.response.text();
  const docs = parseSearchDocs(raw);
  if (!docs) {
    return json({ error: "Search index format is invalid" }, 502);
  }

  const phrase = query.toLowerCase();
  const terms = tokenizeQuery(query);
  const baseUrl = new URL(env.PUBLIC_BASE_URL);

  const ranked: SearchResult[] = [];
  for (const doc of docs) {
    const location = typeof doc.location === "string" ? doc.location.trim() : "";
    if (!location) continue;

    const path = locationToPath(location);
    if (!path) continue;
    if (!isSafePath(path)) continue;
    if (extractPathname(path).startsWith("/api/")) continue;
    if (!isPathAllowed(path, allowedPrefixes)) continue;
    if (requestedPrefix && !isPathAllowed(path, [requestedPrefix])) continue;

    const title = typeof doc.title === "string" ? doc.title.trim() : "";
    const text = typeof doc.text === "string" ? doc.text : "";
    const score = scoreDocument({ title, text, location }, terms, phrase);
    if (score <= 0) continue;

    try {
      ranked.push({
        title: title || path,
        location,
        path,
        url: new URL(location, baseUrl).toString(),
        snippet: buildSnippet(text, terms, maxChars),
        score,
      });
    } catch {
      continue;
    }
  }

  ranked.sort((a, b) => b.score - a.score || a.path.localeCompare(b.path));
  const results = ranked.slice(0, limit);

  return json({
    query,
    indexPath,
    totalHits: ranked.length,
    count: results.length,
    results,
  });
};

function parseSearchDocs(raw: string): SearchIndexDoc[] | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }

  if (Array.isArray(parsed)) {
    return parsed as SearchIndexDoc[];
  }

  if (parsed && typeof parsed === "object" && Array.isArray((parsed as { docs?: unknown }).docs)) {
    return (parsed as { docs: SearchIndexDoc[] }).docs;
  }

  return null;
}

function tokenizeQuery(query: string): string[] {
  const terms = query
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .map((part) => part.trim())
    .filter((part) => part.length > 1);
  return Array.from(new Set(terms)).slice(0, 10);
}

function locationToPath(location: string): string {
  const locationWithoutHash = location.split("#", 1)[0] || "";
  if (!locationWithoutHash) return "/";

  if (/^https?:\/\//i.test(locationWithoutHash)) {
    try {
      return new URL(locationWithoutHash).pathname || "/";
    } catch {
      return "";
    }
  }

  return locationWithoutHash.startsWith("/") ? locationWithoutHash : `/${locationWithoutHash}`;
}

function scoreDocument(
  doc: { title: string; text: string; location: string },
  terms: string[],
  phrase: string
): number {
  const title = doc.title.toLowerCase();
  const text = doc.text.toLowerCase();
  const location = doc.location.toLowerCase();

  let score = 0;
  if (phrase && title.includes(phrase)) score += 40;
  if (phrase && text.includes(phrase)) score += 20;

  let matchedTerms = 0;
  for (const term of terms) {
    let matched = false;
    if (title.includes(term)) {
      score += 14;
      matched = true;
    }
    if (location.includes(term)) {
      score += 6;
      matched = true;
    }

    const hitCount = countOccurrences(text, term);
    if (hitCount > 0) {
      score += Math.min(hitCount, 5) * 2;
      matched = true;
    }

    if (matched) matchedTerms += 1;
  }

  if (terms.length > 1 && matchedTerms === terms.length) score += 12;
  return score;
}

function countOccurrences(haystack: string, needle: string): number {
  if (!needle) return 0;
  let count = 0;
  let index = 0;
  while (true) {
    index = haystack.indexOf(needle, index);
    if (index === -1) break;
    count += 1;
    index += needle.length;
  }
  return count;
}

function buildSnippet(text: string, terms: string[], maxChars: number): string {
  const clean = text.replace(/\s+/g, " ").trim();
  if (!clean) return "";

  const lower = clean.toLowerCase();
  let bestIndex = -1;
  for (const term of terms) {
    const index = lower.indexOf(term);
    if (index >= 0 && (bestIndex === -1 || index < bestIndex)) {
      bestIndex = index;
    }
  }

  if (bestIndex === -1) return truncate(clean, maxChars);

  const start = Math.max(0, bestIndex - Math.floor(maxChars * 0.35));
  const end = Math.min(clean.length, start + maxChars);
  const prefix = start > 0 ? "..." : "";
  const suffix = end < clean.length ? "..." : "";
  return `${prefix}${clean.slice(start, end).trim()}${suffix}`;
}
