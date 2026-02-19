import {
  buildAccessAuthorizationUrl,
  ensureRedirectAllowed,
  mintUpstreamState,
  normalizeScope,
  oauthCorsHeaders,
  oauthErrorResponse,
  OAuthRouteError,
  parseClientId,
  requireAccessOAuthConfig,
  resolveOAuthEndpoints,
  validateRedirectUri,
  withOauthCors,
} from "./_lib/mcp-oauth";
import type { ProtectedDocsEnv } from "./_lib/protected-docs";

export const onRequestOptions: PagesFunction<ProtectedDocsEnv> = async ({ request }) => {
  return new Response(null, { status: 204, headers: oauthCorsHeaders(request) });
};

export const onRequestGet: PagesFunction<ProtectedDocsEnv> = async ({ request, env }) => {
  try {
    const url = new URL(request.url);
    const responseType = (url.searchParams.get("response_type") || "").trim();
    const clientId = (url.searchParams.get("client_id") || "").trim();
    const redirectUriRaw = (url.searchParams.get("redirect_uri") || "").trim();
    const scope = normalizeScope(url.searchParams.get("scope"));
    const state = (url.searchParams.get("state") || "").trim();
    const codeChallenge = (url.searchParams.get("code_challenge") || "").trim();
    const codeChallengeMethod = (
      url.searchParams.get("code_challenge_method") || "S256"
    ).trim();

    if (responseType !== "code") {
      throw new OAuthRouteError(
        "unsupported_response_type",
        "Only response_type=code is supported",
        400
      );
    }
    if (!clientId) {
      throw new OAuthRouteError("invalid_request", "client_id is required", 400);
    }

    const redirectUri = validateRedirectUri(redirectUriRaw);
    const client = await parseClientId(clientId, env);
    ensureRedirectAllowed(redirectUri, client.redirect_uris);

    if (!state) {
      throw new OAuthRouteError("invalid_request", "state is required", 400);
    }

    if (codeChallenge && codeChallengeMethod !== "S256") {
      throw new OAuthRouteError(
        "invalid_request",
        "Only code_challenge_method=S256 is supported",
        400
      );
    }

    const accessConfig = requireAccessOAuthConfig(env);
    const endpoints = resolveOAuthEndpoints(env, request);
    const upstreamState = await mintUpstreamState(env, request, {
      oauth_client_id: clientId,
      oauth_redirect_uri: redirectUri,
      oauth_state: state,
      oauth_scope: scope,
      oauth_code_challenge: codeChallenge || undefined,
      oauth_code_challenge_method: codeChallenge ? "S256" : undefined,
    });

    const destination = buildAccessAuthorizationUrl({
      authorizationUrl: accessConfig.authorizationUrl,
      clientId: accessConfig.clientId,
      callbackUrl: endpoints.callbackEndpoint,
      scope: "openid email profile",
      state: upstreamState,
    });

    return withOauthCors(Response.redirect(destination, 302), request);
  } catch (error) {
    if (error instanceof OAuthRouteError) {
      return withOauthCors(oauthErrorResponse(error), request);
    }
    const fallback = new OAuthRouteError("server_error", "Unexpected authorization error", 500);
    return withOauthCors(oauthErrorResponse(fallback), request);
  }
};
