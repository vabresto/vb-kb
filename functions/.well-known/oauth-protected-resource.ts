import { json, type ProtectedDocsEnv } from "../_lib/protected-docs";
import { resolveOAuthEndpoints } from "../_lib/mcp-oauth";

export const onRequestGet: PagesFunction<ProtectedDocsEnv> = async ({
  request,
  env
}) => {
  const endpoints = resolveOAuthEndpoints(env, request);

  return json({
    resource: endpoints.mcpEndpoint,
    authorization_servers: [endpoints.issuer],
    bearer_methods_supported: ["header"],
    scopes_supported: ["openid", "email", "profile"],
  });
};
