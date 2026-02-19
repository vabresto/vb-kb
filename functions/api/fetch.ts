import {
  clampInt,
  ensureRequiredEnv,
  extractPathname,
  extractTitle,
  fetchProtectedPath,
  htmlToText,
  isPathAllowed,
  isSafePath,
  json,
  parsePrefixes,
  truncate,
  validatePublicBaseUrl,
  type ProtectedDocsEnv,
} from "../_lib/protected-docs";

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
  const path = (requestUrl.searchParams.get("path") || "").trim();
  const format = (requestUrl.searchParams.get("format") || "text").trim();
  const maxChars = clampInt(requestUrl.searchParams.get("maxChars"), 1000, 200000, 60000);

  if (!path) return json({ error: "Missing path" }, 400);
  if (!isSafePath(path)) return json({ error: "Invalid path" }, 400);
  if (format !== "text" && format !== "html") {
    return json({ error: "Invalid format. Use text or html." }, 400);
  }

  const pathname = extractPathname(path);
  if (pathname.startsWith("/api/")) {
    return json({ error: "Disallowed path" }, 403);
  }

  const allowedPrefixes = parsePrefixes(env.ALLOWED_PREFIXES);
  if (!isPathAllowed(pathname, allowedPrefixes)) {
    return json(
      {
        error: `Path not allowed. Allowed prefixes: ${allowedPrefixes.join(", ")}`,
      },
      403
    );
  }

  let upstream: Awaited<ReturnType<typeof fetchProtectedPath>>;
  try {
    upstream = await fetchProtectedPath(env, path, {
      userAgent: "DexProtectedDocsFetch/1.0",
      accept: "text/html,application/xhtml+xml,text/plain,application/xml,*/*;q=0.1",
      requestUrl: request.url,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Invalid target path";
    return json({ error: message }, 400);
  }

  if (upstream.response.status === 404) return json({ error: "Not found" }, 404);
  if (!upstream.response.ok) {
    return json({ error: `Upstream error: ${upstream.response.status}` }, 502);
  }

  const contentType =
    upstream.response.headers.get("content-type") || "text/html; charset=utf-8";
  const payload = await upstream.response.text();
  const title = contentType.includes("html") ? extractTitle(payload) || "" : "";

  if (format === "html") {
    return json({
      path,
      url: upstream.target.toString(),
      title,
      contentType,
      content: truncate(payload, maxChars),
    });
  }

  const content = contentType.includes("html") ? htmlToText(payload) : payload.trim();
  return json({
    path,
    url: upstream.target.toString(),
    title,
    contentType: "text/plain; charset=utf-8",
    content: truncate(content, maxChars),
  });
};
