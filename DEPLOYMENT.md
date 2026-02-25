# Deployment (Docker + Traefik)

Deployment assets are organized by environment under `infra/deploy/`.

## Environment layout

- `infra/deploy/dev`: local/dev deployment with bundled Keycloak + MCP + docs behind Traefik.
- `infra/deploy/prod`: production deployment with MCP + docs behind Traefik, expecting an external IdP.
- `infra/deploy/auth-integration`: isolated no-port integration test stack for `external-jwt` + Keycloak confidential client.

## Shared files

- `infra/deploy/shared/Dockerfile`
- `infra/deploy/shared/keycloak/realm-vb-kb.json`

## Prerequisites

- Docker + Docker Compose on host.
- Traefik running with an external Docker network named `traefik-public`.
- Checked-out repo path on host containing `.git/` and `data/`.

## Dev stack (`infra/deploy/dev`)

This stack includes:

- `kb-mcp` (`/mcp`, OAuth metadata endpoints)
- `kb-docs` (`/`, protected with BasicAuth middleware)
- `keycloak` (OIDC issuer for external JWT validation)

### Configure

Edit `infra/deploy/dev/.env`:

- Set `VB_KB_HOST` and `VB_KC_HOST`.
- Set `VB_KB_REPO_PATH`.
- Set `DOCS_BASIC_AUTH_USERS` (htpasswd hash; escape `$` as `$$`).
- Keep Keycloak realm/client values aligned with `infra/deploy/shared/keycloak/realm-vb-kb.json`.

### Run

```bash
cd infra/deploy/dev
docker compose -f docker-compose.traefik.yml up -d --build
```

Optional local overrides can live in `infra/deploy/dev/.env.local` (gitignored).

## Auth integration suite (`infra/deploy/auth-integration`)

Run:

```bash
just test-auth-integration
```

This suite:

- spins up Keycloak + MCP on an internal Docker network only
- does not publish host ports
- obtains a token with Keycloak confidential `client_credentials`
- validates MCP rejects missing/invalid bearer tokens and accepts the Keycloak token in `external-jwt` mode

The runner uses a unique Compose project name per invocation, so concurrent runs are safe.

## Prod stack (`infra/deploy/prod`)

This stack includes:

- `kb-mcp`
- `kb-docs`

It does **not** run Keycloak. Configure `external-jwt` to point at your external issuer.

### Configure

Edit `infra/deploy/prod/.env`:

- `VB_KB_HOST`
- `VB_KB_REPO_PATH`
- `KB_MCP_EXTERNAL_AUTHORIZATION_SERVERS`
- exactly one of `KB_MCP_EXTERNAL_JWT_JWKS_URI` or `KB_MCP_EXTERNAL_JWT_PUBLIC_KEY`
- optional `KB_MCP_EXTERNAL_JWT_ISSUER`, `KB_MCP_EXTERNAL_JWT_AUDIENCE`, and scope vars
- `DOCS_BASIC_AUTH_USERS`

### Run

```bash
cd infra/deploy/prod
docker compose -f docker-compose.traefik.yml up -d --build
```

Optional local overrides can live in `infra/deploy/prod/.env.local` (gitignored).

## OAuth mode notes

- Server default for HTTP transports is still in-memory OAuth unless overridden.
- Dev/prod deploy envs are preconfigured for `KB_MCP_OAUTH_MODE=external-jwt`.
- In `external-jwt`, MCP does not expose `/authorize`, `/token`, or `/register`; it validates bearer JWTs locally using JWKS/public key config.

## Verification

For deployed host:

1. Docs: `https://<host>/` prompts for docs auth.
2. MCP metadata:
   - in-memory mode: `/.well-known/oauth-authorization-server`
   - external-jwt mode: `/.well-known/oauth-protected-resource/mcp`
3. MCP challenge: `curl -i https://<host>/mcp` returns `401` before bearer token.
