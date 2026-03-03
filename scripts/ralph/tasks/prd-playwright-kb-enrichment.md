# PRD: Playwright Authenticated Enrichment for KB Entities

## 1. Introduction/Overview

Add a Playwright-based enrichment pipeline so Codex can, after a manual kickoff, autonomously gather authenticated information about one target entity (person or organization), normalize it, and write updates directly to canonical KB files.

v1 is headless by default, supports saved sessions, and supports scripted login bootstrap flows. When needed, operators can run a local headful bootstrap to export a session artifact (JSON `storageState`) for use in headless environments. Human review happens at the end of a run, not during execution.

v1 sources are `linkedin.com` and `skool.com`.

## 2. Goals

- Enable manual kickoff of a single-entity enrichment run that executes fully autonomously.
- Support authenticated access via local secret manager + env var fallback, using saved Playwright sessions and scripted login bootstrap.
- Keep default execution headless while supporting local headful session bootstrap/export when required.
- Update canonical KB files directly under `data/` with validation gating.
- Log all extracted facts to source entities, while promoting only reasonably confident facts to person/org entities.
- Require both structured provenance metadata and body citations that reference KB source entities.
- Ensure all runnable workflows are exposed via root `justfile` targets.

## 3. User Stories

### US-001: Kick off autonomous enrichment for one entity
**Description:** As an operator, I want to start enrichment for one person or org so that Codex performs a full run without in-run approvals.

**Acceptance Criteria:**
- [ ] A single command can target one entity record by path or slug.
- [ ] The run executes end-to-end without requiring interactive confirmation after kickoff.
- [ ] The run emits a final structured report summarizing files changed, sources accessed, and pass/fail status.
- [ ] `just` target exists for this workflow.

### US-002: Reuse saved authenticated sessions
**Description:** As an operator, I want enrichment to reuse saved sessions so that routine runs can access private content without repeated login.

**Acceptance Criteria:**
- [ ] Session storage format and location are documented.
- [ ] Sessions are loaded automatically per source adapter when available.
- [ ] Missing or expired session states are detected with explicit error messages.
- [ ] Session artifacts are excluded from version control.

### US-003: Bootstrap sessions for headless environments
**Description:** As an operator, I want scripted bootstrap paths so I can initialize or refresh credentials for headless runs.

**Acceptance Criteria:**
- [ ] A dedicated command can run source-specific scripted login bootstrap in headless mode.
- [ ] A local operator flow can run headful bootstrap and export JSON `storageState` for transfer into headless environments.
- [ ] On successful bootstrap, session artifacts are persisted for future runs.
- [ ] Bootstrap failure returns actionable diagnostics (source, step, reason).

### US-004: Extract normalized facts from LinkedIn and Skool
**Description:** As Codex, I want a reusable source-adapter interface so that the system can support `linkedin.com` and `skool.com` in v1 with consistent output shape.

**Acceptance Criteria:**
- [ ] A common adapter contract defines required inputs/outputs and error semantics.
- [ ] Source adapters for `linkedin.com` and `skool.com` are implemented in v1 and pass adapter contract tests.
- [ ] Extracted records include source URL, retrieval timestamp, and confidence metadata.
- [ ] Unsupported entities/sources fail gracefully with typed errors.
- [ ] Anti-bot/MFA detection fails fast with remediation guidance rather than silent retries.

### US-005: Map confident facts into person/org KB files
**Description:** As Codex, I want to map confident extracted facts into canonical person/org files so enrichment updates are useful and trustworthy.

**Acceptance Criteria:**
- [ ] Writes target canonical `data/person/*` and `data/org/*` records (or newly created valid records where required).
- [ ] Facts below confidence threshold are not promoted to person/org records.
- [ ] First-mention linking and organization/person link rules are preserved in narrative sections.
- [ ] Structured fields use expected object formats (for example, `known-people` path links and relationship fields).
- [ ] Employment updates preserve frontmatter current-role semantics and append prior roles to `## Employment History` table.

### US-006: Persist source records, snapshots, and full fact logs
**Description:** As a reviewer, I want all extracted facts and evidence captured under source entities so private-source enrichment is auditable even when low-confidence facts are not promoted.

**Acceptance Criteria:**
- [ ] Each run writes/updates KB source entities under nested `data/source/` paths.
- [ ] Source records include full extracted fact logs with confidence tags.
- [ ] Source records include page dump/snapshot artifacts when technically feasible.
- [ ] Source artifacts are referenced from run outputs so reviewers can inspect evidence.

### US-007: Enforce provenance and citation linking to source entities
**Description:** As a reviewer, I want every promoted fact to trace back to KB source entities so I can audit where claims came from.

**Acceptance Criteria:**
- [ ] Promoted facts include structured provenance metadata (source identifier, retrieval timestamp, and location pointer where available).
- [ ] Body citations reference KB source entities (not just raw URLs).
- [ ] Run report includes fact-to-source summary for all changed entities.
- [ ] Missing provenance for any promoted fact fails the run.

### US-008: Validation failure handling with auto-remediation then human handoff
**Description:** As an operator, I want validation failures handled automatically first and escalated only if needed so runs can recover without unnecessary intervention.

**Acceptance Criteria:**
- [ ] Enrichment run automatically executes `just validate-changed` (or equivalent scoped validation) before reporting success.
- [ ] On validation failure, the system performs one automated remediation attempt.
- [ ] If remediation still fails, run status is blocked/failed and the system requests human intervention with diagnostics.
- [ ] No automatic rollback is required in v1.

### US-009: Publish operator-facing workflows in justfile and docs
**Description:** As a maintainer, I want all enrichment workflows exposed via `just` so operations are discoverable and consistent.

**Acceptance Criteria:**
- [ ] Root `justfile` includes targets for enrichment run, session bootstrap/export, and Playwright-focused tests/checks.
- [ ] README or dedicated docs explain secret handling, required env vars, and usage as `just <target>`.
- [ ] Help text distinguishes v1 manual kickoff from future scheduling.
- [ ] Commands are reproducible in local development without undocumented shell steps.

## 4. Functional Requirements

- FR-1: The system must provide a manual kickoff command to enrich exactly one target entity per invocation.
- FR-2: After kickoff, the run must execute autonomously without in-run human approval prompts.
- FR-3: The default browser mode for enrichment and scripted login bootstrap must be headless.
- FR-4: The system must support loading persisted Playwright `storageState` sessions per source.
- FR-5: The system must support scripted headless login to create/refresh sessions when missing or expired.
- FR-6: The system must support local headful session bootstrap and JSON `storageState` export for portability to headless environments.
- FR-7: Authentication secrets must support local secret manager integration with environment-variable fallback.
- FR-8: Session artifacts and credential material must be stored outside tracked canonical data and excluded from git.
- FR-9: v1 must support `linkedin.com` and `skool.com` through a reusable adapter interface.
- FR-10: Adapter outputs must normalize extracted facts into a common internal schema, including confidence metadata.
- FR-11: The mapper must update canonical KB files under `data/` directly and preserve repository content rules.
- FR-12: Promoted facts written to `data/person/*` and `data/org/*` must meet a minimum confidence threshold (v1 default: medium or higher).
- FR-13: All extracted facts, including low-confidence facts, must be logged under source entities with confidence tags.
- FR-14: Source entities must follow nested `data/source/` organization and store snapshot/dump artifacts when feasible.
- FR-15: Body citations for promoted facts must reference KB source entities.
- FR-16: If enrichment references missing linked entities, the run must create valid referenced pages before linking.
- FR-17: The run must automatically execute validation checks and block success status on unresolved validation failures.
- FR-18: On validation failure, the system must attempt one automated remediation pass, then escalate to human input if still failing.
- FR-19: The run must produce a structured final report including changed files, validation/remediation status, and source summary.
- FR-20: The system must fail with explicit, typed errors for authentication, anti-bot/MFA challenge, extraction, mapping, and validation failures.
- FR-21: All runnable enrichment workflows must be exposed through root `justfile` targets.

## 5. Non-Goals (Out of Scope)

- Fully automated scheduling or cron-based kickoff.
- Broad generic support for many websites beyond `linkedin.com` and `skool.com`.
- Interactive in-run review or approval gates.
- Building a full UI/dashboard for run management.
- Automatic rollback of KB writes on validation failure in v1.
- Large-scale historical backfill for all entities in one release.

## 6. Design Considerations (Optional)

- CLI-first operator UX with explicit phases: `bootstrap-session`, `export-session`, `enrich-entity`, `validate`, `report`.
- Deterministic logging format so reviewers can compare runs.
- Adapter behavior should minimize anti-bot triggers by using conservative navigation patterns.
- Error messages should identify source adapter and entity target to reduce triage time.

## 7. Technical Considerations (Optional)

- Keep Playwright integration modular: core orchestrator + adapter plugins + mappers.
- Define a stable adapter contract to reduce schema drift between sources.
- Use atomic file writes to minimize partial-write risk during direct updates.
- Store session state under `.build/` and ensure `.gitignore` coverage.
- Reuse existing nested `data/source/<prefix>/source@<slug>/` conventions.
- Capture source snapshot artifacts in a deterministic format and path (format to be finalized during implementation).
- Include unit tests for mapping/provenance/confidence gating and integration smoke tests for auth/session paths.

## 8. Success Metrics

- Both prioritized authenticated sources (`linkedin.com` and `skool.com`) are operational in v1 on real entity runs.
- At least 90% of enrichment runs complete end-to-end for supported sources.
- 100% of promoted person/org facts include required provenance metadata and source-entity citations.
- 0 promoted person/org facts fall below the configured minimum confidence threshold.
- 100% of successful runs pass validation before completion.

## 9. Open Questions

- Which local secret manager should be the first supported backend (for example, 1Password CLI vs. macOS Keychain)?
- What snapshot format should be canonical for source dumps in v1 (HTML, MHTML, screenshot bundle, or hybrid)?
- What is the preferred human-handoff channel when anti-bot/MFA blocks automation (for example, local pair session vs. screen share)?
- How should confidence scoring be calibrated per source adapter to keep "medium" consistent across LinkedIn and Skool?
