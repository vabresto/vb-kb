# KB Validation Tools

## Setup

Run once per clone:

```bash
uvx prek install --install-hooks
```

This installs `.git/hooks/pre-commit` and runs hooks from `.pre-commit-config.yaml`.

## Hooks

1. `tools/check_entity_links.py`
- Scope: staged markdown files matching `data/person/*.md`, `data/org/*.md`, and `data/notes/reflections/long-form/*.md`.
- Rule: first mention of a known KB person/org in page body must be a local markdown link to that entity file.
- Ignores frontmatter, headings, fenced code blocks, and footnote/link-definition lines.

2. `tools/check_new_urls.py`
- Scope: staged diff only.
- Rule: only URLs introduced in added lines are checked.
- Pass criteria: request follows redirects and final HTTP status is `2xx`.
- Excludes placeholder/local hosts such as `localhost`, `*.localhost`, `*.example`, `*.invalid`, and `*.test`.

## Manual runs

```bash
uv run python tools/check_entity_links.py data/person/victor-brestoiu.md
uv run python tools/check_entity_links.py data/notes/reflections/long-form/2026-02-19-post-mortem-inbot.md
uv run python tools/check_new_urls.py
uvx prek run kb-entity-first-mention-links --files data/person/victor-brestoiu.md
uvx prek run kb-entity-first-mention-links --files data/notes/reflections/long-form/2026-02-19-post-mortem-inbot.md
uvx prek run kb-newly-added-urls-reachable --all-files
uv run kb validate --pretty
uv run kb validate --changed --pretty
uv run kb migrate-v2 --output-dir data-new
```

## Local website preview

MkDocs is wired to generate view-only site content from `data/` into `site_docs/` on every build.

```bash
uv run mkdocs serve
```

Open the local URL printed by MkDocs (usually `http://127.0.0.1:8000`).

For a production build:

```bash
uv run mkdocs build
```

Generated output is written to `site/`.

Presentation overrides for the generated site live in `site_assets/`.
