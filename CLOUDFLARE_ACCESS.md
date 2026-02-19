# Cloudflare Access Setup

This repo builds a static MkDocs site from `data/`. The site is read-only by design.

## 1. Deploy the static site

Use Cloudflare Pages:

- Build command: `pip install mkdocs-material PyYAML && mkdocs build --strict`
- Build output directory: `site`
- Root directory: `/`
- Environment variable: `PYTHON_VERSION=3.13` (or any `>=3.11`)

`mkdocs build` triggers `mkdocs_hooks.py`, which regenerates `site_docs/` from `data/` before build output is created.

## 2. Protect access

Create a Cloudflare Zero Trust Access application for your site hostnames.

Recommended hostnames to include:

- `kb.yourdomain.com` (custom domain)
- `<your-project>.pages.dev` (fallback Pages domain)

Policy baseline:

- Include: specific allowlisted emails (or `Emails ending in @yourdomain.com`)
- Action: Allow
- Session duration: choose per app or per policy

Cloudflare Access is deny-by-default once an app is configured with policy.

## 3. Shared access model

Start simple:

- One-time PIN (email OTP) identity provider
- Allowlisted email addresses for each collaborator

Scale later:

- Switch include rules to SSO groups or domain-based rules
- Keep a separate allow policy for external collaborators

## 4. Prevent public fallback exposure

For Pages deployments:

- Ensure Access policy also covers the `*.pages.dev` hostname.
- Do not assume only the custom domain is protected.

For Worker deployments (if used later instead of Pages):

- set `workers_dev = false`
- set `preview_urls = false`

## 5. Verify lock-down

Test in an incognito session:

1. Visit the custom domain and confirm Access prompt appears.
2. Visit the `pages.dev` URL and confirm Access prompt appears there too.
3. Confirm successful login grants only intended users.

## 6. Enable GPT APIs + MCP (single service)

This repo now exposes both Action-style APIs and MCP from the same Pages Functions service, reading directly from bundled MkDocs assets (`ASSETS.fetch`) with no internal round-trip to the public hostname.

Implemented endpoints:

- `GET /api/fetch?path=/...&format=text|html&maxChars=...`
- `GET /api/search?q=...&limit=...&maxChars=...&pathPrefix=/...`
- `POST /mcp` (JSON-RPC MCP transport with tools `search` and `fetch`)
- `GET /authorize` (OAuth authorization entrypoint)
- `GET /callback` (OAuth callback from Access)
- `POST /token` (OAuth token exchange)
- `POST /register` (OAuth client registration)
- `GET /.well-known/oauth-protected-resource`
- `GET /.well-known/oauth-authorization-server`

### Required Access policy scope

Keep `/api/*` policy-protected as before if you still use Custom GPT Actions with service tokens.

For MCP OAuth routes:

- `/mcp`
- `/register`
- `/token`
- `/.well-known/*`

These must be reachable by ChatGPT over the public internet. Access control for MCP is enforced by OAuth bearer tokens issued only after Access login in `/authorize` -> `/callback`.

For browser login flow routes:

- `/authorize`
- `/callback`

These should remain reachable for your interactive Access login flow.

### Pages environment variables

Set these in Cloudflare Pages (Production + Preview as needed):

- `PUBLIC_BASE_URL=https://<your-host>` (optional, defaults to request origin)
- `ALLOWED_PREFIXES=/` (or narrow list like `/,/person,/org,/people,/orgs,/search`)
- optional: `SEARCH_INDEX_PATH=/search/search_index.json`
- optional: `MCP_SERVER_NAME=VB Knowledge Base`
- optional: `MCP_SERVER_VERSION=1.0.0`

For OAuth discovery metadata (Access for SaaS OIDC):

- `OAUTH_SIGNING_KEY=<random-long-secret-for-signing-client-and-token-jwts>`
- optional: `OAUTH_ISSUER=https://<your-host>`
- optional: `ACCESS_OIDC_ISSUER=<Access issuer URL for id_token iss validation>`
- `ACCESS_CLIENT_ID=<Access for SaaS OIDC client id>`
- `ACCESS_CLIENT_SECRET=<Access for SaaS OIDC client secret>`
- `ACCESS_AUTHORIZATION_URL=<Access authorization endpoint>`
- `ACCESS_TOKEN_URL=<Access token endpoint>`
- `ACCESS_JWKS_URL=<Access JWKS endpoint>`

### Access OIDC redirect URL

When configuring the Access-for-SaaS OIDC app, set redirect URL to:

- `https://<your-host>/callback`

Example:

- `https://dex.victorbrestoiu.me/callback`

### Manual API tests

```bash
curl -sS \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  "https://dex.victorbrestoiu.me/api/fetch?path=/&format=text"
```

```bash
curl -sS \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  "https://dex.victorbrestoiu.me/api/search?q=webflow&limit=5"
```

### Manual MCP test

First check discovery docs:

```bash
curl -sS \
  "https://dex.victorbrestoiu.me/.well-known/oauth-authorization-server"
```

Then use OAuth (`/register` -> `/authorize` -> `/callback` -> `/token`) to obtain a bearer token and call MCP:

```bash
curl -sS \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <mcp_access_token>" \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/list"}' \
  "https://dex.victorbrestoiu.me/mcp"
```

### Custom GPT Action OpenAPI

Use `openapi/custom-gpt-action.yaml` as the Action spec with server URL `https://dex.victorbrestoiu.me`.
