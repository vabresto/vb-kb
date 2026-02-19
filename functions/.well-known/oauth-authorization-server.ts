import { json, type ProtectedDocsEnv } from "../_lib/protected-docs";
import { resolveOAuthEndpoints } from "../_lib/mcp-oauth";

export const onRequestGet: PagesFunction<ProtectedDocsEnv> = async ({
  request,
  env,
}) => {
  const endpoints = resolveOAuthEndpoints(env, request);

  return json({
    issuer: endpoints.issuer,
    authorization_endpoint: endpoints.authorizeEndpoint,
    token_endpoint: endpoints.tokenEndpoint,
    registration_endpoint: endpoints.registrationEndpoint,
    response_types_supported: ["code"],
    grant_types_supported: ["authorization_code", "refresh_token"],
    token_endpoint_auth_methods_supported: ["none"],
    code_challenge_methods_supported: ["S256"],
    scopes_supported: ["openid", "email", "profile"],
  });
};
