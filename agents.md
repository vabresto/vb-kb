# Knowledge Base Rules

## Automation Surface

- Any runnable workflow in this repo must be exposed in the root `justfile`.
- When adding new scripts, integration suites, deploy commands, or maintenance tasks, add/update the corresponding `just` target in the same change.
- Prefer documenting usage as `just <target>` instead of ad-hoc shell invocations.

## Enrichment Sessions

- Use `lookup_session_state(...)` before authenticated enrichment extraction. It returns actionable missing/expired diagnostics, while invalid `storageState` payloads fail fast with typed authentication errors.
- For portable session transfer, use an envelope JSON with `source`, `exported_at`, `expires_at`, and `storage_state`; reject imports when `source` does not match the target adapter source.
- `kb bootstrap-session` resolves per-source login commands from `SourceSettings.bootstrap_command` (or `KB_ENRICHMENT_*_BOOTSTRAP_COMMAND` env overrides), sets `KB_ENRICHMENT_BOOTSTRAP_*` context env vars for the command, and expects JSON output as raw storageState (`cookies` + `origins`) or `{ "storage_state": ... }`.
- Adapter result path fields (`AuthenticationResult.used_session_state_path`, `SnapshotResult.snapshot_path`) must stay relative paths; resolve absolute filesystem paths only for local I/O and return the relative path token in typed models.
- Command-backed source adapters should use source-specific fetch command env vars (`KB_ENRICHMENT_LINKEDIN_FETCH_COMMAND`, `KB_ENRICHMENT_SKOOL_FETCH_COMMAND`) while emitting the shared `KB_ENRICHMENT_EXTRACT_*` runtime env contract.
- `kb enrich-entity <entity>` is intentionally single-target kickoff: pass one slug/path positional and repeat `--source` when you need multiple adapters in one run.
- Keep orchestration output in the shared `EnrichmentRunReport` schema (`extraction`, `source_logging`, `mapping`, `validation`, `reporting`) and persist it to `EnrichmentConfig.run_report_path` for deterministic run auditing.
- Source logging persists one canonical source entity per successful `(run_id, source, entity)` extraction under `data/source/<shard>/source@enrichment-.../`, with `index.md` plus a structured `facts.json`; keep `SourceExtractionState.source_entity_ref/source_entity_path/facts_artifact_path` in sync with those writes.
- Person mapping (`US-009`) promotes only facts meeting `confidence_policy.minimum_promotion_level`; keep narrative body sections unchanged and write person updates through canonical `data/person/<shard>/person@<slug>/index.md` frontmatter fields (`firm`, `role`, `location`).
- When enrichment updates the current person `firm`/`role`, archive the previous pair into `employment-history.jsonl` as the next `employment-###` row with `source_section: employment_history_table` to preserve current-role frontmatter semantics.
- Organization mapping (`US-010`) should write through canonical `data/org/<shard>/org@<slug>/index.md`, update frontmatter fields (`org`, `website`, `hq-location`, `thesis`) only from confidence-qualified facts, and merge `known-people` entries by canonical person reference while preserving existing narrative body sections.
- Provenance enforcement (`US-011`) must fail mapping when any promoted fact lacks `source_entity_ref`, `source_entity_path`, or derivable citation key; when facts are promoted, upsert a managed `## Enrichment Provenance` section with source-entity links plus `[^citation-key]` footnote tokens (never URL-only citations), and keep `EnrichmentRunReport.fact_to_source_mappings` in sync.

## Reference Integrity

- In structured reference fields (for example `known-people`), reference entities like people by file path link, not by plain name.
- Preferred format for `data/org/*.md` `known-people` is a structured object:
  - `person`: `"[Full Name](../person/person-slug.md)"`
  - `relationship`: `current | former | advisor | investor | alumni | other`
  - `relationship-details`
  - `relationship-start-date`
  - `relationship-end-date`
  - `first-noted-at`
  - `last-verified-at`
- Use `null` for `relationship-start-date` or `relationship-end-date` when dates are unknown.
- Date values can be `YYYY-MM-DD` when exact, or `YYYY-MM`/`YYYY` when only partial precision is known.
- If a referenced person file does not exist, create it before adding the reference.
- Rationale: names are not unique; file paths are unique and auditable.

### Example

```yaml
known-people:
  - person: "[David Tisch](../person/david-tisch.md)"
    relationship: current
    relationship-details: Current team member.
    relationship-start-date: null
    relationship-end-date: null
    first-noted-at: 2026-02-16
    last-verified-at: 2026-02-16
```

## Employment History

- In `data/person/*.md`, keep frontmatter `firm` and `role` for current (or most recent) employment only.
- Record prior roles in a dedicated `## Employment History` section.
- Use a table with columns: `Period`, `Organization`, `Role`, `Notes`, `Source`.
- Include source footnotes when public sources exist; use `Internal note` when details come from private context.

## Looking For

- In `data/person/*.md` frontmatter, track asks in `looking-for`.
- Keep `looking-for: []` when no active asks are known (common for cold contacts).
- Each `looking-for` item should include at minimum:
  - `ask`
  - `details`
  - `first-asked-at`
  - `last-checked-at`
- Recommended additional fields:
  - `status` (for example `open`, `paused`, `closed`)
  - `notes`

## Organization Links

- In person profiles, organization mentions in core sections (`Snapshot`, `Employment History`, `Bio`) should link to their org page using a relative path like `[Org Name](../org/org-slug.md)`.
- If an org page does not exist, create `data/org/org-slug.md` with at least a minimal sourced snapshot and bio.

## First Mention Linking

- Follow a Wikipedia-like rule in page body content: the first mention of each known entity (person or organization) should be linked to its local KB file path.
- Use relative links (`../person/...` or `../org/...`) in `Snapshot`, `Employment History`, `Bio`, and other narrative sections.
- If an entity does not yet have a page, create it before adding the first-mention link.
