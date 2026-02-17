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

## 6. Enable Custom GPT Actions (still fully protected)

You can keep `https://dex.victorbrestoiu.me` fully behind Access and expose GPT-friendly endpoints via Cloudflare Pages Functions.

Endpoints implemented in this repo:

- `GET /api/fetch?path=/...&format=text|html&maxChars=...`
- `GET /api/search?q=...&limit=...&maxChars=...&pathPrefix=/...`

These endpoints do **server-side** fetches to the same protected host using a service token. This avoids redirect/header-loss issues from Action clients.

### Required Access policy scope

Create or reuse a Service Token and allow it to access:

- `/api/*` (for GPT Action calls)
- any content paths fetched by the function (usually easiest is `/*`)

If only `/api/*` is allowed, the function can be called but its internal fetch to protected pages will be blocked.

### Pages environment variables

Set these in Cloudflare Pages (Production + Preview as needed):

- `PUBLIC_BASE_URL=https://dex.victorbrestoiu.me`
- `CF_ACCESS_CLIENT_ID=<service-token-client-id>`
- `CF_ACCESS_CLIENT_SECRET=<service-token-client-secret>`
- `ALLOWED_PREFIXES=/` (or narrow list like `/,/people,/orgs,/search`)
- optional: `SEARCH_INDEX_PATH=/search/search_index.json`

### Manual API test

Use your service token headers:

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

Expected result: JSON payload with `content` (fetch) and ranked `results` (search).

### Custom GPT Action OpenAPI

Use `openapi/custom-gpt-action.yaml` as the Action spec with server URL `https://dex.victorbrestoiu.me`.
