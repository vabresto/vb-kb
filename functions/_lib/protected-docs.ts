export interface ProtectedDocsEnv {
  ASSETS?: {
    fetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response>;
  };
  PUBLIC_BASE_URL?: string;
  ALLOWED_PREFIXES?: string;
  SEARCH_INDEX_PATH?: string;
  MCP_SERVER_NAME?: string;
  MCP_SERVER_VERSION?: string;
  ACCESS_OIDC_ISSUER?: string;
  ACCESS_AUTHORIZATION_URL?: string;
  ACCESS_TOKEN_URL?: string;
  ACCESS_JWKS_URL?: string;
  ACCESS_CLIENT_ID?: string;
  ACCESS_CLIENT_SECRET?: string;
  OAUTH_ISSUER?: string;
  OAUTH_SIGNING_KEY?: string;
}

const DEFAULT_USER_AGENT = "DexProtectedDocs/1.0";

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

export function clampInt(
  value: string | null,
  min: number,
  max: number,
  fallback: number
): number {
  const parsed = Number(value ?? "");
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(Math.max(Math.trunc(parsed), min), max);
}

export function parsePrefixes(raw?: string): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((prefix) => (prefix.startsWith("/") ? prefix : `/${prefix}`))
    .map((prefix) => prefix.replace(/\/{2,}/g, "/"))
    .map((prefix) => (prefix === "/" ? prefix : prefix.replace(/\/$/, "")));
}

export function isSafePath(path: string): boolean {
  if (!path.startsWith("/")) return false;
  if (path.startsWith("//")) return false;
  if (path.includes("://")) return false;
  if (/[\\\r\n\0]/.test(path)) return false;

  const pathOnly = path.split(/[?#]/, 1)[0] ?? path;
  let decoded = pathOnly;
  try {
    decoded = decodeURIComponent(pathOnly);
  } catch {
    return false;
  }

  return !decoded.includes("..");
}

export function extractPathname(path: string): string {
  try {
    return new URL(path, "https://internal.example").pathname;
  } catch {
    return path.split(/[?#]/, 1)[0] ?? path;
  }
}

export function isPathAllowed(pathname: string, allowedPrefixes: string[]): boolean {
  if (!allowedPrefixes.length) return true;
  return allowedPrefixes.some((prefix) => {
    if (prefix === "/") return true;
    return pathname === prefix || pathname.startsWith(`${prefix}/`);
  });
}

export function ensureRequiredEnv(env: ProtectedDocsEnv): string[] {
  void env;
  return [];
}

export function validatePublicBaseUrl(rawBaseUrl?: string): string | null {
  if (!rawBaseUrl) return null;
  try {
    parseBaseUrl(rawBaseUrl);
    return null;
  } catch (error) {
    if (error instanceof Error) return error.message;
    return "PUBLIC_BASE_URL is invalid";
  }
}

function parseBaseUrl(rawBaseUrl: string): URL {
  const base = new URL(rawBaseUrl);
  if (!["https:", "http:"].includes(base.protocol)) {
    throw new Error("PUBLIC_BASE_URL must use http or https");
  }
  return base;
}

export function resolvePublicBaseUrl(
  rawBaseUrl: string | undefined,
  requestUrl: string
): URL {
  if (rawBaseUrl) return parseBaseUrl(rawBaseUrl);
  const fallback = new URL(requestUrl);
  if (!["https:", "http:"].includes(fallback.protocol)) {
    throw new Error("Request URL must use http or https");
  }
  return fallback;
}

export function resolveTarget(
  path: string,
  rawBaseUrl: string | undefined,
  requestUrl: string
): URL {
  const base = resolvePublicBaseUrl(rawBaseUrl, requestUrl);
  const target = new URL(path, base);
  if (target.origin !== base.origin) {
    throw new Error("Cross-origin path is not allowed");
  }
  return target;
}

export function buildAssetPathCandidates(path: string): string[] {
  const pathname = extractPathname(path);
  const trimmed = pathname || "/";
  const candidates: string[] = [trimmed];

  const isRoot = trimmed === "/";
  const hasExtension = /\.[a-zA-Z0-9]{1,12}$/.test(trimmed.split("/").pop() || "");
  if (!hasExtension) {
    if (isRoot) {
      candidates.push("/index.html");
    } else if (trimmed.endsWith("/")) {
      candidates.push(`${trimmed}index.html`);
    } else {
      candidates.push(`${trimmed}/`);
      candidates.push(`${trimmed}/index.html`);
    }
  }

  return Array.from(new Set(candidates));
}

export async function fetchProtectedPath(
  env: ProtectedDocsEnv,
  path: string,
  options?: {
    accept?: string;
    userAgent?: string;
    requestUrl?: string;
  }
): Promise<{ target: URL; response: Response; usedPath: string }> {
  const baseRequestUrl = options?.requestUrl || env.PUBLIC_BASE_URL;
  if (!baseRequestUrl) {
    throw new Error("PUBLIC_BASE_URL or requestUrl is required");
  }

  const candidates = buildAssetPathCandidates(path);
  let lastResult: { target: URL; response: Response; usedPath: string } | null = null;

  for (const candidate of candidates) {
    const target = resolveTarget(candidate, env.PUBLIC_BASE_URL, baseRequestUrl);
    const request = new Request(target.toString(), {
      method: "GET",
      headers: {
        "User-Agent": options?.userAgent ?? DEFAULT_USER_AGENT,
        Accept:
          options?.accept ?? "text/html,application/xhtml+xml,text/plain,*/*;q=0.1",
      },
      redirect: "follow",
    });

    const response = env.ASSETS?.fetch
      ? await env.ASSETS.fetch(request)
      : await fetch(request);

    const current = { target, response, usedPath: candidate };
    if (response.status !== 404) {
      return current;
    }
    lastResult = current;
  }

  if (lastResult) return lastResult;
  throw new Error("Failed to fetch protected path");
}

export function extractTitle(html: string): string | null {
  const match = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  if (!match) return null;
  return decodeEntities(match[1].trim());
}

export function htmlToText(html: string): string {
  let normalized = html
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/<style[\s\S]*?<\/style>/gi, "");

  normalized = normalized
    .replace(/<\/(p|div|li|h1|h2|h3|h4|h5|h6|br|tr|table)>/gi, "\n")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<[^>]+>/g, "");

  return decodeEntities(normalized)
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function truncate(value: string, maxChars: number): string {
  if (value.length <= maxChars) return value;
  return `${value.slice(0, maxChars)}\n\n[TRUNCATED]`;
}

export function decodeEntities(input: string): string {
  return input
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#x([0-9a-fA-F]+);/g, (_, n: string) =>
      String.fromCharCode(parseInt(n, 16))
    )
    .replace(/&#([0-9]+);/g, (_, n: string) => String.fromCharCode(Number(n)));
}
