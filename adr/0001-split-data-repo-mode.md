# ADR 0001: Split Data Repository Mode and Canonical Markdown

- Status: Proposed
- Date: 2026-02-25

## Context

We want the knowledge base to support running the same application code against multiple users' data repositories.

The current implementation assumes a monorepo shape in many places:

- app code and `data/` live in one git repository
- mutation transactions run git operations from `project_root`
- some tooling assumes `project_root/data`

We also need a clear input model:

1. MCP interface as the primary write path
2. ingestion endpoint for large source inputs (transcripts, web pages, etc.)
3. limited direct human edits

Finally, we need to settle canonicality:

- markdown narratives are not reliably generated from raw sources
- we still want lightweight "check constraints" through validation and pre-commit

## Decision

### 1) Support split app/data repository mode

Introduce an explicit runtime repository context:

- `app_root`: repository containing code/tools
- `data_root`: canonical KB content directory
- `data_git_root`: git repository that owns `data_root`
- `build_root`: generated artifacts output location

Default behavior remains monorepo-compatible:

- `data_root = app_root/data`
- `data_git_root = app_root`

### 2) Treat `data_git_root` as the transaction boundary

All mutating operations (MCP and future ingestion writes) must:

- lock at `data_git_root`
- validate changed files under `data_root`
- commit and push in `data_git_root`
- reject any out-of-scope changes outside `data_root`

### 3) Input channel model

- MCP is the primary mutation interface.
- Ingestion endpoint is secondary and should write structured source artifacts and proposed patches, not directly overwrite narrative content.
- Human edits are allowed but must pass the same validation and pre-commit checks.

### 4) Canonicality model

- Narrative markdown (`index.md` and similar authored content) is canonical.
- Structured records that are explicitly canonical (`*.json`, `*.jsonl`, canonical edge files) remain canonical.
- Generated artifacts are derived only:
  - site output
  - backlinks/symlink indexes
  - search/index caches
  - embeddings/summaries
- Full markdown regeneration from source ingestion is not a default workflow.

### 5) Multi-dataset compatibility

The same application code/scripts should run against any compatible data repo via configuration (`data_root`/`data_git_root`), without user-specific logic in code paths.

## Consequences

### Positive

- clean separation of code lifecycle vs data lifecycle
- supports multiple people/org datasets using one app shell
- preserves git audit trail where it matters (data repo)
- keeps authored markdown quality as first-class

### Negative / Cost

- required refactor of git/validation/build path assumptions
- more configuration surface (`data_root`, `data_git_root`, `build_root`)
- additional integration testing burden

## Alternatives Considered

1. Keep monorepo-only model.
   - Rejected: limits multi-dataset operation and coupling is too high.

2. Make markdown fully derived from structured/source ingestion.
   - Rejected: narrative quality and maintainability regress in practice.

3. Move canonical state to a relational DB now.
   - Deferred: higher complexity than needed for current scope.

## Implementation Notes (Non-Binding)

- Add explicit `data_git_root` resolution to CLI/MCP config.
- Refactor git helpers and changed-path detection to operate on `data_git_root`.
- Make site/data tooling read from configured `data_root` rather than hardcoded `project_root/data`.
- Add split-repo integration tests:
  - app repo clean
  - data repo receives commits/pushes
  - validation and transaction guarantees remain intact

## Acceptance Criteria for "Supported Mode"

Split mode is considered supported when:

1. MCP mutations commit/push to `data_git_root` when `data_root` is external.
2. Validation (`full`, `changed`, path-scoped) works with external `data_root`.
3. Non-data writes are rejected in transactions.
4. Standard workflows can target different data repos without code changes.
