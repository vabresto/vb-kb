import {
  mintClientId,
  oauthCorsHeaders,
  oauthErrorResponse,
  OAuthRouteError,
  parseBodyForm,
  tokenResponse,
  validateRedirectUri,
  withOauthCors,
} from "./_lib/mcp-oauth";
import type { ProtectedDocsEnv } from "./_lib/protected-docs";

export const onRequestOptions: PagesFunction<ProtectedDocsEnv> = async ({ request }) => {
  return new Response(null, { status: 204, headers: oauthCorsHeaders(request) });
};

export const onRequestPost: PagesFunction<ProtectedDocsEnv> = async ({ request, env }) => {
  try {
    const requestForJson = request.clone();
    const form = await parseBodyForm(request);
    const jsonBody = await maybeParseJson(requestForJson);
    const redirectUris = collectRedirectUris(form, jsonBody);
    if (!redirectUris.length) {
      throw new OAuthRouteError("invalid_client_metadata", "redirect_uris is required", 400);
    }

    const validatedUris = redirectUris.map((uri) => validateRedirectUri(uri));
    const dedupedUris = Array.from(new Set(validatedUris));

    const clientName = pickFirstNonEmpty([
      form.get("client_name"),
      pickJsonString(jsonBody, "client_name"),
    ]);

    const clientId = await mintClientId(env, request, {
      redirectUris: dedupedUris,
      clientName: clientName || undefined,
    });

    return withOauthCors(
      tokenResponse({
        client_id: clientId,
        client_id_issued_at: Math.floor(Date.now() / 1000),
        client_name: clientName || undefined,
        grant_types: ["authorization_code", "refresh_token"],
        response_types: ["code"],
        token_endpoint_auth_method: "none",
        redirect_uris: dedupedUris,
      }),
      request
    );
  } catch (error) {
    if (error instanceof OAuthRouteError) {
      return withOauthCors(oauthErrorResponse(error), request);
    }
    const fallback = new OAuthRouteError("server_error", "Unexpected registration error", 500);
    return withOauthCors(oauthErrorResponse(fallback), request);
  }
};

function collectRedirectUris(
  form: URLSearchParams,
  jsonBody: Record<string, unknown> | null
): string[] {
  const collected: string[] = [];

  for (const value of form.getAll("redirect_uri")) {
    if (typeof value === "string" && value.trim()) collected.push(value.trim());
  }

  const jsonUris = pickJsonArray(jsonBody, "redirect_uris");
  for (const value of jsonUris) {
    if (typeof value === "string" && value.trim()) collected.push(value.trim());
  }

  const formRedirectUris = form.get("redirect_uris");
  if (formRedirectUris && formRedirectUris.trim().startsWith("[")) {
    try {
      const parsed = JSON.parse(formRedirectUris) as unknown;
      if (Array.isArray(parsed)) {
        for (const value of parsed) {
          if (typeof value === "string" && value.trim()) collected.push(value.trim());
        }
      }
    } catch {
      // ignore malformed JSON and rely on other fields
    }
  }

  return collected;
}

async function maybeParseJson(request: Request): Promise<Record<string, unknown> | null> {
  const contentType = (request.headers.get("content-type") || "").toLowerCase();
  if (!contentType.includes("application/json")) return null;
  try {
    const body = await request.json();
    if (!body || typeof body !== "object") return null;
    return body as Record<string, unknown>;
  } catch {
    return null;
  }
}

function pickJsonArray(
  body: Record<string, unknown> | null,
  key: string
): unknown[] {
  if (!body) return [];
  const value = body[key];
  if (!Array.isArray(value)) return [];
  return value;
}

function pickJsonString(
  body: Record<string, unknown> | null,
  key: string
): string | null {
  if (!body) return null;
  const value = body[key];
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed || null;
}

function pickFirstNonEmpty(values: Array<string | null>): string | null {
  for (const value of values) {
    if (!value) continue;
    const trimmed = value.trim();
    if (trimmed) return trimmed;
  }
  return null;
}
