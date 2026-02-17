export interface ProtectedDocsEnv {
  PUBLIC_BASE_URL: string;
  CF_ACCESS_CLIENT_ID: string;
  CF_ACCESS_CLIENT_SECRET: string;
  ALLOWED_PREFIXES?: string;
  SEARCH_INDEX_PATH?: string;
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
  const missing: string[] = [];
  if (!env.PUBLIC_BASE_URL) missing.push("PUBLIC_BASE_URL");
  if (!env.CF_ACCESS_CLIENT_ID) missing.push("CF_ACCESS_CLIENT_ID");
  if (!env.CF_ACCESS_CLIENT_SECRET) missing.push("CF_ACCESS_CLIENT_SECRET");
  return missing;
}

export function validatePublicBaseUrl(rawBaseUrl: string): string | null {
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

export function resolveTarget(path: string, rawBaseUrl: string): URL {
  const base = parseBaseUrl(rawBaseUrl);
  const target = new URL(path, base);
  if (target.origin !== base.origin) {
    throw new Error("Cross-origin path is not allowed");
  }
  return target;
}

export async function fetchProtectedPath(
  env: ProtectedDocsEnv,
  path: string,
  options?: {
    accept?: string;
    userAgent?: string;
  }
): Promise<{ target: URL; response: Response }> {
  const target = resolveTarget(path, env.PUBLIC_BASE_URL);
  const response = await fetch(target.toString(), {
    method: "GET",
    headers: {
      "CF-Access-Client-Id": env.CF_ACCESS_CLIENT_ID,
      "CF-Access-Client-Secret": env.CF_ACCESS_CLIENT_SECRET,
      "User-Agent": options?.userAgent ?? DEFAULT_USER_AGENT,
      Accept:
        options?.accept ?? "text/html,application/xhtml+xml,text/plain,*/*;q=0.1",
    },
    redirect: "follow",
  });
  return { target, response };
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
