from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from kb.cli import build_parser, run_bootstrap_session
from kb.enrichment_bootstrap import BootstrapSessionResult
from kb.enrichment_config import EnrichmentConfig, SupportedSource


def test_bootstrap_session_parser_accepts_headful_export_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "bootstrap-session",
            "linkedin.com",
            "--headful",
            "--export-path",
            "exports/linkedin.json",
            "--bootstrap-command",
            "bootstrap-linkedin",
            "--no-random-waits",
        ]
    )

    assert args.command == "bootstrap-session"
    assert args.source == "linkedin.com"
    assert args.headful is True
    assert args.export_path == Path("exports/linkedin.json")
    assert args.bootstrap_command == "bootstrap-linkedin"
    assert args.no_random_waits is True


def test_run_bootstrap_session_reports_success_payload(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr("kb.cli.load_enrichment_config_from_env", lambda: EnrichmentConfig())
    monkeypatch.setattr(
        "kb.cli.bootstrap_session_login",
        lambda *_args, **_kwargs: BootstrapSessionResult(
            source=SupportedSource.skool,
            headless=True,
            bootstrap_command="bootstrap-skool",
            session_state_path=str(tmp_path / ".build/enrichment/sessions/skool.com/storage-state.json"),
            export_path=str(tmp_path / "exports/skool-session.json"),
            expires_at=datetime(2026, 2, 28, 16, 30, tzinfo=UTC),
        ),
    )

    parser = build_parser()
    args = parser.parse_args(["bootstrap-session", "skool.com", "--project-root", str(tmp_path)])
    status_code = run_bootstrap_session(args)

    assert status_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["source"] == "skool.com"
    assert payload["headless"] is True
    assert payload["bootstrap_command"] == "bootstrap-skool"


def test_run_bootstrap_session_uses_default_headful_export_path(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    observed: dict[str, object] = {}

    def _bootstrap_stub(*_args, **kwargs) -> BootstrapSessionResult:
        observed["export_path"] = kwargs["export_path"]
        return BootstrapSessionResult(
            source=SupportedSource.linkedin,
            headless=False,
            bootstrap_command="bootstrap-linkedin",
            session_state_path=str(tmp_path / ".build/enrichment/sessions/linkedin.com/storage-state.json"),
            export_path=str(tmp_path / ".build/enrichment/sessions/linkedin.com/headful-export.json"),
            expires_at=None,
        )

    monkeypatch.setattr("kb.cli.load_enrichment_config_from_env", lambda: EnrichmentConfig())
    monkeypatch.setattr("kb.cli.bootstrap_session_login", _bootstrap_stub)

    parser = build_parser()
    args = parser.parse_args(["bootstrap-session", "linkedin.com", "--headful", "--project-root", str(tmp_path)])
    status_code = run_bootstrap_session(args)

    assert status_code == 0
    assert observed["export_path"] == Path(".build/enrichment/sessions/linkedin.com/headful-export.json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["headless"] is False


def test_run_bootstrap_session_no_random_waits_sets_env_var(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    observed: dict[str, object] = {}

    def _bootstrap_stub(*_args, **kwargs) -> BootstrapSessionResult:
        environ = kwargs.get("environ") or {}
        observed["random_waits"] = environ.get("KB_ENRICHMENT_ACTION_RANDOM_WAITS")
        return BootstrapSessionResult(
            source=SupportedSource.linkedin,
            headless=True,
            bootstrap_command="bootstrap-linkedin",
            session_state_path=str(tmp_path / ".build/enrichment/sessions/linkedin.com/storage-state.json"),
            export_path=None,
            expires_at=None,
        )

    monkeypatch.setattr("kb.cli.load_enrichment_config_from_env", lambda: EnrichmentConfig())
    monkeypatch.setattr("kb.cli.bootstrap_session_login", _bootstrap_stub)

    parser = build_parser()
    args = parser.parse_args(
        ["bootstrap-session", "linkedin.com", "--project-root", str(tmp_path), "--no-random-waits"]
    )
    status_code = run_bootstrap_session(args)

    assert status_code == 0
    assert observed["random_waits"] == "false"
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
