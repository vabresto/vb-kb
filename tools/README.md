# KB Validation Tools

## Setup

Run once per clone:

```bash
uvx prek install --install-hooks
```

This installs `.git/hooks/pre-commit` and runs hooks from `.pre-commit-config.yaml`.

## Hooks

1. `tools/check_entity_links.py`
- Scope: staged markdown files matching `data/person/*.md` and `data/org/*.md`.
- Rule: first mention of a known KB person/org in page body must be a local markdown link to that entity file.
- Ignores frontmatter, headings, fenced code blocks, and footnote/link-definition lines.

2. `tools/check_new_urls.py`
- Scope: staged diff only.
- Rule: only URLs introduced in added lines are checked.
- Pass criteria: request follows redirects and final HTTP status is `2xx`.

## Manual runs

```bash
uv run python tools/check_entity_links.py data/person/victor-brestoiu.md
uv run python tools/check_new_urls.py
uvx prek run kb-entity-first-mention-links --files data/person/victor-brestoiu.md
uvx prek run kb-newly-added-urls-reachable --all-files
```
