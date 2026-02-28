from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from kb.enrichment_adapters import AntiBotChallengeError, MFAChallengeError
from kb.enrichment_bootstrap import (
    BootstrapCommandResult,
    BootstrapScriptNotConfiguredError,
    bootstrap_session_login,
)
from kb.enrichment_config import EnrichmentConfig, SupportedSource
from kb.enrichment_sessions import load_session_state


def _storage_state_with_expiry(*, expires: float, domain: str) -> dict[str, Any]:
    return {
        "cookies": [
            {
                "name": "session-cookie",
                "value": "token-value",
                "domain": domain,
                "path": "/",
                "expires": expires,
                "httpOnly": True,
                "secure": True,
            }
        ],
        "origins": [],
    }


def _config_with_bootstrap_commands() -> EnrichmentConfig:
    config = EnrichmentConfig()
    config.sources[SupportedSource.linkedin].bootstrap_command = "bootstrap-linkedin"
    config.sources[SupportedSource.skool].bootstrap_command = "bootstrap-skool"
    return config


def test_bootstrap_session_login_persists_session_and_exports_when_requested(tmp_path: Path) -> None:
    config = _config_with_bootstrap_commands()
    now = datetime(2026, 2, 28, 16, 0, tzinfo=UTC)
    storage_state = _storage_state_with_expiry(
        expires=now.timestamp() + 1800,
        domain=".linkedin.com",
    )

    observed: dict[str, Any] = {}

    def runner(argv: list[str], env: dict[str, str], cwd: Path) -> BootstrapCommandResult:
        observed["argv"] = argv
        observed["headless"] = env["KB_ENRICHMENT_BOOTSTRAP_HEADLESS"]
        observed["source"] = env["KB_ENRICHMENT_BOOTSTRAP_SOURCE"]
        observed["cwd"] = cwd
        return BootstrapCommandResult(returncode=0, stdout=json.dumps(storage_state))

    result = bootstrap_session_login(
        SupportedSource.linkedin,
        config=config,
        project_root=tmp_path,
        headless=False,
        export_path=Path("exports/linkedin-session.json"),
        now=now,
        command_runner=runner,
    )

    assert observed["argv"] == ["bootstrap-linkedin"]
    assert observed["headless"] == "false"
    assert observed["source"] == "linkedin.com"
    assert observed["cwd"] == tmp_path.resolve()

    assert Path(result.session_state_path).exists()
    assert result.export_path is not None
    assert Path(result.export_path).exists()
    assert load_session_state(
        SupportedSource.linkedin,
        config=config,
        project_root=tmp_path,
        now=now,
    ) == storage_state


def test_bootstrap_session_login_requires_configured_command(tmp_path: Path) -> None:
    config = EnrichmentConfig()

    with pytest.raises(BootstrapScriptNotConfiguredError) as exc:
        bootstrap_session_login(
            SupportedSource.skool,
            config=config,
            project_root=tmp_path,
            headless=True,
            command_runner=lambda *_: BootstrapCommandResult(returncode=0, stdout="{}"),
        )

    assert "bootstrap command not configured" in str(exc.value)
    assert "KB_ENRICHMENT_SKOOL_BOOTSTRAP_COMMAND" in str(exc.value)


def test_bootstrap_session_login_raises_mfa_challenge_with_guidance(tmp_path: Path) -> None:
    config = _config_with_bootstrap_commands()

    def runner(_: list[str], __: dict[str, str], ___: Path) -> BootstrapCommandResult:
        return BootstrapCommandResult(
            returncode=0,
            stdout=json.dumps({"challenge": "mfa", "details": "verification code required"}),
        )

    with pytest.raises(MFAChallengeError) as exc:
        bootstrap_session_login(
            SupportedSource.linkedin,
            config=config,
            project_root=tmp_path,
            headless=True,
            command_runner=runner,
        )

    assert "mfa challenge encountered" in str(exc.value)
    assert "--headful" in str(exc.value)


def test_bootstrap_session_login_raises_antibot_challenge_from_command_failure(tmp_path: Path) -> None:
    config = _config_with_bootstrap_commands()

    def runner(_: list[str], __: dict[str, str], ___: Path) -> BootstrapCommandResult:
        return BootstrapCommandResult(returncode=1, stderr="captcha challenge page")

    with pytest.raises(AntiBotChallengeError) as exc:
        bootstrap_session_login(
            SupportedSource.skool,
            config=config,
            project_root=tmp_path,
            headless=True,
            command_runner=runner,
        )

    assert "anti-bot challenge encountered" in str(exc.value)
    assert "headful" in str(exc.value)
