import {
  extractPathname,
  isPathAllowed,
  isSafePath,
  truncate,
} from "./protected-docs";

export type SearchIndexDoc = {
  location?: unknown;
  title?: unknown;
  text?: unknown;
};

export type RankedSearchResult = {
  id: string;
  title: string;
  location: string;
  path: string;
  url: string;
  snippet: string;
  score: number;
};

export function parseSearchDocs(raw: string): SearchIndexDoc[] | null {
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

export function rankSearchResults(options: {
  docs: SearchIndexDoc[];
  query: string;
  baseUrl: URL;
  allowedPrefixes: string[];
  requestedPrefix?: string;
  maxChars: number;
}): RankedSearchResult[] {
  const phrase = options.query.toLowerCase();
  const terms = tokenizeQuery(options.query);

  const ranked: RankedSearchResult[] = [];
  for (const doc of options.docs) {
    const location = typeof doc.location === "string" ? doc.location.trim() : "";
    if (!location) continue;

    const path = locationToPath(location);
    if (!path) continue;
    if (!isSafePath(path)) continue;
    if (extractPathname(path).startsWith("/api/")) continue;
    if (!isPathAllowed(path, options.allowedPrefixes)) continue;
    if (options.requestedPrefix && !isPathAllowed(path, [options.requestedPrefix])) continue;

    const title = typeof doc.title === "string" ? doc.title.trim() : "";
    const text = typeof doc.text === "string" ? doc.text : "";
    const score = scoreDocument({ title, text, location }, terms, phrase);
    if (score <= 0) continue;

    try {
      ranked.push({
        id: makeSearchResultId(location),
        title: title || path,
        location,
        path,
        url: new URL(location, options.baseUrl).toString(),
        snippet: buildSnippet(text, terms, options.maxChars),
        score,
      });
    } catch {
      continue;
    }
  }

  ranked.sort((a, b) => b.score - a.score || a.path.localeCompare(b.path));
  return ranked;
}

export function makeSearchResultId(location: string): string {
  return `loc:${encodeURIComponent(location)}`;
}

export function parseSearchResultId(id: string): string | null {
  if (!id.startsWith("loc:")) return null;
  try {
    const location = decodeURIComponent(id.slice(4));
    return location.trim() ? location : null;
  } catch {
    return null;
  }
}

export function locationToPath(location: string): string {
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

function tokenizeQuery(query: string): string[] {
  const terms = query
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .map((part) => part.trim())
    .filter((part) => part.length > 1);
  return Array.from(new Set(terms)).slice(0, 10);
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
