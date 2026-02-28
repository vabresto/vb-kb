from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from uuid import uuid4

from pydantic import Field

from kb.enrichment_adapters import (
    FetchRequest,
    NormalizeRequest,
    SnapshotRequest,
    SourceAdapterError,
    SourceAdapterRegistry,
)
from kb.enrichment_config import EnrichmentConfig, SupportedSource
from kb.enrichment_linkedin_adapter import LinkedInSourceAdapter
from kb.enrichment_skool_adapter import SkoolSourceAdapter
from kb.schemas import KBBaseModel, normalize_path_token

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ENTITY_PATH_SLUG_RE = re.compile(
    r"(?:^|/)(?:person|org|source)@(?P<slug>[a-z0-9][a-z0-9-]*)(?:/index\.md)?$"
)


class RunStatus(str, Enum):
    succeeded = "succeeded"
    partial = "partial"
    failed = "failed"


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

    slug = raw_target.lower()
    if _SLUG_RE.fullmatch(slug) is None:
        raise EntityTargetResolutionError(
            entity_target=entity_target,
            details="slug must match [a-z0-9][a-z0-9-]*",
        )
    return EntityTarget(
        entity_ref=slug,
        entity_slug=slug,
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

    source_logging_phase = _build_source_logging_phase(extraction_failed)
    mapping_phase = _build_mapping_phase(extraction_failed)
    validation_phase = _build_validation_phase(extraction_failed)
    reporting_phase = PhaseState(
        status=PhaseStatus.succeeded,
        message=f"run report persisted to {config.run_report_path}",
        started_at=_normalize_now(),
        completed_at=_normalize_now(),
    )
    completed_at = _normalize_now()

    if extraction_failed:
        run_status = RunStatus.failed
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
    )
    _write_run_report(report, project_root=resolved_root)
    return report


def _build_source_logging_phase(extraction_failed: bool) -> PhaseState:
    if extraction_failed:
        return PhaseState(
            status=PhaseStatus.skipped,
            message="skipped because extraction phase failed",
        )
    return PhaseState(
        status=PhaseStatus.pending,
        message="pending implementation for source fact logging (US-008)",
    )


def _build_mapping_phase(extraction_failed: bool) -> PhaseState:
    if extraction_failed:
        return PhaseState(
            status=PhaseStatus.skipped,
            message="skipped because extraction phase failed",
        )
    return PhaseState(
        status=PhaseStatus.pending,
        message="pending implementation for person/org mapping (US-009/US-010)",
    )


def _build_validation_phase(extraction_failed: bool) -> PhaseState:
    if extraction_failed:
        return PhaseState(
            status=PhaseStatus.skipped,
            message="skipped because extraction phase failed",
        )
    return PhaseState(
        status=PhaseStatus.pending,
        message="pending implementation for validation gating (US-012)",
    )


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


def _build_run_id(now: datetime) -> str:
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    return f"enrich-{timestamp}-{uuid4().hex[:8]}"


def _normalize_now(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(tz=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
