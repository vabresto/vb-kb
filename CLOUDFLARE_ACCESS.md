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
