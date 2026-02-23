# KB Validation Tools

## Setup

Run once per clone:

```bash
uvx prek install --install-hooks
```

This installs `.git/hooks/pre-commit` and runs hooks from `.pre-commit-config.yaml`.

## Hooks

1. `tools/check_entity_links.py`
- Scope: staged markdown files matching canonical v2 files in `data/person/.../person@.../index.md`, `data/org/.../org@.../index.md`, and long-form note records `data/note/.../note@reflections-long-form-.../index.md`.
- Rule: first mention of a known KB person/org in page body must be a local markdown link to that entity file.
- Ignores frontmatter, headings, fenced code blocks, and footnote/link-definition lines.

2. `tools/check_new_urls.py`
- Scope: staged diff only.
- Rule: only URLs introduced in added lines are checked.
- Pass criteria: request follows redirects and final HTTP status is `2xx`.
- Excludes placeholder/local hosts such as `localhost`, `*.localhost`, `*.example`, `*.invalid`, and `*.test`.

## Manual runs

```bash
uv run python tools/check_entity_links.py data/person/vi/person@victor-brestoiu/index.md
uv run python tools/check_entity_links.py data/note/re/note@reflections-long-form-2026-02-19-post-mortem-inbot/index.md
uv run python tools/check_new_urls.py
uv run kb validate --changed --pretty
uvx prek run kb-entity-first-mention-links --files data/person/vi/person@victor-brestoiu/index.md
uvx prek run kb-entity-first-mention-links --files data/note/re/note@reflections-long-form-2026-02-19-post-mortem-inbot/index.md
uvx prek run kb-newly-added-urls-reachable --all-files
uv run kb validate --pretty
uv run kb validate --changed --pretty
uv run kb derive-employment-edges
uv run kb sync-edges
uv run kb mcp-server --transport stdio
```

## Local website preview

MkDocs is wired to generate view-only site content from `data/` (including `data/note/`) into `.build/docs/` on every build.

```bash
just site
```

Open the local URL printed by MkDocs (usually `http://127.0.0.1:8000`).

For a production build:

```bash
just site-build
```

Generated output is written to `.build/site/`.

Presentation overrides for the generated site live in `tools/site_assets/`.
