from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import Field, ValidationError
import yaml

from kb.enrichment_adapters import (
    FetchRequest,
    FetchResult,
    NormalizedFact,
    NormalizeResult,
    NormalizeRequest,
    SnapshotResult,
    SnapshotRequest,
    SourceAdapterError,
    SourceAdapterRegistry,
)
from kb.enrichment_config import ConfidenceLevel, EnrichmentConfig, SupportedSource
from kb.enrichment_linkedin_adapter import LinkedInSourceAdapter
from kb.enrichment_skool_adapter import SkoolSourceAdapter
from kb.edges import derive_citation_edges, derive_employment_edges, sync_edge_backlinks
from kb.schemas import (
    EmploymentHistoryRow,
    KBBaseModel,
    SourceRecord,
    SourceType,
    normalize_path_token,
    parse_partial_date,
    shard_for_slug,
    validate_entity_rel_path,
)
from kb.validate import collect_changed_paths, infer_data_root, run_validation

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ENTITY_REF_TOKEN_RE = re.compile(
    r"^(?P<kind>person|org|source)@(?P<slug>[a-z0-9][a-z0-9-]*)$"
)
_ENTITY_PATH_SLUG_RE = re.compile(
    r"(?:^|/)(?:person|org|source)@(?P<slug>[a-z0-9][a-z0-9-]*)(?:/index\.md)?$"
)
_FRONTMATTER_BLOCK_RE = re.compile(r"\A---\n(?P<frontmatter>.*?)\n---\n?(?P<body>.*)\Z", re.DOTALL)
_ENRICHMENT_PROVENANCE_SECTION_RE = re.compile(
    r"\n*## Enrichment Provenance\n\n<!-- enrichment-provenance:start -->.*?<!-- enrichment-provenance:end -->\n*",
    re.DOTALL,
)
_MARKDOWN_LINK_RE = re.compile(r"^\[(?P<label>[^\]]+)\]\((?P<target>[^)]+)\)$")
_EMPLOYMENT_ID_RE = re.compile(r"^employment-(?P<index>\d{3})$")
_ROLE_ATTRIBUTE_PRIORITY = {
    "current_role": 0,
    "role": 1,
    "headline": 2,
}
_FIRM_ATTRIBUTE_PRIORITY = {
    "current_company": 0,
    "current_organization": 1,
    "company": 2,
    "organization": 3,
}
_LOCATION_ATTRIBUTE_PRIORITY = {
    "location": 0,
}
_ORG_NAME_ATTRIBUTE_PRIORITY = {
    "organization_name": 0,
    "org_name": 1,
    "org": 2,
    "company_name": 3,
    "community": 4,
    "name": 5,
}
_ORG_WEBSITE_ATTRIBUTE_PRIORITY = {
    "website": 0,
    "homepage": 1,
}
_ORG_HQ_LOCATION_ATTRIBUTE_PRIORITY = {
    "hq_location": 0,
    "headquarters": 1,
    "location": 2,
}
_ORG_THESIS_ATTRIBUTE_PRIORITY = {
    "about": 0,
    "description": 1,
    "summary": 2,
    "headline": 3,
}
_ORG_KNOWN_PERSON_ATTRIBUTES = frozenset(
    {
        "known_person",
        "known_people",
        "person",
        "founder",
        "cofounder",
        "co_founder",
        "ceo",
        "employee",
        "team_member",
        "advisor",
        "investor",
        "alumni",
    }
)
_KNOWN_PEOPLE_RELATIONSHIPS = frozenset(
    {
        "current",
        "former",
        "advisor",
        "investor",
        "alumni",
        "other",
        "reported",
    }
)
_RELATIONSHIP_DEFAULT_BY_ATTRIBUTE = {
    "founder": "current",
    "cofounder": "current",
    "co_founder": "current",
    "ceo": "current",
    "employee": "current",
    "team_member": "current",
    "advisor": "advisor",
    "investor": "investor",
    "alumni": "alumni",
}
_CONFIDENCE_RANK = {
    ConfidenceLevel.low: 0,
    ConfidenceLevel.medium: 1,
    ConfidenceLevel.high: 2,
}
_PROVENANCE_POINTER_METADATA_KEYS = (
    "location_pointer",
    "location-pointer",
    "source_location_pointer",
    "source-location-pointer",
    "source_pointer",
    "source-pointer",
    "source_locator",
    "source-locator",
    "selector",
    "xpath",
    "css_selector",
    "css-selector",
)


class RunStatus(str, Enum):
    succeeded = "succeeded"
    partial = "partial"
    failed = "failed"
    blocked = "blocked"


class PhaseStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"


class EnrichmentRunError(RuntimeError):
    """Base error for enrichment run kickoff/orchestration failures."""


class EntityTargetResolutionError(EnrichmentRunError):
    def __init__(self, *, entity_target: str, details: str) -> None:
        super().__init__(f"unable to resolve entity target '{entity_target}': {details}")
        self.entity_target = entity_target
        self.details = details


class RunReportWriteError(EnrichmentRunError):
    def __init__(self, *, report_path: str, details: str) -> None:
        super().__init__(f"unable to write run report to '{report_path}': {details}")
        self.report_path = report_path
        self.details = details


class SourceRecordWriteError(EnrichmentRunError):
    def __init__(self, *, source: SupportedSource, path: str, details: str) -> None:
        super().__init__(f"unable to persist source record for '{source.value}' at '{path}': {details}")
        self.source = source
        self.path = path
        self.details = details


class PromotedFactProvenanceError(EnrichmentRunError):
    def __init__(
        self,
        *,
        entity_label: str,
        attribute: str,
        source: SupportedSource,
        details: str,
    ) -> None:
        message = (
            f"{entity_label} promoted fact missing source entity linkage "
            f"for attribute '{attribute}' from '{source.value}': {details}"
        )
        super().__init__(message)
        self.entity_label = entity_label
        self.attribute = attribute
        self.source = source
        self.details = details


class EntityTarget(KBBaseModel):
    entity_ref: str
    entity_slug: str


class PhaseState(KBBaseModel):
    status: PhaseStatus
    message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class SourceExtractionState(KBBaseModel):
    source: SupportedSource
    status: PhaseStatus
    source_url: str | None = None
    retrieved_at: datetime | None = None
    facts_count: int = 0
    snapshot_path: str | None = None
    source_entity_ref: str | None = None
    source_entity_path: str | None = None
    facts_artifact_path: str | None = None
    source_logging_error_type: str | None = None
    source_logging_error: str | None = None
    error_type: str | None = None
    error: str | None = None


class ExtractionPhaseState(PhaseState):
    sources: list[SourceExtractionState] = Field(default_factory=list)


class RunPhaseStates(KBBaseModel):
    extraction: ExtractionPhaseState
    source_logging: PhaseState
    mapping: PhaseState
    validation: PhaseState
    reporting: PhaseState


class FactSourceMapping(KBBaseModel):
    attribute: str
    value: str
    confidence: ConfidenceLevel
    source_identifier: SupportedSource
    source_entity_ref: str
    source_entity_path: str
    source_citation_key: str
    source_url: str
    retrieved_at: datetime
    location_pointer: str | None = None


class EntityFactSourceSummary(KBBaseModel):
    entity_kind: str
    entity_ref: str
    entity_index_path: str
    mappings: list[FactSourceMapping] = Field(default_factory=list)


class EnrichmentRunReport(KBBaseModel):
    run_id: str
    entity_ref: str
    entity_slug: str
    selected_sources: list[SupportedSource]
    autonomous: bool = True
    status: RunStatus
    started_at: datetime
    completed_at: datetime
    facts_extracted_total: int = 0
    report_path: str
    phases: RunPhaseStates
    fact_to_source_mappings: list[EntityFactSourceSummary] = Field(default_factory=list)


class _SuccessfulExtraction(KBBaseModel):
    source: SupportedSource
    fetch_result: FetchResult
    normalize_result: NormalizeResult
    snapshot_result: SnapshotResult


class _SourceEntityArtifact(KBBaseModel):
    source_entity_ref: str
    source_entity_path: str
    facts_artifact_path: str


class _SourceLoggingError(KBBaseModel):
    error_type: str
    error: str


class _PromotedFact(KBBaseModel):
    source: SupportedSource
    source_entity_ref: str | None = None
    source_entity_path: str | None = None
    source_citation_key: str | None = None
    attribute: str
    value: str
    confidence: ConfidenceLevel
    source_url: str
    retrieved_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class _PersonMappingResult(KBBaseModel):
    person_index_path: str
    promoted_fact_count: int = 0
    frontmatter_fields_updated: list[str] = Field(default_factory=list)
    employment_rows_added: int = 0
    fact_source_summary: EntityFactSourceSummary | None = None


class _OrganizationMappingResult(KBBaseModel):
    organization_index_path: str
    promoted_fact_count: int = 0
    frontmatter_fields_updated: list[str] = Field(default_factory=list)
    known_people_entries_added: int = 0
    fact_source_summary: EntityFactSourceSummary | None = None


class _MappingPhaseResult(KBBaseModel):
    phase: PhaseState
    fact_to_source_mappings: list[EntityFactSourceSummary] = Field(default_factory=list)


class _KnownPersonEntry(KBBaseModel):
    person_ref: str
    person_name: str
    relationship: str
    relationship_details: str | None = None
    relationship_start_date: str | None = None
    relationship_end_date: str | None = None
    first_noted_at: str
    last_verified_at: str


def build_default_adapter_registry(
    *,
    config: EnrichmentConfig,
    project_root: Path,
    environ: Mapping[str, str] | None = None,
) -> SourceAdapterRegistry:
    return SourceAdapterRegistry(
        adapters=(
            LinkedInSourceAdapter(
                config=config,
                project_root=project_root,
                environ=environ,
            ),
            SkoolSourceAdapter(
                config=config,
                project_root=project_root,
                environ=environ,
            ),
        )
    )


def resolve_entity_target(entity_target: str) -> EntityTarget:
    raw_target = entity_target.strip()
    if not raw_target:
        raise EntityTargetResolutionError(
            entity_target=entity_target,
            details="target must be non-empty",
        )

    if "/" in raw_target or raw_target.endswith(".md"):
        normalized_path = normalize_path_token(raw_target)
        if normalized_path is None:
            raise EntityTargetResolutionError(
                entity_target=entity_target,
                details="entity path must be non-empty",
            )
        match = _ENTITY_PATH_SLUG_RE.search(normalized_path.lower().rstrip("/"))
        if match is None:
            raise EntityTargetResolutionError(
                entity_target=entity_target,
                details=(
                    "path must include a canonical entity segment like "
                    "'person@<slug>', 'org@<slug>', or 'source@<slug>'"
                ),
            )
        return EntityTarget(
            entity_ref=normalized_path,
            entity_slug=match.group("slug"),
        )

    token = raw_target.lower()
    token_match = _ENTITY_REF_TOKEN_RE.fullmatch(token)
    if token_match is None:
        raise EntityTargetResolutionError(
            entity_target=entity_target,
            details=(
                "target must be '<kind>@<slug>' (for example 'person@founder-name') "
                "or a canonical entity path containing one of "
                "'person@<slug>', 'org@<slug>', or 'source@<slug>'"
            ),
        )
    return EntityTarget(
        entity_ref=token,
        entity_slug=token_match.group("slug"),
    )


def run_enrichment_for_entity(
    entity_target: str,
    *,
    selected_sources: Iterable[SupportedSource | str] | None,
    config: EnrichmentConfig,
    project_root: Path,
    adapter_registry: SourceAdapterRegistry | None = None,
    now: datetime | None = None,
    run_id: str | None = None,
) -> EnrichmentRunReport:
    resolved_root = project_root.resolve()
    resolved_target = resolve_entity_target(entity_target)
    resolved_sources = _normalize_sources(selected_sources)
    started_at = _normalize_now(now)
    resolved_run_id = run_id or _build_run_id(started_at)
    registry = adapter_registry or build_default_adapter_registry(
        config=config,
        project_root=resolved_root,
    )

    extraction_phase = ExtractionPhaseState(
        status=PhaseStatus.running,
        message="running extraction adapters",
        started_at=started_at,
    )
    source_states: list[SourceExtractionState] = []
    successful_extractions: list[_SuccessfulExtraction] = []
    extracted_fact_total = 0

    for source in resolved_sources:
        request = FetchRequest(
            entity_ref=resolved_target.entity_ref,
            entity_slug=resolved_target.entity_slug,
            run_id=resolved_run_id,
            started_at=started_at,
        )
        try:
            adapter = registry.get(source)
            fetch_result = adapter.fetch(request)
            normalize_result = adapter.normalize(NormalizeRequest(fetch_result=fetch_result))
            facts_count = len(normalize_result.facts)
            snapshot_path = _build_snapshot_output_path(
                source=source,
                config=config,
                run_id=resolved_run_id,
                entity_slug=resolved_target.entity_slug,
            )
            snapshot_result = adapter.snapshot(
                SnapshotRequest(
                    fetch_result=fetch_result,
                    output_path=snapshot_path,
                )
            )
            extracted_fact_total += facts_count
            source_states.append(
                SourceExtractionState(
                    source=source,
                    status=PhaseStatus.succeeded,
                    source_url=fetch_result.source_url,
                    retrieved_at=fetch_result.retrieved_at,
                    facts_count=facts_count,
                    snapshot_path=snapshot_result.snapshot_path,
                )
            )
            successful_extractions.append(
                _SuccessfulExtraction(
                    source=source,
                    fetch_result=fetch_result,
                    normalize_result=normalize_result,
                    snapshot_result=snapshot_result,
                )
            )
        except SourceAdapterError as exc:
            source_states.append(
                SourceExtractionState(
                    source=source,
                    status=PhaseStatus.failed,
                    error_type=exc.__class__.__name__,
                    error=str(exc),
                )
            )
        except Exception as exc:  # pragma: no cover - defensive fallback for unexpected adapter errors.
            source_states.append(
                SourceExtractionState(
                    source=source,
                    status=PhaseStatus.failed,
                    error_type=exc.__class__.__name__,
                    error=str(exc) or exc.__class__.__name__,
                )
            )

    extraction_failed = any(state.status == PhaseStatus.failed for state in source_states)
    extraction_phase.sources = source_states
    extraction_phase.completed_at = _normalize_now()
    if extraction_failed:
        extraction_phase.status = PhaseStatus.failed
        extraction_phase.message = "one or more extraction sources failed"
    else:
        extraction_phase.status = PhaseStatus.succeeded
        extraction_phase.message = f"extraction completed for {len(source_states)} source(s)"

    source_logging_phase, source_artifacts, source_logging_errors = _build_source_logging_phase(
        extraction_failed=extraction_failed,
        successful_extractions=successful_extractions,
        resolved_target=resolved_target,
        run_id=resolved_run_id,
        project_root=resolved_root,
    )
    source_states_by_source = {state.source: state for state in source_states}
    for source, artifact in source_artifacts.items():
        state = source_states_by_source.get(source)
        if state is None:
            continue
        state.source_entity_ref = artifact.source_entity_ref
        state.source_entity_path = artifact.source_entity_path
        state.facts_artifact_path = artifact.facts_artifact_path
    for source, error in source_logging_errors.items():
        state = source_states_by_source.get(source)
        if state is None:
            continue
        state.source_logging_error_type = error.error_type
        state.source_logging_error = error.error

    mapping_result = _build_mapping_phase(
        extraction_failed=extraction_failed,
        successful_extractions=successful_extractions,
        source_artifacts=source_artifacts,
        resolved_target=resolved_target,
        config=config,
        run_id=resolved_run_id,
        project_root=resolved_root,
    )
    mapping_phase = mapping_result.phase
    validation_phase = _build_validation_phase(
        extraction_failed=extraction_failed,
        mapping_phase_status=mapping_phase.status,
        project_root=resolved_root,
        run_started_at=started_at,
    )
    reporting_phase = PhaseState(
        status=PhaseStatus.succeeded,
        message=f"run report persisted to {config.run_report_path}",
        started_at=_normalize_now(),
        completed_at=_normalize_now(),
    )
    completed_at = _normalize_now()

    if (
        extraction_failed
        or mapping_phase.status == PhaseStatus.failed
    ):
        run_status = RunStatus.failed
    elif validation_phase.status == PhaseStatus.failed:
        run_status = RunStatus.blocked
    elif (
        source_logging_phase.status == PhaseStatus.succeeded
        and mapping_phase.status == PhaseStatus.succeeded
        and validation_phase.status == PhaseStatus.succeeded
    ):
        run_status = RunStatus.succeeded
    else:
        run_status = RunStatus.partial

    report = EnrichmentRunReport(
        run_id=resolved_run_id,
        entity_ref=resolved_target.entity_ref,
        entity_slug=resolved_target.entity_slug,
        selected_sources=list(resolved_sources),
        status=run_status,
        started_at=started_at,
        completed_at=completed_at,
        facts_extracted_total=extracted_fact_total,
        report_path=config.run_report_path,
        phases=RunPhaseStates(
            extraction=extraction_phase,
            source_logging=source_logging_phase,
            mapping=mapping_phase,
            validation=validation_phase,
            reporting=reporting_phase,
        ),
        fact_to_source_mappings=mapping_result.fact_to_source_mappings,
    )
    _write_run_report(report, project_root=resolved_root)
    return report

def _build_source_logging_phase(
    *,
    extraction_failed: bool,
    successful_extractions: list[_SuccessfulExtraction],
    resolved_target: EntityTarget,
    run_id: str,
    project_root: Path,
) -> tuple[PhaseState, dict[SupportedSource, _SourceEntityArtifact], dict[SupportedSource, _SourceLoggingError]]:
    started_at = _normalize_now()
    if not successful_extractions:
        message = "no successful extraction outputs available for source logging"
        if extraction_failed:
            message = "skipped source logging because extraction produced no successful source outputs"
        return (
            PhaseState(
                status=PhaseStatus.skipped,
                message=message,
                started_at=started_at,
                completed_at=_normalize_now(),
            ),
            {},
            {},
        )

    artifacts: dict[SupportedSource, _SourceEntityArtifact] = {}
    errors: dict[SupportedSource, _SourceLoggingError] = {}
    for extraction in sorted(successful_extractions, key=lambda item: item.source.value):
        try:
            artifacts[extraction.source] = _write_source_entity_record(
                source=extraction.source,
                fetch_result=extraction.fetch_result,
                normalize_result=extraction.normalize_result,
                snapshot_result=extraction.snapshot_result,
                target=resolved_target,
                run_id=run_id,
                project_root=project_root,
            )
        except Exception as exc:  # pragma: no cover - defensive fallback for filesystem or schema edge cases.
            errors[extraction.source] = _SourceLoggingError(
                error_type=exc.__class__.__name__,
                error=str(exc) or exc.__class__.__name__,
            )

    completed_at = _normalize_now()
    if errors:
        message = f"logged {len(artifacts)} source record(s); {len(errors)} source record write(s) failed"
        return (
            PhaseState(
                status=PhaseStatus.failed,
                message=message,
                started_at=started_at,
                completed_at=completed_at,
            ),
            artifacts,
            errors,
        )
    return (
        PhaseState(
            status=PhaseStatus.succeeded,
            message=f"persisted {len(artifacts)} source record(s) with full fact logs",
            started_at=started_at,
            completed_at=completed_at,
        ),
        artifacts,
        {},
    )


def _build_mapping_phase(
    *,
    extraction_failed: bool,
    successful_extractions: list[_SuccessfulExtraction],
    source_artifacts: dict[SupportedSource, _SourceEntityArtifact],
    resolved_target: EntityTarget,
    config: EnrichmentConfig,
    run_id: str,
    project_root: Path,
) -> _MappingPhaseResult:
    started_at = _normalize_now()
    if extraction_failed:
        return _MappingPhaseResult(
            phase=PhaseState(
                status=PhaseStatus.skipped,
                message="skipped because extraction phase failed",
                started_at=started_at,
                completed_at=_normalize_now(),
            )
        )

    entity_kind = _resolve_entity_kind(resolved_target)
    if entity_kind == "source":
        return _MappingPhaseResult(
            phase=PhaseState(
                status=PhaseStatus.pending,
                message="mapping skipped for source target",
                started_at=started_at,
                completed_at=_normalize_now(),
            )
        )

    if entity_kind == "org":
        try:
            mapping = _map_organization_facts(
                successful_extractions=successful_extractions,
                source_artifacts=source_artifacts,
                resolved_target=resolved_target,
                config=config,
                project_root=project_root,
            )
        except Exception as exc:
            return _MappingPhaseResult(
                phase=PhaseState(
                    status=PhaseStatus.failed,
                    message=f"organization mapping failed: {exc}",
                    started_at=started_at,
                    completed_at=_normalize_now(),
                )
            )

        if mapping.promoted_fact_count == 0:
            message = (
                "no organization facts met minimum confidence "
                f"'{config.confidence_policy.minimum_promotion_level.value}' for promotion"
            )
        else:
            mapping_count = (
                len(mapping.fact_source_summary.mappings)
                if mapping.fact_source_summary is not None
                else 0
            )
            message = (
                f"promoted {mapping.promoted_fact_count} organization fact(s) into "
                f"{mapping.organization_index_path}; "
                f"frontmatter fields updated: {len(mapping.frontmatter_fields_updated)}; "
                f"known-people entries added: {mapping.known_people_entries_added}; "
                f"fact-to-source mappings: {mapping_count}"
            )
        return _MappingPhaseResult(
            phase=PhaseState(
                status=PhaseStatus.succeeded,
                message=message,
                started_at=started_at,
                completed_at=_normalize_now(),
            ),
            fact_to_source_mappings=(
                [mapping.fact_source_summary]
                if mapping.fact_source_summary is not None
                else []
            ),
        )

    try:
        mapping = _map_person_facts(
            successful_extractions=successful_extractions,
            source_artifacts=source_artifacts,
            resolved_target=resolved_target,
            config=config,
            run_id=run_id,
            project_root=project_root,
        )
    except Exception as exc:
        return _MappingPhaseResult(
            phase=PhaseState(
                status=PhaseStatus.failed,
                message=f"person mapping failed: {exc}",
                started_at=started_at,
                completed_at=_normalize_now(),
            )
        )

    if mapping.promoted_fact_count == 0:
        message = (
            "no person facts met minimum confidence "
            f"'{config.confidence_policy.minimum_promotion_level.value}' for promotion"
        )
    else:
        mapping_count = (
            len(mapping.fact_source_summary.mappings)
            if mapping.fact_source_summary is not None
            else 0
        )
        message = (
            f"promoted {mapping.promoted_fact_count} person fact(s) into {mapping.person_index_path}; "
            f"frontmatter fields updated: {len(mapping.frontmatter_fields_updated)}; "
            f"employment rows appended: {mapping.employment_rows_added}; "
            f"fact-to-source mappings: {mapping_count}"
        )

    return _MappingPhaseResult(
        phase=PhaseState(
            status=PhaseStatus.succeeded,
            message=message,
            started_at=started_at,
            completed_at=_normalize_now(),
        ),
        fact_to_source_mappings=(
            [mapping.fact_source_summary]
            if mapping.fact_source_summary is not None
            else []
        ),
    )


def _resolve_entity_kind(target: EntityTarget) -> str | None:
    entity_ref = target.entity_ref.lower()
    token_match = _ENTITY_REF_TOKEN_RE.fullmatch(entity_ref)
    if token_match is not None:
        return token_match.group("kind")
    if entity_ref.startswith("data/person/") or "/person@" in entity_ref:
        return "person"
    if entity_ref.startswith("data/org/") or "/org@" in entity_ref:
        return "org"
    if entity_ref.startswith("data/source/") or "/source@" in entity_ref:
        return "source"
    return None


def _map_person_facts(
    *,
    successful_extractions: list[_SuccessfulExtraction],
    source_artifacts: dict[SupportedSource, _SourceEntityArtifact],
    resolved_target: EntityTarget,
    config: EnrichmentConfig,
    run_id: str,
    project_root: Path,
) -> _PersonMappingResult:
    person_index_rel = _canonical_person_index_path_for_slug(resolved_target.entity_slug)
    person_index_path = project_root / person_index_rel
    if not person_index_path.exists():
        raise EnrichmentRunError(
            f"canonical person index not found for slug '{resolved_target.entity_slug}' at '{person_index_rel}'"
        )

    promoted_facts = _collect_promoted_facts(
        successful_extractions=successful_extractions,
        source_artifacts=source_artifacts,
        minimum_level=config.confidence_policy.minimum_promotion_level,
    )
    if not promoted_facts:
        return _PersonMappingResult(
            person_index_path=person_index_rel,
            promoted_fact_count=0,
            frontmatter_fields_updated=[],
            employment_rows_added=0,
            fact_source_summary=None,
        )

    _validate_promoted_fact_provenance(promoted_facts=promoted_facts, entity_label="person")

    frontmatter, body = _read_markdown_document(
        index_path=person_index_path,
        entity_label="person",
    )
    fact_source_summary = _build_entity_fact_source_summary(
        entity_kind="person",
        entity_index_path=person_index_rel,
        promoted_facts=promoted_facts,
        index_path=person_index_path,
        project_root=project_root,
    )
    body_with_provenance = _upsert_enrichment_provenance_section(
        body=body,
        promoted_facts=promoted_facts,
        index_path=person_index_path,
        project_root=project_root,
    )
    body_updated = body_with_provenance.strip() != body.strip()
    frontmatter_updated_fields: list[str] = []
    employment_rows_added = 0

    existing_firm = _normalize_text(frontmatter.get("firm"))
    existing_role = _normalize_text(frontmatter.get("role"))

    firm_candidate = _select_best_promoted_fact(
        promoted_facts=promoted_facts,
        attribute_priority=_FIRM_ATTRIBUTE_PRIORITY,
    )
    role_candidate = _select_best_promoted_fact(
        promoted_facts=promoted_facts,
        attribute_priority=_ROLE_ATTRIBUTE_PRIORITY,
    )
    location_candidate = _select_best_promoted_fact(
        promoted_facts=promoted_facts,
        attribute_priority=_LOCATION_ATTRIBUTE_PRIORITY,
    )

    current_candidates_used: list[_PromotedFact] = []
    if firm_candidate is not None:
        if _set_frontmatter_text(frontmatter, key="firm", value=firm_candidate.value):
            frontmatter_updated_fields.append("firm")
            current_candidates_used.append(firm_candidate)
    if role_candidate is not None:
        if _set_frontmatter_text(frontmatter, key="role", value=role_candidate.value):
            frontmatter_updated_fields.append("role")
            current_candidates_used.append(role_candidate)
    if location_candidate is not None:
        if _set_frontmatter_text(frontmatter, key="location", value=location_candidate.value):
            frontmatter_updated_fields.append("location")

    if frontmatter_updated_fields:
        latest_promoted_at = max(fact.retrieved_at for fact in promoted_facts)
        _set_frontmatter_text(
            frontmatter,
            key="updated-at",
            value=latest_promoted_at.date().isoformat(),
        )

    if current_candidates_used and existing_firm and existing_role:
        archived_on = max(candidate.retrieved_at for candidate in current_candidates_used).date().isoformat()
        employment_rows_added += _append_prior_current_role(
            person_index_rel=person_index_rel,
            person_index_path=person_index_path,
            prior_firm=existing_firm,
            prior_role=existing_role,
            archived_on=archived_on,
            run_id=run_id,
        )

    if frontmatter_updated_fields or body_updated:
        person_index_path.write_text(
            _render_markdown(frontmatter=frontmatter, body=body_with_provenance),
            encoding="utf-8",
        )

    return _PersonMappingResult(
        person_index_path=person_index_rel,
        promoted_fact_count=len(promoted_facts),
        frontmatter_fields_updated=frontmatter_updated_fields,
        employment_rows_added=employment_rows_added,
        fact_source_summary=fact_source_summary,
    )


def _canonical_person_index_path_for_slug(slug: str) -> str:
    shard = shard_for_slug(slug)
    return f"data/person/{shard}/person@{slug}/index.md"


def _canonical_org_index_path_for_slug(slug: str) -> str:
    shard = shard_for_slug(slug)
    return f"data/org/{shard}/org@{slug}/index.md"


def _map_organization_facts(
    *,
    successful_extractions: list[_SuccessfulExtraction],
    source_artifacts: dict[SupportedSource, _SourceEntityArtifact],
    resolved_target: EntityTarget,
    config: EnrichmentConfig,
    project_root: Path,
) -> _OrganizationMappingResult:
    organization_index_rel = _canonical_org_index_path_for_slug(resolved_target.entity_slug)
    organization_index_path = project_root / organization_index_rel
    if not organization_index_path.exists():
        raise EnrichmentRunError(
            "canonical organization index not found for slug "
            f"'{resolved_target.entity_slug}' at '{organization_index_rel}'"
        )

    promoted_facts = _collect_promoted_facts(
        successful_extractions=successful_extractions,
        source_artifacts=source_artifacts,
        minimum_level=config.confidence_policy.minimum_promotion_level,
    )
    if not promoted_facts:
        return _OrganizationMappingResult(
            organization_index_path=organization_index_rel,
            promoted_fact_count=0,
            frontmatter_fields_updated=[],
            known_people_entries_added=0,
            fact_source_summary=None,
        )

    _validate_promoted_fact_provenance(promoted_facts=promoted_facts, entity_label="organization")

    frontmatter, body = _read_markdown_document(
        index_path=organization_index_path,
        entity_label="organization",
    )
    fact_source_summary = _build_entity_fact_source_summary(
        entity_kind="org",
        entity_index_path=organization_index_rel,
        promoted_facts=promoted_facts,
        index_path=organization_index_path,
        project_root=project_root,
    )
    body_with_provenance = _upsert_enrichment_provenance_section(
        body=body,
        promoted_facts=promoted_facts,
        index_path=organization_index_path,
        project_root=project_root,
    )
    body_updated = body_with_provenance.strip() != body.strip()
    frontmatter_updated_fields: list[str] = []

    org_name_candidate = _select_best_promoted_fact(
        promoted_facts=promoted_facts,
        attribute_priority=_ORG_NAME_ATTRIBUTE_PRIORITY,
    )
    website_candidate = _select_best_promoted_fact(
        promoted_facts=promoted_facts,
        attribute_priority=_ORG_WEBSITE_ATTRIBUTE_PRIORITY,
    )
    hq_location_candidate = _select_best_promoted_fact(
        promoted_facts=promoted_facts,
        attribute_priority=_ORG_HQ_LOCATION_ATTRIBUTE_PRIORITY,
    )
    thesis_candidate = _select_best_promoted_fact(
        promoted_facts=promoted_facts,
        attribute_priority=_ORG_THESIS_ATTRIBUTE_PRIORITY,
    )

    if org_name_candidate is not None:
        if _set_frontmatter_text(frontmatter, key="org", value=org_name_candidate.value):
            frontmatter_updated_fields.append("org")
    if website_candidate is not None:
        if _set_frontmatter_text(frontmatter, key="website", value=website_candidate.value):
            frontmatter_updated_fields.append("website")
    if hq_location_candidate is not None:
        if _set_frontmatter_text(frontmatter, key="hq-location", value=hq_location_candidate.value):
            frontmatter_updated_fields.append("hq-location")
    if thesis_candidate is not None:
        if _set_frontmatter_text(frontmatter, key="thesis", value=thesis_candidate.value):
            frontmatter_updated_fields.append("thesis")

    known_people_entries_added = _merge_known_people_entries(
        frontmatter=frontmatter,
        promoted_facts=promoted_facts,
        organization_index_path=organization_index_path,
        project_root=project_root,
    )
    if known_people_entries_added > 0:
        frontmatter_updated_fields.append("known-people")

    if frontmatter_updated_fields or body_updated:
        latest_promoted_at = max(fact.retrieved_at for fact in promoted_facts).date().isoformat()
        _set_frontmatter_text(frontmatter, key="updated-at", value=latest_promoted_at)
        _set_frontmatter_text(frontmatter, key="last-updated-from-source", value=latest_promoted_at)
        organization_index_path.write_text(
            _render_markdown(frontmatter=frontmatter, body=body_with_provenance),
            encoding="utf-8",
        )

    return _OrganizationMappingResult(
        organization_index_path=organization_index_rel,
        promoted_fact_count=len(promoted_facts),
        frontmatter_fields_updated=frontmatter_updated_fields,
        known_people_entries_added=known_people_entries_added,
        fact_source_summary=fact_source_summary,
    )


def _collect_promoted_facts(
    *,
    successful_extractions: list[_SuccessfulExtraction],
    source_artifacts: dict[SupportedSource, _SourceEntityArtifact],
    minimum_level: ConfidenceLevel,
) -> list[_PromotedFact]:
    promoted: list[_PromotedFact] = []
    for extraction in sorted(successful_extractions, key=lambda item: item.source.value):
        source_artifact = source_artifacts.get(extraction.source)
        source_entity_ref = source_artifact.source_entity_ref if source_artifact is not None else None
        source_entity_path = source_artifact.source_entity_path if source_artifact is not None else None
        source_citation_key = (
            _citation_key_from_source_entity_ref(source_entity_ref)
            if source_entity_ref is not None
            else None
        )
        for fact in extraction.normalize_result.facts:
            if not _confidence_meets_threshold(fact.confidence, minimum_level):
                continue
            promoted.append(
                _to_promoted_fact(
                    source=extraction.source,
                    source_entity_ref=source_entity_ref,
                    source_entity_path=source_entity_path,
                    source_citation_key=source_citation_key,
                    fact=fact,
                )
            )
    return promoted


def _to_promoted_fact(
    *,
    source: SupportedSource,
    source_entity_ref: str | None,
    source_entity_path: str | None,
    source_citation_key: str | None,
    fact: NormalizedFact,
) -> _PromotedFact:
    return _PromotedFact(
        source=source,
        source_entity_ref=source_entity_ref,
        source_entity_path=source_entity_path,
        source_citation_key=source_citation_key,
        attribute=fact.attribute.strip().lower(),
        value=fact.value.strip(),
        confidence=fact.confidence,
        source_url=fact.source_url,
        retrieved_at=_normalize_now(fact.retrieved_at),
        metadata=dict(fact.metadata),
    )


def _citation_key_from_source_entity_ref(source_entity_ref: str) -> str | None:
    _, marker, tail = source_entity_ref.partition("source@")
    if not marker:
        return None
    citation_key = tail.strip().lower()
    if _SLUG_RE.fullmatch(citation_key) is None:
        return None
    return citation_key


def _validate_promoted_fact_provenance(*, promoted_facts: list[_PromotedFact], entity_label: str) -> None:
    for fact in promoted_facts:
        if fact.source_entity_ref is None:
            raise PromotedFactProvenanceError(
                entity_label=entity_label,
                attribute=fact.attribute,
                source=fact.source,
                details="missing source entity reference",
            )
        if fact.source_entity_path is None:
            raise PromotedFactProvenanceError(
                entity_label=entity_label,
                attribute=fact.attribute,
                source=fact.source,
                details="missing source entity path",
            )
        citation_key = fact.source_citation_key or _citation_key_from_source_entity_ref(fact.source_entity_ref)
        if citation_key is None:
            raise PromotedFactProvenanceError(
                entity_label=entity_label,
                attribute=fact.attribute,
                source=fact.source,
                details=f"invalid source entity reference '{fact.source_entity_ref}'",
            )
        fact.source_citation_key = citation_key


def _build_entity_fact_source_summary(
    *,
    entity_kind: str,
    entity_index_path: str,
    promoted_facts: list[_PromotedFact],
    index_path: Path,
    project_root: Path,
) -> EntityFactSourceSummary:
    mappings = sorted(
        (
            _build_fact_source_mapping(
                fact=fact,
                entity_kind=entity_kind,
                index_path=index_path,
                project_root=project_root,
            )
            for fact in promoted_facts
        ),
        key=lambda mapping: (
            mapping.attribute,
            mapping.value,
            mapping.confidence.value,
            mapping.source_identifier.value,
            mapping.source_citation_key,
            mapping.retrieved_at,
            mapping.source_url,
            mapping.location_pointer or "",
        ),
    )
    return EntityFactSourceSummary(
        entity_kind=entity_kind,
        entity_ref=_entity_ref_from_index_path(entity_index_path),
        entity_index_path=entity_index_path,
        mappings=mappings,
    )


def _build_fact_source_mapping(
    *,
    fact: _PromotedFact,
    entity_kind: str,
    index_path: Path,
    project_root: Path,
) -> FactSourceMapping:
    if fact.source_entity_ref is None:
        raise PromotedFactProvenanceError(
            entity_label=entity_kind,
            attribute=fact.attribute,
            source=fact.source,
            details="missing source entity reference",
        )
    if fact.source_entity_path is None:
        raise PromotedFactProvenanceError(
            entity_label=entity_kind,
            attribute=fact.attribute,
            source=fact.source,
            details="missing source entity path",
        )
    citation_key = fact.source_citation_key or _citation_key_from_source_entity_ref(fact.source_entity_ref)
    if citation_key is None:
        raise PromotedFactProvenanceError(
            entity_label=entity_kind,
            attribute=fact.attribute,
            source=fact.source,
            details=f"invalid source entity reference '{fact.source_entity_ref}'",
        )
    source_entity_index_path = project_root / fact.source_entity_path
    if not source_entity_index_path.exists():
        relative_path = source_entity_index_path.relative_to(project_root).as_posix()
        raise PromotedFactProvenanceError(
            entity_label=entity_kind,
            attribute=fact.attribute,
            source=fact.source,
            details=f"source entity path does not exist: {relative_path}",
        )
    return FactSourceMapping(
        attribute=fact.attribute,
        value=fact.value,
        confidence=fact.confidence,
        source_identifier=fact.source,
        source_entity_ref=fact.source_entity_ref,
        source_entity_path=fact.source_entity_path,
        source_citation_key=citation_key,
        source_url=fact.source_url,
        retrieved_at=fact.retrieved_at,
        location_pointer=_provenance_location_pointer(fact),
    )


def _entity_ref_from_index_path(index_path: str) -> str:
    if not index_path.startswith("data/") or not index_path.endswith("/index.md"):
        raise EnrichmentRunError(f"invalid canonical entity index path '{index_path}'")
    entity_rel = index_path[len("data/") : -len("/index.md")]
    try:
        return validate_entity_rel_path(entity_rel)
    except ValueError as exc:
        raise EnrichmentRunError(f"invalid canonical entity index path '{index_path}': {exc}") from exc


def _provenance_location_pointer(fact: _PromotedFact) -> str | None:
    return _normalize_text(_metadata_value(fact.metadata, *_PROVENANCE_POINTER_METADATA_KEYS))


def _upsert_enrichment_provenance_section(
    *,
    body: str,
    promoted_facts: list[_PromotedFact],
    index_path: Path,
    project_root: Path,
) -> str:
    section = _render_enrichment_provenance_section(
        promoted_facts=promoted_facts,
        index_path=index_path,
        project_root=project_root,
    )
    body_without_section = _ENRICHMENT_PROVENANCE_SECTION_RE.sub("\n\n", body).strip()
    if not body_without_section:
        return section
    return f"{body_without_section}\n\n{section}"


def _render_enrichment_provenance_section(
    *,
    promoted_facts: list[_PromotedFact],
    index_path: Path,
    project_root: Path,
) -> str:
    lines = [
        "## Enrichment Provenance",
        "",
        "<!-- enrichment-provenance:start -->",
        "Promoted facts mapped by this enrichment run. Each citation key resolves to a KB source entity.",
        "",
    ]
    for fact in sorted(
        promoted_facts,
        key=lambda item: (
            item.attribute,
            item.value,
            item.confidence.value,
            item.source.value,
            item.source_citation_key or "",
            item.retrieved_at,
            item.source_url,
        ),
    ):
        if fact.source_entity_path is None or fact.source_citation_key is None:
            continue
        source_entity_index_path = project_root / fact.source_entity_path
        source_link = _relative_link(
            from_dir=index_path.parent,
            target_path=source_entity_index_path,
        )
        retrieved_on = fact.retrieved_at.date().isoformat()
        pointer = _provenance_location_pointer(fact)
        line = (
            f"- `{fact.attribute}` -> `{fact.value}` via "
            f"[source@{fact.source_citation_key}]({source_link}) "
            f"[^{fact.source_citation_key}] "
            f"(`{fact.source.value}`, retrieved `{retrieved_on}`)"
        )
        if pointer is not None:
            line += f"; pointer `{pointer}`"
        lines.append(line)
    lines.extend(
        [
            "",
            "<!-- enrichment-provenance:end -->",
        ]
    )
    return "\n".join(lines)


def _confidence_meets_threshold(level: ConfidenceLevel, minimum_level: ConfidenceLevel) -> bool:
    return _CONFIDENCE_RANK[level] >= _CONFIDENCE_RANK[minimum_level]


def _select_best_promoted_fact(
    *,
    promoted_facts: list[_PromotedFact],
    attribute_priority: dict[str, int],
) -> _PromotedFact | None:
    candidates = [fact for fact in promoted_facts if fact.attribute in attribute_priority and fact.value]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda fact: (
            _CONFIDENCE_RANK[fact.confidence],
            fact.retrieved_at,
            -attribute_priority[fact.attribute],
            fact.source.value,
            fact.value,
            fact.source_url,
            fact.source_entity_ref or "",
        ),
    )


def _merge_known_people_entries(
    *,
    frontmatter: dict[str, Any],
    promoted_facts: list[_PromotedFact],
    organization_index_path: Path,
    project_root: Path,
) -> int:
    entries_by_ref = {
        entry.person_ref: entry
        for entry in _parse_existing_known_people_entries(
            raw_known_people=frontmatter.get("known-people"),
            organization_index_path=organization_index_path,
            project_root=project_root,
        )
    }
    added_count = 0
    for fact in sorted(
        promoted_facts,
        key=lambda item: (
            item.attribute,
            item.value,
            item.source.value,
            item.retrieved_at,
            item.source_url,
        ),
    ):
        candidate = _known_person_entry_from_fact(
            fact=fact,
            organization_index_path=organization_index_path,
            project_root=project_root,
        )
        if candidate is None:
            continue
        existing = entries_by_ref.get(candidate.person_ref)
        if existing is None:
            entries_by_ref[candidate.person_ref] = candidate
            added_count += 1
            continue
        entries_by_ref[candidate.person_ref] = _merge_known_person_entry(existing=existing, candidate=candidate)

    if not entries_by_ref:
        if isinstance(frontmatter.get("known-people"), list):
            frontmatter["known-people"] = []
        return 0

    ordered_entries = sorted(
        entries_by_ref.values(),
        key=lambda entry: (
            entry.person_ref,
            entry.relationship,
            entry.first_noted_at,
            entry.last_verified_at,
        ),
    )
    frontmatter["known-people"] = [
        _known_person_entry_to_frontmatter(
            entry=entry,
            organization_index_path=organization_index_path,
            project_root=project_root,
        )
        for entry in ordered_entries
    ]
    return added_count


def _parse_existing_known_people_entries(
    *,
    raw_known_people: object,
    organization_index_path: Path,
    project_root: Path,
) -> list[_KnownPersonEntry]:
    if not isinstance(raw_known_people, list):
        return []

    parsed: list[_KnownPersonEntry] = []
    for raw_entry in raw_known_people:
        if not isinstance(raw_entry, Mapping):
            continue
        person_value = _normalize_text(raw_entry.get("person"))
        if person_value is None:
            continue
        person_ref = _resolve_person_ref_from_value(
            person_value,
            organization_index_path=organization_index_path,
            project_root=project_root,
        )
        if person_ref is None:
            continue
        person_name = _extract_person_name_from_value(
            value=person_value,
            fallback_ref=person_ref,
        )
        relationship = _normalize_relationship(raw_entry.get("relationship"), fallback="other")
        relationship_details = _normalize_text(raw_entry.get("relationship-details"))
        relationship_start_date = _normalize_partial_date(raw_entry.get("relationship-start-date"))
        relationship_end_date = _normalize_partial_date(raw_entry.get("relationship-end-date"))
        first_noted_at = _normalize_partial_date(raw_entry.get("first-noted-at"))
        last_verified_at = _normalize_partial_date(raw_entry.get("last-verified-at"))
        if first_noted_at is None and last_verified_at is None:
            default_date = _normalize_now().date().isoformat()
            first_noted_at = default_date
            last_verified_at = default_date
        elif first_noted_at is None:
            first_noted_at = last_verified_at
        elif last_verified_at is None:
            last_verified_at = first_noted_at
        parsed.append(
            _KnownPersonEntry(
                person_ref=person_ref,
                person_name=person_name,
                relationship=relationship,
                relationship_details=relationship_details,
                relationship_start_date=relationship_start_date,
                relationship_end_date=relationship_end_date,
                first_noted_at=first_noted_at,
                last_verified_at=last_verified_at,
            )
        )
    return parsed


def _known_person_entry_from_fact(
    *,
    fact: _PromotedFact,
    organization_index_path: Path,
    project_root: Path,
) -> _KnownPersonEntry | None:
    if fact.attribute not in _ORG_KNOWN_PERSON_ATTRIBUTES:
        return None
    person_ref = _resolve_person_ref_from_fact(
        fact=fact,
        organization_index_path=organization_index_path,
        project_root=project_root,
    )
    if person_ref is None:
        return None

    person_index_path = project_root / "data" / person_ref / "index.md"
    if not person_index_path.exists():
        return None

    person_name = _normalize_text(
        _metadata_value(
            fact.metadata,
            "person_name",
            "person-name",
            "name",
            "full_name",
            "full-name",
        )
    )
    if person_name is None:
        person_name = _extract_person_name_from_value(
            value=fact.value,
            fallback_ref=person_ref,
        )
    relationship_details = _normalize_text(
        _metadata_value(
            fact.metadata,
            "relationship_details",
            "relationship-details",
            "details",
        )
    )
    relationship_start_date = _normalize_partial_date(
        _metadata_value(
            fact.metadata,
            "relationship_start_date",
            "relationship-start-date",
        )
    )
    relationship_end_date = _normalize_partial_date(
        _metadata_value(
            fact.metadata,
            "relationship_end_date",
            "relationship-end-date",
        )
    )
    first_noted_at = _normalize_partial_date(
        _metadata_value(
            fact.metadata,
            "first_noted_at",
            "first-noted-at",
        )
    ) or fact.retrieved_at.date().isoformat()
    last_verified_at = _normalize_partial_date(
        _metadata_value(
            fact.metadata,
            "last_verified_at",
            "last-verified-at",
        )
    ) or fact.retrieved_at.date().isoformat()
    relationship = _relationship_for_fact(fact)
    return _KnownPersonEntry(
        person_ref=person_ref,
        person_name=person_name,
        relationship=relationship,
        relationship_details=relationship_details,
        relationship_start_date=relationship_start_date,
        relationship_end_date=relationship_end_date,
        first_noted_at=first_noted_at,
        last_verified_at=last_verified_at,
    )


def _resolve_person_ref_from_fact(
    *,
    fact: _PromotedFact,
    organization_index_path: Path,
    project_root: Path,
) -> str | None:
    metadata = fact.metadata
    candidates = (
        _metadata_value(
            metadata,
            "person_ref",
            "person-ref",
            "person_entity_ref",
            "person-entity-ref",
        ),
        _metadata_value(
            metadata,
            "person_path",
            "person-path",
            "person_index_path",
            "person-index-path",
        ),
        _metadata_value(metadata, "person_slug", "person-slug"),
        fact.value,
    )
    for candidate in candidates:
        person_ref = _resolve_person_ref_from_value(
            candidate,
            organization_index_path=organization_index_path,
            project_root=project_root,
        )
        if person_ref is not None:
            return person_ref
    return None


def _resolve_person_ref_from_value(
    value: object,
    *,
    organization_index_path: Path,
    project_root: Path,
) -> str | None:
    text = _normalize_text(value)
    if text is None:
        return None

    link_match = _MARKDOWN_LINK_RE.match(text)
    if link_match is not None:
        return _resolve_person_ref_from_value(
            link_match.group("target"),
            organization_index_path=organization_index_path,
            project_root=project_root,
        )

    if "://" in text:
        return None

    normalized = text.replace("\\", "/")
    try:
        entity_ref = validate_entity_rel_path(normalized)
    except ValueError:
        entity_ref = None
    if entity_ref is not None and entity_ref.startswith("person/"):
        return entity_ref

    if normalized.startswith("data/person/") and normalized.endswith("/index.md"):
        rel_ref = normalized[len("data/") : -len("/index.md")]
        try:
            entity_ref = validate_entity_rel_path(rel_ref)
        except ValueError:
            return None
        if entity_ref.startswith("person/"):
            return entity_ref
        return None

    if _SLUG_RE.fullmatch(normalized) is not None:
        slug = normalized.lower()
        return f"person/{shard_for_slug(slug)}/person@{slug}"

    path_candidate = Path(normalized)
    if path_candidate.is_absolute():
        try:
            relative = path_candidate.resolve().relative_to(project_root).as_posix()
        except ValueError:
            return None
        return _resolve_person_ref_from_value(
            relative,
            organization_index_path=organization_index_path,
            project_root=project_root,
        )

    if normalized.startswith("./") or normalized.startswith("../"):
        resolved_path = (organization_index_path.parent / normalized).resolve()
        try:
            relative = resolved_path.relative_to(project_root).as_posix()
        except ValueError:
            return None
        return _resolve_person_ref_from_value(
            relative,
            organization_index_path=organization_index_path,
            project_root=project_root,
        )
    return None


def _extract_person_name_from_value(*, value: object, fallback_ref: str) -> str:
    text = _normalize_text(value)
    if text is None:
        return _person_name_from_ref(fallback_ref)
    match = _MARKDOWN_LINK_RE.match(text)
    if match is not None:
        label = _normalize_text(match.group("label"))
        if label is not None:
            return label
    if _SLUG_RE.fullmatch(text) is not None:
        return _person_name_from_ref(fallback_ref)
    if text.startswith("person/") or text.startswith("data/person/"):
        return _person_name_from_ref(fallback_ref)
    return text


def _person_name_from_ref(person_ref: str) -> str:
    _, _, tail = person_ref.partition("person@")
    slug = tail.strip()
    if not slug:
        return "Unknown Person"
    return " ".join(token.capitalize() for token in slug.split("-"))


def _relationship_for_fact(fact: _PromotedFact) -> str:
    explicit = _normalize_relationship(_metadata_value(fact.metadata, "relationship"), fallback=None)
    if explicit is not None:
        return explicit
    return _RELATIONSHIP_DEFAULT_BY_ATTRIBUTE.get(fact.attribute, "other")


def _normalize_relationship(value: object, *, fallback: str | None) -> str | None:
    text = _normalize_text(value)
    if text is None:
        return fallback
    normalized = text.lower().replace("-", "_").replace(" ", "_")
    if normalized in _KNOWN_PEOPLE_RELATIONSHIPS:
        return normalized
    return fallback


def _merge_known_person_entry(
    *,
    existing: _KnownPersonEntry,
    candidate: _KnownPersonEntry,
) -> _KnownPersonEntry:
    return _KnownPersonEntry(
        person_ref=existing.person_ref,
        person_name=existing.person_name or candidate.person_name,
        relationship=existing.relationship if existing.relationship != "other" else candidate.relationship,
        relationship_details=existing.relationship_details or candidate.relationship_details,
        relationship_start_date=existing.relationship_start_date or candidate.relationship_start_date,
        relationship_end_date=existing.relationship_end_date or candidate.relationship_end_date,
        first_noted_at=_min_partial_date(existing.first_noted_at, candidate.first_noted_at),
        last_verified_at=_max_partial_date(existing.last_verified_at, candidate.last_verified_at),
    )


def _known_person_entry_to_frontmatter(
    *,
    entry: _KnownPersonEntry,
    organization_index_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    person_index_path = project_root / "data" / entry.person_ref / "index.md"
    person_link_target = _relative_link(
        from_dir=organization_index_path.parent,
        target_path=person_index_path,
    )
    return {
        "person": f"[{entry.person_name}]({person_link_target})",
        "relationship": entry.relationship,
        "relationship-details": entry.relationship_details,
        "relationship-start-date": entry.relationship_start_date,
        "relationship-end-date": entry.relationship_end_date,
        "first-noted-at": entry.first_noted_at,
        "last-verified-at": entry.last_verified_at,
    }


def _metadata_value(metadata: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in metadata:
            return metadata[key]
    return None


def _normalize_partial_date(value: object) -> str | None:
    text = _normalize_text(value)
    if text is None:
        return None
    try:
        return parse_partial_date(text)
    except ValueError:
        return None


def _partial_date_sort_key(value: str) -> tuple[int, int, int]:
    parts = [int(part) for part in value.split("-")]
    year = parts[0]
    month = parts[1] if len(parts) > 1 else 0
    day = parts[2] if len(parts) > 2 else 0
    return (year, month, day)


def _min_partial_date(left: str, right: str) -> str:
    if _partial_date_sort_key(left) <= _partial_date_sort_key(right):
        return left
    return right


def _max_partial_date(left: str, right: str) -> str:
    if _partial_date_sort_key(left) >= _partial_date_sort_key(right):
        return left
    return right


def _read_markdown_document(*, index_path: Path, entity_label: str) -> tuple[dict[str, Any], str]:
    markdown = index_path.read_text(encoding="utf-8")
    match = _FRONTMATTER_BLOCK_RE.match(markdown)
    if match is None:
        raise EnrichmentRunError(
            f"{entity_label} index is missing YAML frontmatter: {index_path.as_posix()}"
        )
    frontmatter_raw = yaml.safe_load(match.group("frontmatter"))
    if frontmatter_raw is None:
        frontmatter: dict[str, Any] = {}
    elif isinstance(frontmatter_raw, dict):
        frontmatter = dict(frontmatter_raw)
    else:
        raise EnrichmentRunError(
            f"{entity_label} index frontmatter must be a mapping: {index_path.as_posix()}"
        )
    return frontmatter, match.group("body") or ""


def _normalize_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
    else:
        text = str(value).strip()
    return text or None


def _set_frontmatter_text(frontmatter: dict[str, Any], *, key: str, value: str) -> bool:
    normalized = _normalize_text(value)
    if normalized is None:
        return False
    current = _normalize_text(frontmatter.get(key))
    if current == normalized:
        return False
    frontmatter[key] = normalized
    return True


def _append_prior_current_role(
    *,
    person_index_rel: str,
    person_index_path: Path,
    prior_firm: str,
    prior_role: str,
    archived_on: str,
    run_id: str,
) -> int:
    employment_path = person_index_path.parent / "employment-history.jsonl"
    existing_rows = _load_employment_history_rows(path=employment_path)
    next_row = EmploymentHistoryRow(
        id=_next_employment_row_id(existing_rows),
        period=f"Before {archived_on}",
        organization=prior_firm,
        organization_ref=_guess_organization_ref(existing_rows=existing_rows, organization=prior_firm),
        role=prior_role,
        notes=f"Archived prior current role during enrichment run {run_id}.",
        source_path=person_index_rel,
        source_section="employment_history_table",
        source_row=_next_source_row_number(existing_rows),
    )
    _append_jsonl_row(
        path=employment_path,
        payload=next_row.model_dump(mode="json", exclude_none=True),
    )
    return 1


def _load_employment_history_rows(*, path: Path) -> list[EmploymentHistoryRow]:
    if not path.exists():
        return []
    rows: list[EmploymentHistoryRow] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise EnrichmentRunError(
                f"invalid JSON in {path.as_posix()} line {line_no}: {exc.msg}"
            ) from exc
        try:
            rows.append(EmploymentHistoryRow.model_validate(payload))
        except ValidationError as exc:
            raise EnrichmentRunError(
                f"invalid employment row in {path.as_posix()} line {line_no}: {exc.errors()[0]['msg']}"
            ) from exc
    return rows


def _next_employment_row_id(rows: list[EmploymentHistoryRow]) -> str:
    max_index = 0
    for row in rows:
        match = _EMPLOYMENT_ID_RE.match(row.id)
        if match is None:
            continue
        max_index = max(max_index, int(match.group("index")))
    return f"employment-{max_index + 1:03d}"


def _next_source_row_number(rows: list[EmploymentHistoryRow]) -> int:
    max_source_row = 0
    for row in rows:
        if row.source_row is not None:
            max_source_row = max(max_source_row, row.source_row)
    if max_source_row > 0:
        return max_source_row + 1
    return len(rows) + 1


def _guess_organization_ref(*, existing_rows: list[EmploymentHistoryRow], organization: str) -> str | None:
    normalized = organization.strip().lower()
    for row in reversed(existing_rows):
        if row.organization.strip().lower() != normalized:
            continue
        if row.organization_ref:
            return row.organization_ref
    return None


def _append_jsonl_row(*, path: Path, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, sort_keys=True)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
        return
    existing = path.read_text(encoding="utf-8")
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    path.write_text(existing + prefix + rendered + "\n", encoding="utf-8")


def _build_validation_phase(
    *,
    extraction_failed: bool,
    mapping_phase_status: PhaseStatus,
    project_root: Path,
    run_started_at: datetime,
) -> PhaseState:
    started_at = _normalize_now()
    if extraction_failed:
        return PhaseState(
            status=PhaseStatus.skipped,
            message="skipped because extraction phase failed",
            started_at=started_at,
            completed_at=_normalize_now(),
        )
    if mapping_phase_status == PhaseStatus.failed:
        return PhaseState(
            status=PhaseStatus.skipped,
            message="skipped because mapping phase failed",
            started_at=started_at,
            completed_at=_normalize_now(),
        )

    initial_validation = _run_validate_changed(project_root=project_root)
    if bool(initial_validation.get("ok")):
        return PhaseState(
            status=PhaseStatus.succeeded,
            message="validate-changed checks passed",
            started_at=started_at,
            completed_at=_normalize_now(),
        )

    remediation_steps = _run_validation_auto_remediation(
        project_root=project_root,
        as_of=run_started_at.date().isoformat(),
    )
    retried_validation = _run_validate_changed(project_root=project_root)
    if bool(retried_validation.get("ok")):
        return PhaseState(
            status=PhaseStatus.succeeded,
            message=(
                "validate-changed checks passed after one automated remediation attempt "
                f"({_summarize_remediation_steps(remediation_steps)})"
            ),
            started_at=started_at,
            completed_at=_normalize_now(),
        )

    blocked_message = _build_validation_blocked_message(
        validation_result=retried_validation,
        remediation_steps=remediation_steps,
    )
    return PhaseState(
        status=PhaseStatus.failed,
        message=blocked_message,
        started_at=started_at,
        completed_at=_normalize_now(),
    )


def _run_validate_changed(*, project_root: Path) -> dict[str, Any]:
    data_root = infer_data_root(project_root, None)
    scope_paths = collect_changed_paths(project_root, data_root)
    return run_validation(
        project_root=project_root,
        data_root=data_root,
        scope_paths=scope_paths,
        scope_label="changed",
    )


def _run_validation_auto_remediation(*, project_root: Path, as_of: str) -> list[dict[str, Any]]:
    data_root = infer_data_root(project_root, None)
    return [
        _run_remediation_step(
            step_name="derive-employment-edges",
            action=lambda: derive_employment_edges(
                project_root=project_root,
                data_root=data_root,
                as_of=as_of,
            ),
        ),
        _run_remediation_step(
            step_name="derive-citation-edges",
            action=lambda: derive_citation_edges(
                project_root=project_root,
                data_root=data_root,
                as_of=as_of,
            ),
        ),
        _run_remediation_step(
            step_name="sync-edge-backlinks",
            action=lambda: sync_edge_backlinks(
                project_root=project_root,
                data_root=data_root,
            ),
        ),
    ]


def _run_remediation_step(
    *,
    step_name: str,
    action: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    try:
        result = action()
    except Exception as exc:  # pragma: no cover - defensive fallback around remediation commands.
        return {
            "step": step_name,
            "ok": False,
            "issue_count": 1,
            "error": str(exc) or exc.__class__.__name__,
        }

    issue_count_raw = result.get("issue_count")
    issue_count = issue_count_raw if isinstance(issue_count_raw, int) else 0
    return {
        "step": step_name,
        "ok": bool(result.get("ok")),
        "issue_count": issue_count,
    }


def _summarize_remediation_steps(steps: list[dict[str, Any]]) -> str:
    summaries: list[str] = []
    for step in steps:
        step_name = str(step.get("step") or "unknown-step")
        if bool(step.get("ok")):
            summaries.append(f"{step_name}:ok")
            continue
        issue_count_raw = step.get("issue_count")
        issue_count = issue_count_raw if isinstance(issue_count_raw, int) else 0
        summaries.append(f"{step_name}:{issue_count} issue(s)")
    return ", ".join(summaries)


def _build_validation_blocked_message(
    *,
    validation_result: Mapping[str, Any],
    remediation_steps: list[dict[str, Any]],
) -> str:
    check_codes, affected_files = _extract_validation_error_details(validation_result)
    checks_fragment = _format_limited_list(check_codes)
    files_fragment = _format_limited_list(affected_files)
    remediation_fragment = _summarize_remediation_steps(remediation_steps)
    return (
        "validation blocked after one automated remediation attempt; human intervention required. "
        f"failing checks: {checks_fragment}. "
        f"affected files: {files_fragment}. "
        f"remediation: {remediation_fragment}"
    )


def _extract_validation_error_details(validation_result: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    check_codes: set[str] = set()
    affected_files: set[str] = set()
    errors_raw = validation_result.get("errors")
    if isinstance(errors_raw, list):
        for entry in errors_raw:
            if not isinstance(entry, Mapping):
                continue
            code = entry.get("code")
            if isinstance(code, str) and code.strip():
                check_codes.add(code.strip())
            path = entry.get("path")
            if isinstance(path, str) and path.strip():
                affected_files.add(path.strip())
    return sorted(check_codes), sorted(affected_files)


def _format_limited_list(values: list[str], *, limit: int = 6) -> str:
    if not values:
        return "none"
    if len(values) <= limit:
        return ", ".join(values)
    return f"{', '.join(values[:limit])}, +{len(values) - limit} more"


def _write_run_report(report: EnrichmentRunReport, *, project_root: Path) -> None:
    report_path = project_root.joinpath(report.report_path)
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise RunReportWriteError(report_path=report.report_path, details=str(exc)) from exc


def _normalize_sources(selected_sources: Iterable[SupportedSource | str] | None) -> tuple[SupportedSource, ...]:
    if selected_sources is None:
        return tuple(source for source in SupportedSource)

    normalized: list[SupportedSource] = []
    seen: set[SupportedSource] = set()
    for source in selected_sources:
        try:
            resolved = source if isinstance(source, SupportedSource) else SupportedSource(str(source).strip())
        except ValueError as exc:
            raise EnrichmentRunError(f"unsupported enrichment source '{source}'") from exc
        if resolved in seen:
            continue
        seen.add(resolved)
        normalized.append(resolved)

    if not normalized:
        return tuple(source for source in SupportedSource)
    return tuple(normalized)


def _build_snapshot_output_path(
    *,
    source: SupportedSource,
    config: EnrichmentConfig,
    run_id: str,
    entity_slug: str,
) -> str:
    source_evidence_path = config.sources[source].evidence_path.rstrip("/")
    return f"{source_evidence_path}/{run_id}/{entity_slug}.json"


def _write_source_entity_record(
    *,
    source: SupportedSource,
    fetch_result: FetchResult,
    normalize_result: NormalizeResult,
    snapshot_result: SnapshotResult,
    target: EntityTarget,
    run_id: str,
    project_root: Path,
) -> _SourceEntityArtifact:
    source_slug = _build_source_entity_slug(
        source=source,
        entity_slug=target.entity_slug,
        run_id=run_id,
    )
    shard = shard_for_slug(source_slug)
    source_ref = f"source/{shard}/source@{source_slug}"
    source_dir_rel = f"data/source/{shard}/source@{source_slug}"
    source_entity_path = f"{source_dir_rel}/index.md"
    facts_artifact_path = f"{source_dir_rel}/facts.json"

    source_dir = project_root / source_dir_rel
    source_dir.mkdir(parents=True, exist_ok=True)
    edges_dir = source_dir / "edges"
    edges_dir.mkdir(parents=True, exist_ok=True)
    gitkeep = edges_dir / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text("", encoding="utf-8")

    sorted_facts = _serialize_normalized_facts(normalize_result=normalize_result)
    facts_payload = {
        "entity_ref": target.entity_ref,
        "entity_slug": target.entity_slug,
        "facts": sorted_facts,
        "retrieved_at": _normalize_now(fetch_result.retrieved_at).isoformat(),
        "run_id": run_id,
        "snapshot": {
            "content_type": snapshot_result.content_type,
            "path": snapshot_result.snapshot_path,
        },
        "source": source.value,
        "source_ref": source_ref,
        "source_url": fetch_result.source_url,
    }

    facts_path = source_dir / "facts.json"
    try:
        facts_path.write_text(
            json.dumps(facts_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise SourceRecordWriteError(
            source=source,
            path=facts_artifact_path,
            details=str(exc),
        ) from exc

    retrieved_on = _normalize_now(fetch_result.retrieved_at).date().isoformat()
    title = f"Enrichment capture for {target.entity_slug} from {source.value} ({run_id})"
    source_record_payload: dict[str, str] = {
        "id": f"source@{source_slug}",
        "title": title,
        "source-type": SourceType.website.value,
        "citation-key": source_slug,
        "source-path": source_entity_path,
        "source-category": f"citations/enrichment/{_slugify_token(source.value)}",
        "url": fetch_result.source_url,
        "retrieved-at": retrieved_on,
        "citation-text": (
            f"Source: {source.value} enrichment capture for {target.entity_slug} "
            f"({fetch_result.source_url}). Retrieved on {retrieved_on}."
        ),
    }
    if snapshot_result.content_type == "text/html":
        source_record_payload["html-capture-path"] = snapshot_result.snapshot_path
    source_record = SourceRecord.model_validate(source_record_payload)
    source_frontmatter = source_record.model_dump(mode="json", by_alias=True, exclude_none=True)

    snapshot_link = _relative_link(
        from_dir=source_dir,
        target_path=(project_root / snapshot_result.snapshot_path),
    )
    source_body = _render_source_entity_body(
        title=title,
        source_ref=source_ref,
        source=source,
        target=target,
        run_id=run_id,
        source_url=fetch_result.source_url,
        retrieved_at=_normalize_now(fetch_result.retrieved_at).isoformat(),
        snapshot_path=snapshot_result.snapshot_path,
        snapshot_content_type=snapshot_result.content_type,
        snapshot_link=snapshot_link,
        sorted_facts=sorted_facts,
    )
    index_path = source_dir / "index.md"
    try:
        index_path.write_text(
            _render_markdown(frontmatter=source_frontmatter, body=source_body),
            encoding="utf-8",
        )
    except OSError as exc:
        raise SourceRecordWriteError(
            source=source,
            path=source_entity_path,
            details=str(exc),
        ) from exc

    return _SourceEntityArtifact(
        source_entity_ref=source_ref,
        source_entity_path=source_entity_path,
        facts_artifact_path=facts_artifact_path,
    )


def _serialize_normalized_facts(*, normalize_result: NormalizeResult) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for fact in normalize_result.facts:
        metadata_blob = json.dumps(fact.metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        rows.append(
            {
                "attribute": fact.attribute,
                "confidence": fact.confidence.value,
                "metadata": fact.metadata,
                "metadata_blob": metadata_blob,
                "retrieved_at": _normalize_now(fact.retrieved_at).isoformat(),
                "source_url": fact.source_url,
                "value": fact.value,
            }
        )

    rows.sort(
        key=lambda row: (
            str(row["attribute"]),
            str(row["value"]),
            str(row["confidence"]),
            str(row["source_url"]),
            str(row["retrieved_at"]),
            str(row["metadata_blob"]),
        )
    )
    return [
        {
            "attribute": str(row["attribute"]),
            "confidence": str(row["confidence"]),
            "metadata": dict(row["metadata"]) if isinstance(row["metadata"], dict) else {},
            "retrieved_at": str(row["retrieved_at"]),
            "source_url": str(row["source_url"]),
            "value": str(row["value"]),
        }
        for row in rows
    ]


def _render_source_entity_body(
    *,
    title: str,
    source_ref: str,
    source: SupportedSource,
    target: EntityTarget,
    run_id: str,
    source_url: str,
    retrieved_at: str,
    snapshot_path: str,
    snapshot_content_type: str,
    snapshot_link: str,
    sorted_facts: list[dict[str, object]],
) -> str:
    facts_json = json.dumps(sorted_facts, indent=2, sort_keys=True)
    return "\n".join(
        [
            f"# {title}",
            "",
            "Automated enrichment evidence captured by `kb enrich-entity`.",
            "",
            f"- Source entity ref: `{source_ref}`",
            f"- Source adapter: `{source.value}`",
            f"- Entity target: `{target.entity_ref}`",
            f"- Run ID: `{run_id}`",
            f"- Source URL: {source_url}",
            f"- Retrieved at (UTC): `{retrieved_at}`",
            f"- Snapshot artifact: [`{snapshot_path}`]({snapshot_link}) (`{snapshot_content_type}`)",
            "- Structured facts artifact: [`facts.json`](facts.json)",
            "",
            "## Extracted Facts",
            "",
            "```json",
            facts_json,
            "```",
            "",
        ]
    )


def _build_source_entity_slug(*, source: SupportedSource, entity_slug: str, run_id: str) -> str:
    return _slugify_token(f"enrichment-{source.value}-{entity_slug}-{run_id}")


def _slugify_token(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "enrichment-source"


def _relative_link(*, from_dir: Path, target_path: Path) -> str:
    return Path(os.path.relpath(target_path, start=from_dir)).as_posix()


def _render_markdown(*, frontmatter: dict[str, object], body: str) -> str:
    dumped = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).rstrip()
    chunks = [f"---\n{dumped}\n---\n"]
    if body.strip():
        chunks.extend(["", body.strip()])
    return "\n".join(chunks).rstrip() + "\n"


def _build_run_id(now: datetime) -> str:
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    return f"enrich-{timestamp}-{uuid4().hex[:8]}"


def _normalize_now(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(tz=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
