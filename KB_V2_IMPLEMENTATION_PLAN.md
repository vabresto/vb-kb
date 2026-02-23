# KB v2 Implementation Plan

Status: In progress  
Date: 2026-02-22

## Objective

Migrate from single-file entity records to folder-based entities with canonical centralized edges and structured JSONL tables, while preserving human-friendly browsing and existing git audit workflows.

## Guiding Constraints

- One canonical source of truth: files in `data/`.
- No mandatory SQLite dependency for v2 initial rollout.
- Preserve or improve readability in the generated web UI.
- Keep migration reversible until cutover is validated.

## Phase 0: Design Freeze and Migration Inputs

Deliverables:

- Freeze v2 naming and layout conventions (`person@<id>`, `org@<id>`, `edge@<id>`).
- Freeze first-pass schemas for:
  - `employment-history.jsonl`
  - `looking-for.jsonl`
  - `changelog.jsonl`
- Define edge relation enum v1 and required edge fields.

Exit criteria:

- Architecture document approved.
- Migration mapping rules documented (old paths to new paths).

## Phase 1: Create New Folder Structure

Scope:

- Create folder structure under `data/person/`, `data/org/`, `data/edge/`.
- Move each current `data/person/*.md` and `data/org/*.md` into `index.md` under new folders.
- Create empty `edges/` and `images/` folders per entity where relevant.

Notes:

- Keep old files temporarily as compatibility aliases or redirects only during migration. For now, use a `data-new/` top level folder for the new data.
- Prefer scripted migration to avoid manual mistakes.

Exit criteria:

- Every existing person and org exists at a new canonical path.
- Site build still runs with compatibility layer in place.

## Phase 2: Introduce Pydantic Schemas and Validation CLI

Scope:

- Add Pydantic models for:
  - Employment history row
  - Looking-for row
  - Changelog row
  - Edge record
- Implement `kb validate` CLI command to validate:
  - JSON/JSONL schema correctness
  - entity and edge references
  - symlink integrity
  - date format and enum constraints

Exit criteria:

- `kb validate` can validate full repo and changed files.
- Validator output is deterministic and machine-readable.

## Phase 3: Migrate Existing Structured Data to JSONL

Scope:

- Extract current structured data from frontmatter and markdown tables into:
  - `employment-history.jsonl`
  - `looking-for.jsonl`
  - `changelog.jsonl`
- Keep `index.md` focused on narrative + identity summary.

Rules:

- Preserve provenance during extraction.
- Preserve link integrity for first entity mentions in narrative sections.
- Migrate legacy notes into canonical note folders with `note@<id>` frontmatter IDs.

Exit criteria:

- Current structured person/org data represented in JSONL.
- Legacy notes represented as canonical `data-new/note/**/note@*/index.md` records.
- Spot checks on representative entities pass schema and content parity checks.

## Phase 4: Edge Canonicalization and Backlink Symlinks

Scope:

- Create canonical edge records in `data/edge/`.
- Add relative endpoint symlinks in each involved entity `edges/` folder.
- Add regeneration command for symlink index from canonical edge set.

Exit criteria:

- Every edge has one canonical record.
- Every canonical edge has exactly two valid endpoint symlinks.
- Regeneration is idempotent.

## Phase 5: Update Site Builder for Human-Friendly UI

Scope:

- Update `tools/build_site_content.py` to read new entity folder layout.
- Render narrative from `index.md`.
- Render structured sections (`Employment History`, `Looking For`, `Changelog`) from JSONL tables.
- Optionally render a relations section from canonical edges/backlinks.
- Render note pages from canonical note records in `data-new/note/` (fallback: legacy `data/notes/`).

Exit criteria:

- `uv run mkdocs serve` works with new layout.
- Person/org pages remain easy to scan and navigate.
- No critical regressions in existing content presentation.

## Phase 6: Hook and CI Integration

Scope:

- Wire `kb validate` into `.pre-commit-config.yaml`.
- Keep existing first-mention and URL checks; update file globs for new layout.
- Add CI job to run full validation on PRs.

Exit criteria:

- Local pre-commit and CI both enforce the same rules.
- Invalid edge/symlink/schema changes fail fast.

## Phase 7: MCP Write Path (Single User, Single Active Writer)

Scope:

- Add Python FastMCP mutating commands for entity/edge operations.
- Route all writes through one write transaction flow:
  - acquire repo write lock
  - apply edits
  - run `kb validate` on changed scope
  - commit if valid
  - release lock
- Return retryable conflict/busy errors when lock is held.
- Implement OAuth2 Authorization Code + PKCE (S256) for interactive user auth.
- Keep the MCP write path Python-only.

Exit criteria:

- MCP can safely create/update entities and edges.
- Validation is fail-closed.
- Concurrency behavior is predictable for callers.
- FastMCP transport is available for local/stdin and HTTP deployment modes.

## Phase 8: Multi-Writer Investigation (Deferred)

Potential approaches:

- Git worktree-backed write sessions per caller.
- Branch-per-session with server-side rebase-and-validate before merge.
- Queue-based serialized write service.

Decision gate:

- Revisit only when single-writer retry flow becomes a practical bottleneck.

## Suggested Execution Order

1. Phase 0
2. Phase 1
3. Phase 2
4. Phase 3
5. Phase 4
6. Phase 5
7. Phase 6
8. Phase 7
9. Phase 8

## Immediate Next Task

Run Phase 8 investigation for multi-writer semantics once the single-writer FastMCP write path sees regular usage.
