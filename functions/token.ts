import {
  mintAccessToken,
  mintRefreshToken,
  oauthCorsHeaders,
  oauthErrorResponse,
  OAuthRouteError,
  parseAuthorizationCode,
  parseBodyForm,
  parseRefreshToken,
  tokenResponse,
  validateRedirectUri,
  verifyCodeChallenge,
  withOauthCors,
} from "./_lib/mcp-oauth";
import type { ProtectedDocsEnv } from "./_lib/protected-docs";

export const onRequestOptions: PagesFunction<ProtectedDocsEnv> = async ({ request }) => {
  return new Response(null, { status: 204, headers: oauthCorsHeaders(request) });
};

export const onRequestPost: PagesFunction<ProtectedDocsEnv> = async ({ request, env }) => {
  try {
    const form = await parseBodyForm(request);
    const grantType = (form.get("grant_type") || "").trim();

    if (grantType === "authorization_code") {
      return withOauthCors(await exchangeAuthorizationCode(form, request, env), request);
    }

    if (grantType === "refresh_token") {
      return withOauthCors(await exchangeRefreshToken(form, request, env), request);
    }

    throw new OAuthRouteError(
      "unsupported_grant_type",
      "Supported grant types are authorization_code and refresh_token",
      400
    );
  } catch (error) {
    if (error instanceof OAuthRouteError) {
      return withOauthCors(oauthErrorResponse(error), request);
    }
    const fallback = new OAuthRouteError("server_error", "Unexpected token error", 500);
    return withOauthCors(oauthErrorResponse(fallback), request);
  }
};

async function exchangeAuthorizationCode(
  form: URLSearchParams,
  request: Request,
  env: ProtectedDocsEnv
): Promise<Response> {
  const code = (form.get("code") || "").trim();
  const clientId = (form.get("client_id") || "").trim();
  const redirectUriRaw = (form.get("redirect_uri") || "").trim();
  const codeVerifier = (form.get("code_verifier") || "").trim();

  if (!code) {
    throw new OAuthRouteError("invalid_request", "code is required", 400);
  }
  if (!clientId) {
    throw new OAuthRouteError("invalid_request", "client_id is required", 400);
  }
  if (!redirectUriRaw) {
    throw new OAuthRouteError("invalid_request", "redirect_uri is required", 400);
  }

  const redirectUri = validateRedirectUri(redirectUriRaw);
  const payload = await parseAuthorizationCode(code, env);
  if (payload.client_id !== clientId) {
    throw new OAuthRouteError("invalid_grant", "client_id does not match code", 400);
  }
  if (payload.redirect_uri !== redirectUri) {
    throw new OAuthRouteError("invalid_grant", "redirect_uri does not match code", 400);
  }

  const pkceValid = await verifyCodeChallenge(
    payload.code_challenge_method,
    codeVerifier || undefined,
    payload.code_challenge
  );
  if (!pkceValid) {
    throw new OAuthRouteError("invalid_grant", "PKCE verification failed", 400);
  }

  const access = await mintAccessToken(env, request, {
    sub: payload.sub,
    email: payload.email,
    name: payload.name,
    scope: payload.scope,
  });
  const refresh = await mintRefreshToken(env, request, {
    sub: payload.sub,
    email: payload.email,
    name: payload.name,
    scope: payload.scope,
  });

  return tokenResponse({
    access_token: access.token,
    token_type: "Bearer",
    expires_in: access.expiresIn,
    refresh_token: refresh,
    scope: payload.scope,
  });
}

async function exchangeRefreshToken(
  form: URLSearchParams,
  request: Request,
  env: ProtectedDocsEnv
): Promise<Response> {
  const refreshToken = (form.get("refresh_token") || "").trim();
  if (!refreshToken) {
    throw new OAuthRouteError("invalid_request", "refresh_token is required", 400);
  }

  const payload = await parseRefreshToken(refreshToken, env);
  const access = await mintAccessToken(env, request, {
    sub: payload.sub,
    email: payload.email,
    name: payload.name,
    scope: payload.scope,
  });
  const nextRefresh = await mintRefreshToken(env, request, {
    sub: payload.sub,
    email: payload.email,
    name: payload.name,
    scope: payload.scope,
  });

  return tokenResponse({
    access_token: access.token,
    token_type: "Bearer",
    expires_in: access.expiresIn,
    refresh_token: nextRefresh,
    scope: payload.scope,
  });
}
