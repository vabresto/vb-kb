from __future__ import annotations

import json
from pathlib import Path

from kb.cli import build_parser, run_export_session, run_import_session
from kb.enrichment_config import EnrichmentConfig, SupportedSource
from kb.enrichment_sessions import InvalidSessionStateError


def test_export_session_parser_accepts_required_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "export-session",
            "linkedin.com",
            "--export-path",
            "exports/linkedin-session.json",
        ]
    )

    assert args.command == "export-session"
    assert args.source == "linkedin.com"
    assert args.export_path == Path("exports/linkedin-session.json")


def test_import_session_parser_accepts_required_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "import-session",
            "skool.com",
            "--import-path",
            "exports/skool-session.json",
        ]
    )

    assert args.command == "import-session"
    assert args.source == "skool.com"
    assert args.import_path == Path("exports/skool-session.json")


def test_run_export_session_reports_success_payload(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    observed: dict[str, object] = {}

    def _export_stub(source: SupportedSource, export_path: Path, *, project_root: Path, **_kwargs) -> Path:
        observed["source"] = source
        observed["export_path"] = export_path
        observed["project_root"] = project_root
        return project_root.joinpath(export_path)

    monkeypatch.setattr("kb.cli.load_enrichment_config_from_env", lambda: EnrichmentConfig())
    monkeypatch.setattr("kb.cli.export_session_state_json", _export_stub)

    parser = build_parser()
    args = parser.parse_args(
        [
            "export-session",
            "linkedin.com",
            "--export-path",
            "exports/linkedin-session.json",
            "--project-root",
            str(tmp_path),
        ]
    )
    status_code = run_export_session(args)

    assert status_code == 0
    assert observed["source"] == SupportedSource.linkedin
    assert observed["export_path"] == Path("exports/linkedin-session.json")
    assert observed["project_root"] == tmp_path.resolve()

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["source"] == "linkedin.com"
    assert payload["export_path"] == str(tmp_path / "exports/linkedin-session.json")


def test_run_import_session_reports_validation_error(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr("kb.cli.load_enrichment_config_from_env", lambda: EnrichmentConfig())
    monkeypatch.setattr(
        "kb.cli.import_session_state_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            InvalidSessionStateError(
                source=SupportedSource.skool,
                details="session transfer source mismatch: expected skool.com, got linkedin.com",
            )
        ),
    )

    parser = build_parser()
    args = parser.parse_args(
        [
            "import-session",
            "skool.com",
            "--import-path",
            "exports/linkedin-session.json",
            "--project-root",
            str(tmp_path),
        ]
    )
    status_code = run_import_session(args)

    assert status_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["source"] == "skool.com"
    assert payload["error_type"] == "InvalidSessionStateError"
    assert "source mismatch" in payload["message"]


def test_run_import_session_reports_success_payload(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    observed: dict[str, object] = {}

    def _import_stub(source: SupportedSource, import_path: Path, *, project_root: Path, **_kwargs) -> Path:
        observed["source"] = source
        observed["import_path"] = import_path
        observed["project_root"] = project_root
        return project_root.joinpath(".build/enrichment/sessions/skool.com/storage-state.json")

    monkeypatch.setattr("kb.cli.load_enrichment_config_from_env", lambda: EnrichmentConfig())
    monkeypatch.setattr("kb.cli.import_session_state_json", _import_stub)

    parser = build_parser()
    args = parser.parse_args(
        [
            "import-session",
            "skool.com",
            "--import-path",
            "exports/skool-session.json",
            "--project-root",
            str(tmp_path),
        ]
    )
    status_code = run_import_session(args)

    assert status_code == 0
    assert observed["source"] == SupportedSource.skool
    assert observed["import_path"] == Path("exports/skool-session.json")
    assert observed["project_root"] == tmp_path.resolve()

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["source"] == "skool.com"
    assert payload["session_state_path"].endswith("skool.com/storage-state.json")
