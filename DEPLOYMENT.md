# Deployment (Docker + Traefik)

This is a simple production shape for hosting both:

- MCP write server at `https://<host>/mcp`
- Human-friendly docs site at `https://<host>/` (password protected)

The examples below use:

- One Docker image for both services
- Traefik for TLS + routing
- Built-in MCP OAuth provider from `kb/mcp_server.py`
- Traefik BasicAuth for docs (with optional OIDC alternatives)

## Prerequisites

- Docker + Docker Compose on the host.
- Traefik already running with an external Docker network (called `traefik-public` in examples).
- A checked-out git repo on host (for example `/srv/vb-kb`) including `.git/` and `data/`.
  - MCP writes use git transactions and require a real repo.

## Files in this repo

- `deploy/Dockerfile`
- `deploy/docker-compose.traefik.yml`
- `deploy/.env.example`

## Quick Start

1. Copy env template:

```bash
cp deploy/.env.example deploy/.env
```

2. Edit `deploy/.env`:
- Set `VB_KB_HOST` (for example `kb.example.com`).
- Set `VB_KB_REPO_PATH` (for example `/srv/vb-kb`).
- Set `DOCS_BASIC_AUTH_USERS` to an htpasswd hash.

Generate a hash example:

```bash
htpasswd -nB admin
```

Use output as `DOCS_BASIC_AUTH_USERS` (escape `$` as `$$` in `.env`).

3. Start:

```bash
docker compose -f deploy/docker-compose.traefik.yml --env-file deploy/.env up -d --build
```

## Route Layout

`kb-mcp` handles:

- `/mcp`
- `/authorize`
- `/token`
- `/register`
- `/.well-known/oauth-authorization-server...`
- `/.well-known/oauth-protected-resource...`

`kb-docs` handles:

- everything else on the same host (`/`)

Traefik router priorities in the compose file ensure MCP paths win over docs.

## OAuth Notes

### Built-in OAuth provider (default in this repo)

Configured via env vars:

- `KB_MCP_OAUTH_MODE` (`in-memory`, `external-jwt`, or `off`)
- `KB_MCP_OAUTH_BASE_URL` (public HTTPS origin)
- `KB_MCP_OAUTH_STATE_FILE` (persistent token/client state file)

With `in-memory` mode, the server validates access tokens locally against its state file, so there is no external auth-server round trip per MCP query.

Client credentials (`client_id`/`client_secret`) are not statically configured in env vars today.
They are created via OAuth Dynamic Client Registration (`/register`) and persisted in `KB_MCP_OAUTH_STATE_FILE`.

### External token validation mode (`external-jwt`)

Use this when tokens are issued by Keycloak/Nextcloud/another external IdP and this server should only validate bearer tokens.

Required env vars:

- `KB_MCP_OAUTH_MODE=external-jwt`
- `KB_MCP_EXTERNAL_AUTHORIZATION_SERVERS` (comma-separated issuer/auth server URLs advertised in resource metadata)
- Exactly one of:
  - `KB_MCP_EXTERNAL_JWT_JWKS_URI`
  - `KB_MCP_EXTERNAL_JWT_PUBLIC_KEY`

Optional env vars:

- `KB_MCP_EXTERNAL_JWT_ISSUER` (string or comma-separated list)
- `KB_MCP_EXTERNAL_JWT_AUDIENCE` (string or comma-separated list)
- `KB_MCP_EXTERNAL_JWT_ALGORITHM` (default `RS256`)
- `KB_MCP_EXTERNAL_REQUIRED_SCOPES` (required on every MCP request)
- `KB_MCP_EXTERNAL_SCOPES_SUPPORTED` (advertised only)

In `external-jwt` mode, this server does not expose `/authorize`, `/token`, or `/register`.
It exposes protected-resource metadata and validates incoming bearer JWTs locally (JWKS/public key), so no IdP round trip is needed per query.

### Where `client_id`/`client_secret` are configured

- Not at Traefik.
- In `in-memory` mode, MCP clients register themselves against `/register`.
- In `external-jwt` mode, this server does not issue client credentials; client registration/auth happens at your external IdP.

### Identity and scope propagation (current state)

Current in-repo OAuth setup is client-centric and does not expose end-user identity claims into tool handlers.
In `external-jwt` mode, required scopes can be enforced globally via `KB_MCP_EXTERNAL_REQUIRED_SCOPES`, but there is still no per-tool scope matrix by default.

If you need user-level identity + scope enforcement in the MCP tool layer, add a custom auth integration:

1. Validate JWT/introspection from your IdP (Keycloak/Nextcloud) in the MCP auth provider.
2. Map claims (`sub`, `email`, `groups`, `scope`) into request context.
3. Enforce tool-level scopes (for example read vs write) in server code.
4. Emit audit logs with resolved user identity.

### External Keycloak/Nextcloud (OIDC) considerations

Yes, this can work, but behavior depends on token validation mode:

- JWT validated locally by gateway/service: no IdP round trip per query.
- Opaque token introspection per request: does round trip to IdP.

For good UX, use short-lived access tokens plus refresh tokens (for example 5-15 min access token).

Minimal recommendation for this repo:

1. Run MCP with `KB_MCP_OAUTH_MODE=external-jwt`.
2. Set `KB_MCP_EXTERNAL_JWT_JWKS_URI` to your IdP JWKS endpoint.
3. Set `KB_MCP_EXTERNAL_AUTHORIZATION_SERVERS` and `KB_MCP_EXTERNAL_JWT_ISSUER` to your IdP issuer URL.
4. Set `KB_MCP_EXTERNAL_JWT_AUDIENCE` and `KB_MCP_EXTERNAL_REQUIRED_SCOPES` to match your MCP client registration.

## Docs Protection Options

### Option A: Traefik BasicAuth (included in compose example)

- Easiest path.
- Fully password protects docs.

### Option B: Cloudflare Access

- Keep docs private without exposing app-level auth logic.
- If you already run Cloudflare Access, this is still a good option.

### Option C: Self-hosted OIDC (oauth2-proxy + Keycloak/Nextcloud)

- Replace docs router middleware (`vb-kb-docs-auth`) with `forwardAuth` middleware to `oauth2-proxy`.
- `oauth2-proxy` handles OIDC login and session cookies; users do not re-auth on every page request.
- This secures docs access but does not by itself make Traefik an MCP OAuth authorization server.

## Verify

1. Docs:
- Open `https://<host>/`.
- Expect auth challenge before content.

2. MCP metadata:
- `in-memory` mode: `curl https://<host>/.well-known/oauth-authorization-server`
- `external-jwt` mode: `curl https://<host>/.well-known/oauth-protected-resource/mcp`

3. MCP handshake:
- `curl -i https://<host>/mcp`
- Expect `401` with OAuth `WWW-Authenticate` challenge before login.
