import {
  exchangeAccessCodeForIdentity,
  mintAuthorizationCode,
  oauthCorsHeaders,
  oauthErrorResponse,
  OAuthRouteError,
  parseUpstreamState,
  withOauthCors,
} from "./_lib/mcp-oauth";
import type { ProtectedDocsEnv } from "./_lib/protected-docs";

export const onRequestOptions: PagesFunction<ProtectedDocsEnv> = async ({ request }) => {
  return new Response(null, { status: 204, headers: oauthCorsHeaders(request) });
};

export const onRequestGet: PagesFunction<ProtectedDocsEnv> = async ({ request, env }) => {
  let statePayload: Awaited<ReturnType<typeof parseUpstreamState>> | null = null;
  try {
    const url = new URL(request.url);
    const code = (url.searchParams.get("code") || "").trim();
    const state = (url.searchParams.get("state") || "").trim();
    const upstreamError = (url.searchParams.get("error") || "").trim();
    const upstreamErrorDescription = (
      url.searchParams.get("error_description") || ""
    ).trim();

    if (!state) {
      throw new OAuthRouteError("invalid_request", "Missing OAuth state", 400);
    }
    statePayload = await parseUpstreamState(state, env, request);

    if (upstreamError) {
      return withOauthCors(
        redirectWithError(
          statePayload.oauth_redirect_uri,
          upstreamError,
          upstreamErrorDescription || "Authentication was denied by Access",
          statePayload.oauth_state
        ),
        request
      );
    }

    if (!code) {
      throw new OAuthRouteError("invalid_request", "Missing authorization code from Access", 400);
    }

    const identity = await exchangeAccessCodeForIdentity(env, request, code);
    const mcpCode = await mintAuthorizationCode(env, request, {
      client_id: statePayload.oauth_client_id,
      redirect_uri: statePayload.oauth_redirect_uri,
      scope: statePayload.oauth_scope,
      sub: identity.sub,
      email: identity.email,
      name: identity.name,
      code_challenge: statePayload.oauth_code_challenge,
      code_challenge_method: statePayload.oauth_code_challenge_method,
    });

    const redirect = new URL(statePayload.oauth_redirect_uri);
    redirect.searchParams.set("code", mcpCode);
    if (statePayload.oauth_state) {
      redirect.searchParams.set("state", statePayload.oauth_state);
    }
    return withOauthCors(Response.redirect(redirect.toString(), 302), request);
  } catch (error) {
    if (statePayload) {
      const oauthError = error instanceof OAuthRouteError
        ? error
        : new OAuthRouteError("server_error", "Unexpected callback error", 500);
      return withOauthCors(
        redirectWithError(
          statePayload.oauth_redirect_uri,
          oauthError.code,
          oauthError.message,
          statePayload.oauth_state
        ),
        request
      );
    }

    if (error instanceof OAuthRouteError) {
      return withOauthCors(oauthErrorResponse(error), request);
    }
    const fallback = new OAuthRouteError("server_error", "Unexpected callback error", 500);
    return withOauthCors(oauthErrorResponse(fallback), request);
  }
};

function redirectWithError(
  redirectUri: string,
  error: string,
  errorDescription: string,
  state: string
): Response {
  const destination = new URL(redirectUri);
  destination.searchParams.set("error", error);
  destination.searchParams.set("error_description", errorDescription);
  if (state) destination.searchParams.set("state", state);
  return Response.redirect(destination.toString(), 302);
}
