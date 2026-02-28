from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kb.cli import build_parser, run_enrich_entity
from kb.enrichment_config import EnrichmentConfig, SupportedSource
from kb.enrichment_run import (
    EnrichmentRunReport,
    ExtractionPhaseState,
    PhaseState,
    PhaseStatus,
    RunPhaseStates,
    RunStatus,
    EntityTargetResolutionError,
)


def _stub_report(
    *,
    sources: list[SupportedSource],
    status: RunStatus = RunStatus.partial,
    validation_status: PhaseStatus = PhaseStatus.pending,
) -> EnrichmentRunReport:
    now = datetime(2026, 2, 28, 18, 0, tzinfo=UTC)
    return EnrichmentRunReport(
        run_id="enrich-stub-run",
        entity_ref="founder-name",
        entity_slug="founder-name",
        selected_sources=sources,
        status=status,
        started_at=now,
        completed_at=now,
        facts_extracted_total=2,
        report_path=".build/enrichment/reports/latest-run.json",
        phases=RunPhaseStates(
            extraction=ExtractionPhaseState(
                status=PhaseStatus.succeeded,
                message="extraction completed",
                sources=[],
            ),
            source_logging=PhaseState(
                status=PhaseStatus.pending,
                message="pending source logging",
            ),
            mapping=PhaseState(
                status=PhaseStatus.pending,
                message="pending mapping",
            ),
            validation=PhaseState(
                status=validation_status,
                message="stub validation phase",
            ),
            reporting=PhaseState(
                status=PhaseStatus.succeeded,
                message="report written",
            ),
        ),
    )


def test_enrich_entity_parser_accepts_single_entity_with_multiple_sources() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "enrich-entity",
            "founder-name",
            "--source",
            "linkedin.com",
            "--source",
            "skool.com",
        ]
    )

    assert args.command == "enrich-entity"
    assert args.entity == "founder-name"
    assert args.sources == ["linkedin.com", "skool.com"]


def test_enrich_entity_parser_rejects_multiple_entity_arguments() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["enrich-entity", "founder-name", "extra-entity"])


def test_run_enrich_entity_reports_structured_payload(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    observed: dict[str, object] = {}

    def _run_stub(
        entity_target: str,
        *,
        selected_sources,
        config: EnrichmentConfig,
        project_root: Path,
    ) -> EnrichmentRunReport:
        observed["entity_target"] = entity_target
        observed["selected_sources"] = list(selected_sources or [])
        observed["project_root"] = project_root
        assert isinstance(config, EnrichmentConfig)
        return _stub_report(sources=[SupportedSource.linkedin, SupportedSource.skool])

    monkeypatch.setattr("kb.cli.load_enrichment_config_from_env", lambda: EnrichmentConfig())
    monkeypatch.setattr("kb.cli.run_enrichment_for_entity", _run_stub)

    parser = build_parser()
    args = parser.parse_args(
        [
            "enrich-entity",
            "founder-name",
            "--source",
            "linkedin.com",
            "--source",
            "skool.com",
            "--project-root",
            str(tmp_path),
        ]
    )
    status_code = run_enrich_entity(args)

    assert status_code == 0
    assert observed["entity_target"] == "founder-name"
    assert observed["selected_sources"] == ["linkedin.com", "skool.com"]
    assert observed["project_root"] == tmp_path.resolve()

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["status"] == "partial"
    assert payload["phases"]["extraction"]["status"] == "succeeded"
    assert payload["phases"]["validation"]["status"] == "pending"


def test_run_enrich_entity_reports_resolution_error(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("kb.cli.load_enrichment_config_from_env", lambda: EnrichmentConfig())
    monkeypatch.setattr(
        "kb.cli.run_enrichment_for_entity",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            EntityTargetResolutionError(
                entity_target="bad target",
                details="slug must match [a-z0-9][a-z0-9-]*",
            )
        ),
    )

    parser = build_parser()
    args = parser.parse_args(
        [
            "enrich-entity",
            "bad target",
            "--project-root",
            str(tmp_path),
        ]
    )
    status_code = run_enrich_entity(args)

    assert status_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_type"] == "EntityTargetResolutionError"
    assert "bad target" in payload["message"]


def test_run_enrich_entity_reports_blocked_validation_status(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr("kb.cli.load_enrichment_config_from_env", lambda: EnrichmentConfig())
    monkeypatch.setattr(
        "kb.cli.run_enrichment_for_entity",
        lambda *_args, **_kwargs: _stub_report(
            sources=[SupportedSource.linkedin],
            status=RunStatus.blocked,
            validation_status=PhaseStatus.failed,
        ),
    )

    parser = build_parser()
    args = parser.parse_args(
        [
            "enrich-entity",
            "founder-name",
            "--project-root",
            str(tmp_path),
        ]
    )
    status_code = run_enrich_entity(args)

    assert status_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["phases"]["validation"]["status"] == "failed"
