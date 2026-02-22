# KB v2 Architecture Decisions

Status: Accepted  
Date: 2026-02-22

## Why This Exists

This document captures the current KB v2 decisions from design discussions.
It is the baseline for migration and implementation work.

## Goals

- Keep filesystem-native identity and human-readable records.
- Support structured relationship data across entities.
- Keep one canonical source of truth and git-based auditability.
- Keep querying simple for a small dataset.
- Keep the door open for MCP-based editing.
- Keep MCP runtime and mutation logic in Python.

## Core Decisions

1. Canonical data stays file-based under `data/`, committed to git.
2. Entities move from single files to folders with `<type>@<id>` names.
2a. Notes move from flat markdown paths to folder records with `note@<id>` names.
3. Subfolder sharding is allowed for scale and path hygiene (for example `data/person/al/person@alice-formwalt/`).
4. Canonical edges live in one place: `data/edge/.../edge@<id>.json`.
5. Each endpoint entity has a relative symlink backlink in `edges/` to the canonical edge file.
6. Structured table-like data is stored as JSONL, not CSV/YAML.
7. SQLite is not required for now; `rg` and DuckDB over JSONL are sufficient at current scale.
8. Any derived views must be reproducible from canonical files and never become a second source of truth.
9. Initial concurrency model is single-writer with retry-on-busy behavior.
10. Structured table-like data all conforms to a global schema, maintained via pydantic models and validated after every edit.
11. The supported MCP server implementation is Python FastMCP (`kb/mcp_server.py`); TypeScript Functions are deprecated.
12. Interactive MCP auth targets OAuth2 Authorization Code + PKCE (S256); fixed API keys are not the target auth path.

## Naming and Layout

Chosen entity folder naming:

- `person@<id>`
- `org@<id>`
- `edge@<id>`

Rationale:

- Keeps explicit type in path.
- Leaves room for subfolder sharding without changing entity identity.
- Avoids ambiguity with plain slugs.

Proposed layout:

```text
data/
  person/
    al/
      person@alice-formwalt/
        index.md
        edges/
          edge@e-20260219-alice-introduced-by-oliver.json -> ../../../edge/e2/edge@e-20260219-alice-introduced-by-oliver.json
        employment-history.jsonl
        looking-for.jsonl
        changelog.jsonl
        images/
          alice-formwalt.jpg
  org/
    un/
      org@unity/
        index.md
        edges/
          edge@e-20260219-alice-introduced-by-oliver.json -> ../../../edge/e2/edge@e-20260219-alice-introduced-by-oliver.json
  note/
    de/
      note@debriefs-2026-02-19-coffee-chat-debrief-alice-formwalt/
        index.md
  edge/
    e2/
      edge@e-20260219-alice-introduced-by-oliver.json
```

## Canonical vs Derived Data

Canonical:

- `index.md` for each entity narrative and identity metadata.
- `index.md` for each note record under `data-new/note/**/note@*/`.
- JSONL tables under entity folders (`employment-history.jsonl`, `looking-for.jsonl`, `changelog.jsonl`).
- Canonical edge JSON files under `data/edge/`.

Derived:

- Entity `edges/` symlinks (derived index, not canonical truth).
- Any generated site artifacts under `site_docs/` and `site/`.
- Any ad hoc query outputs.

Rule:

- If canonical and derived disagree, canonical wins and derived must be regenerated.

## Edge Record Shape (JSON)

Minimum fields per edge file:

- `id`
- `relation`
- `directed` (boolean)
- `from` (entity path)
- `to` (entity path)
- `first_noted_at`
- `last_verified_at`
- `valid_from`
- `valid_to`
- `sources` (array of note/doc references)
- `notes`

## Data Quality and Integrity Rules

Validation should enforce:

- Edge endpoints exist and resolve to valid entities.
- Exactly one canonical file exists per edge id.
- Exactly two endpoint symlinks exist for each edge.
- Endpoint symlinks are relative, resolve correctly, and target only `data/edge/`.
- JSONL rows satisfy schema (dates, enums, required fields, nullability).
- Canonical record fields use consistent date formats (`YYYY`, `YYYY-MM`, `YYYY-MM-DD` as needed).
- Link and first-mention lint rules continue to pass for narrative markdown.

## Query Model

Primary:

- `rg` and direct file reads for small-scope retrieval.

Optional:

- DuckDB queries over globs (for example `data/**/looking-for.jsonl`, `data/**/employment-history.jsonl`, `data/edge/**/*.json`).

No persistent DB index is required for v2 initial scope.

## Concurrency Model (Current)

- Assume one active writer at a time.
- MCP/CLI mutating operations should acquire a global write lock.
- If lock is held, return a structured retryable error.
- Multi-writer improvements are deferred (possible future: git worktree-based write sessions).

## Non-Goals for This Phase

- Full relational database migration.
- Cross-machine transactional guarantees.
- Real-time collaborative editing.
