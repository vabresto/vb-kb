from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kb.enrichment_adapters import (
    AuthenticationRequest,
    AuthenticationResult,
    FetchRequest,
    FetchResult,
    NormalizeRequest,
    NormalizeResult,
    NormalizedFact,
    SnapshotRequest,
    SnapshotResult,
    SourceAdapter,
    SourceAdapterError,
    SourceAdapterRegistry,
)
from kb.enrichment_config import ConfidenceLevel, EnrichmentConfig, SupportedSource
from kb.enrichment_run import (
    EntityTargetResolutionError,
    PhaseStatus,
    RunStatus,
    resolve_entity_target,
    run_enrichment_for_entity,
)


class _SuccessfulAdapter(SourceAdapter):
    def __init__(self, source: SupportedSource, *, project_root: Path) -> None:
        self.source = source
        self._project_root = project_root

    def authenticate(self, request: AuthenticationRequest) -> AuthenticationResult:
        return AuthenticationResult(
            authenticated=True,
            used_session_state_path=request.session_state_path,
        )

    def fetch(self, request: FetchRequest) -> FetchResult:
        return FetchResult(
            source_url=f"https://{self.source.value}/entity/{request.entity_slug}",
            retrieved_at=datetime(2026, 2, 28, 17, 0, tzinfo=UTC),
            payload={},
        )

    def normalize(self, request: NormalizeRequest) -> NormalizeResult:
        return NormalizeResult(
            facts=[
                NormalizedFact(
                    attribute="headline",
                    value=f"{self.source.value} headline",
                    confidence=ConfidenceLevel.medium,
                    source_url=request.fetch_result.source_url,
                    retrieved_at=request.fetch_result.retrieved_at,
                    metadata={"adapter": self.source.value},
                )
            ]
        )

    def snapshot(self, request: SnapshotRequest) -> SnapshotResult:
        output_path = self._project_root / request.output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("{}", encoding="utf-8")
        return SnapshotResult(
            snapshot_path=request.output_path,
            content_type="application/json",
        )


class _FailingAdapter(_SuccessfulAdapter):
    def fetch(self, request: FetchRequest) -> FetchResult:
        raise SourceAdapterError(source=self.source, message="simulated extraction failure")


def test_resolve_entity_target_accepts_slug_and_canonical_path() -> None:
    slug_target = resolve_entity_target("founder-name")
    assert slug_target.entity_ref == "founder-name"
    assert slug_target.entity_slug == "founder-name"

    path_target = resolve_entity_target("data/person/fo/person@founder-name/index.md")
    assert path_target.entity_ref == "data/person/fo/person@founder-name/index.md"
    assert path_target.entity_slug == "founder-name"


def test_resolve_entity_target_rejects_invalid_value() -> None:
    with pytest.raises(EntityTargetResolutionError):
        resolve_entity_target("Founder Name")


def test_run_enrichment_for_entity_reports_all_phase_states(tmp_path: Path) -> None:
    config = EnrichmentConfig()
    registry = SourceAdapterRegistry(
        adapters=(
            _SuccessfulAdapter(SupportedSource.linkedin, project_root=tmp_path),
            _SuccessfulAdapter(SupportedSource.skool, project_root=tmp_path),
        )
    )

    report = run_enrichment_for_entity(
        "founder-name",
        selected_sources=[SupportedSource.linkedin, SupportedSource.skool],
        config=config,
        project_root=tmp_path,
        adapter_registry=registry,
        now=datetime(2026, 2, 28, 17, 5, tzinfo=UTC),
        run_id="enrich-test-run",
    )

    assert report.status == RunStatus.partial
    assert report.phases.extraction.status == PhaseStatus.succeeded
    assert report.phases.source_logging.status == PhaseStatus.pending
    assert report.phases.mapping.status == PhaseStatus.pending
    assert report.phases.validation.status == PhaseStatus.pending
    assert report.phases.reporting.status == PhaseStatus.succeeded
    assert report.facts_extracted_total == 2

    source_states = {state.source: state for state in report.phases.extraction.sources}
    assert source_states[SupportedSource.linkedin].status == PhaseStatus.succeeded
    assert source_states[SupportedSource.skool].status == PhaseStatus.succeeded
    assert "enrich-test-run" in str(source_states[SupportedSource.linkedin].snapshot_path)
    assert "enrich-test-run" in str(source_states[SupportedSource.skool].snapshot_path)

    report_path = tmp_path / config.run_report_path
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["phases"]["extraction"]["status"] == "succeeded"
    assert payload["phases"]["source_logging"]["status"] == "pending"
    assert payload["phases"]["mapping"]["status"] == "pending"
    assert payload["phases"]["validation"]["status"] == "pending"
    assert payload["phases"]["reporting"]["status"] == "succeeded"


def test_run_enrichment_for_entity_marks_failed_extraction_phase(tmp_path: Path) -> None:
    config = EnrichmentConfig()
    registry = SourceAdapterRegistry(
        adapters=(
            _FailingAdapter(SupportedSource.linkedin, project_root=tmp_path),
            _SuccessfulAdapter(SupportedSource.skool, project_root=tmp_path),
        )
    )

    report = run_enrichment_for_entity(
        "data/person/fo/person@founder-name/index.md",
        selected_sources=[SupportedSource.linkedin, SupportedSource.skool],
        config=config,
        project_root=tmp_path,
        adapter_registry=registry,
        run_id="enrich-test-failure",
    )

    assert report.status == RunStatus.failed
    assert report.phases.extraction.status == PhaseStatus.failed
    assert report.phases.source_logging.status == PhaseStatus.skipped
    assert report.phases.mapping.status == PhaseStatus.skipped
    assert report.phases.validation.status == PhaseStatus.skipped
    assert report.phases.reporting.status == PhaseStatus.succeeded

    source_states = {state.source: state for state in report.phases.extraction.sources}
    assert source_states[SupportedSource.linkedin].status == PhaseStatus.failed
    assert source_states[SupportedSource.linkedin].error_type == "SourceAdapterError"
    assert source_states[SupportedSource.skool].status == PhaseStatus.succeeded


def test_run_enrichment_for_entity_never_prompts_after_kickoff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = EnrichmentConfig()
    registry = SourceAdapterRegistry(
        adapters=(
            _SuccessfulAdapter(SupportedSource.linkedin, project_root=tmp_path),
        )
    )

    def _input_should_not_be_called(*_args, **_kwargs) -> str:
        raise AssertionError("interactive input was requested")

    monkeypatch.setattr("builtins.input", _input_should_not_be_called)

    report = run_enrichment_for_entity(
        "founder-name",
        selected_sources=[SupportedSource.linkedin],
        config=config,
        project_root=tmp_path,
        adapter_registry=registry,
        run_id="enrich-test-autonomous",
    )

    assert report.status == RunStatus.partial
    assert report.selected_sources == [SupportedSource.linkedin]
