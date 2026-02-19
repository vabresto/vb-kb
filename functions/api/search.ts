import {
  clampInt,
  ensureRequiredEnv,
  extractPathname,
  fetchProtectedPath,
  isPathAllowed,
  isSafePath,
  json,
  parsePrefixes,
  resolvePublicBaseUrl,
  validatePublicBaseUrl,
  type ProtectedDocsEnv,
} from "../_lib/protected-docs";
import { parseSearchDocs, rankSearchResults } from "../_lib/search-index";

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
      requestUrl: request.url,
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

  const baseUrl = resolvePublicBaseUrl(env.PUBLIC_BASE_URL, request.url);
  const ranked = rankSearchResults({
    docs,
    query,
    baseUrl,
    allowedPrefixes,
    requestedPrefix,
    maxChars,
  });
  const results = ranked.slice(0, limit);

  return json({
    query,
    indexPath,
    totalHits: ranked.length,
    count: results.length,
    results,
  });
};
