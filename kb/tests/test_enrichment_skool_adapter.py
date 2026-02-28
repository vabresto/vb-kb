from __future__ import annotations

import json
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from kb.enrichment_adapters import (
    AntiBotChallengeError,
    AuthenticationRequest,
    FetchRequest,
    NormalizeRequest,
    SnapshotRequest,
    SourceAdapter,
)
from kb.enrichment_config import DEFAULT_SKOOL_FETCH_COMMAND, EnrichmentConfig, SupportedSource
from kb.enrichment_sessions import save_session_state
from kb.enrichment_skool_adapter import (
    SkoolExtractionError,
    SkoolFetchCommandResult,
    SkoolSourceAdapter,
)


def _storage_state(*, expires: float) -> dict[str, Any]:
    return {
        "cookies": [
            {
                "name": "session",
                "value": "session-token",
                "domain": ".skool.com",
                "path": "/",
                "expires": expires,
                "httpOnly": True,
                "secure": True,
            }
        ],
        "origins": [],
    }


def _save_ready_session(tmp_path: Path, config: EnrichmentConfig) -> None:
    now = datetime(2030, 2, 28, 16, 0, tzinfo=UTC)
    save_session_state(
        SupportedSource.skool,
        _storage_state(expires=now.timestamp() + 3600),
        config=config,
        project_root=tmp_path,
    )


def test_skool_adapter_protocol_fetch_normalize_and_snapshot(tmp_path: Path) -> None:
    config = EnrichmentConfig()
    _save_ready_session(tmp_path, config)

    def runner(_: list[str], __: dict[str, str], ___: Path) -> SkoolFetchCommandResult:
        payload = {
            "source_url": "https://www.skool.com/founders/community/post/abc123",
            "retrieved_at": "2026-02-28T15:40:00Z",
            "facts": [
                {
                    "attribute": "headline",
                    "value": "Founder",
                    "confidence": "high",
                    "metadata": {"signal": "profile-header"},
                },
                {
                    "attribute": "community",
                    "value": "Founders Hub",
                },
            ],
            "html": "<html><body>Skool profile</body></html>",
        }
        return SkoolFetchCommandResult(returncode=0, stdout=json.dumps(payload))

    adapter = SkoolSourceAdapter(
        config=config,
        project_root=tmp_path,
        fetch_command="skool-fetch",
        fetch_runner=runner,
    )
    assert isinstance(adapter, SourceAdapter)

    fetch_result = adapter.fetch(
        request=_fetch_request(),
    )
    normalize_result = adapter.normalize(
        NormalizeRequest(fetch_result=fetch_result),
    )

    assert len(normalize_result.facts) == 2
    assert normalize_result.facts[0].attribute == "headline"
    assert normalize_result.facts[0].confidence.value == "high"
    assert normalize_result.facts[0].source_url == "https://www.skool.com/founders/community/post/abc123"
    assert normalize_result.facts[0].metadata["adapter"] == "skool.com"
    assert normalize_result.facts[1].confidence.value == "medium"
    assert normalize_result.facts[1].metadata["confidence_level"] == "medium"

    snapshot_result = adapter.snapshot(
        SnapshotRequest(
            fetch_result=fetch_result,
            output_path=".build/enrichment/source-evidence/skool.com/example.html",
        )
    )
    snapshot_path = tmp_path / snapshot_result.snapshot_path
    assert snapshot_result.content_type == "text/html"
    assert snapshot_path.exists()
    assert "Skool profile" in snapshot_path.read_text(encoding="utf-8")


def test_skool_adapter_fetch_bootstraps_when_session_missing(tmp_path: Path) -> None:
    config = EnrichmentConfig()
    observed: dict[str, Any] = {"bootstrap_calls": 0}

    def bootstrap_runner(source: SupportedSource, **kwargs) -> None:
        observed["bootstrap_calls"] += 1
        observed["bootstrap_source"] = source
        observed["bootstrap_headless"] = kwargs["headless"]
        save_session_state(
            source,
            _storage_state(expires=datetime(2030, 2, 28, 18, 0, tzinfo=UTC).timestamp()),
            config=kwargs["config"],
            project_root=kwargs["project_root"],
        )

    def fetch_runner(argv: list[str], env: dict[str, str], cwd: Path) -> SkoolFetchCommandResult:
        observed["argv"] = argv
        observed["extract_source"] = env["KB_ENRICHMENT_EXTRACT_SOURCE"]
        observed["extract_slug"] = env["KB_ENRICHMENT_EXTRACT_ENTITY_SLUG"]
        observed["cwd"] = cwd
        payload = {
            "source_url": "https://www.skool.com/founders",
            "facts": [{"attribute": "headline", "value": "Founder", "confidence": "high"}],
        }
        return SkoolFetchCommandResult(returncode=0, stdout=json.dumps(payload))

    adapter = SkoolSourceAdapter(
        config=config,
        project_root=tmp_path,
        fetch_command="skool-fetch",
        fetch_runner=fetch_runner,
        bootstrap_login_runner=bootstrap_runner,
    )

    fetch_result = adapter.fetch(_fetch_request(entity_slug="founder"))
    assert fetch_result.source_url == "https://www.skool.com/founders"
    assert observed["bootstrap_calls"] == 1
    assert observed["bootstrap_source"] == SupportedSource.skool
    assert observed["bootstrap_headless"] is True
    assert observed["argv"] == ["skool-fetch"]
    assert observed["extract_source"] == "skool.com"
    assert observed["extract_slug"] == "founder"
    assert observed["cwd"] == tmp_path.resolve()


def test_skool_adapter_uses_config_default_fetch_command(tmp_path: Path) -> None:
    config = EnrichmentConfig()
    _save_ready_session(tmp_path, config)
    observed: dict[str, Any] = {}

    def fetch_runner(argv: list[str], _env: dict[str, str], cwd: Path) -> SkoolFetchCommandResult:
        observed["argv"] = argv
        observed["cwd"] = cwd
        payload = {
            "source_url": "https://www.skool.com/founders",
            "facts": [{"attribute": "headline", "value": "Founder", "confidence": "high"}],
        }
        return SkoolFetchCommandResult(returncode=0, stdout=json.dumps(payload))

    adapter = SkoolSourceAdapter(
        config=config,
        project_root=tmp_path,
        fetch_runner=fetch_runner,
        environ={},
    )

    fetch_result = adapter.fetch(_fetch_request(entity_slug="founder"))
    assert fetch_result.source_url == "https://www.skool.com/founders"
    assert observed["argv"] == shlex.split(DEFAULT_SKOOL_FETCH_COMMAND)
    assert observed["cwd"] == tmp_path.resolve()


def test_skool_adapter_raises_antibot_error_on_blocked_command_output(tmp_path: Path) -> None:
    config = EnrichmentConfig()
    _save_ready_session(tmp_path, config)

    def fetch_runner(_: list[str], __: dict[str, str], ___: Path) -> SkoolFetchCommandResult:
        return SkoolFetchCommandResult(returncode=1, stderr="cloudflare captcha challenge")

    adapter = SkoolSourceAdapter(
        config=config,
        project_root=tmp_path,
        fetch_command="skool-fetch",
        fetch_runner=fetch_runner,
    )

    with pytest.raises(AntiBotChallengeError) as exc:
        adapter.fetch(_fetch_request())

    assert "anti-bot challenge encountered" in str(exc.value)
    assert "headful" in str(exc.value)


def test_skool_adapter_raises_extraction_error_for_unsupported_flow(tmp_path: Path) -> None:
    config = EnrichmentConfig()
    _save_ready_session(tmp_path, config)

    def fetch_runner(_: list[str], __: dict[str, str], ___: Path) -> SkoolFetchCommandResult:
        payload = {
            "status": "unsupported",
            "reason": "community unavailable",
        }
        return SkoolFetchCommandResult(returncode=0, stdout=json.dumps(payload))

    adapter = SkoolSourceAdapter(
        config=config,
        project_root=tmp_path,
        fetch_command="skool-fetch",
        fetch_runner=fetch_runner,
    )

    with pytest.raises(SkoolExtractionError) as exc:
        adapter.fetch(_fetch_request())

    assert "unsupported skool extraction flow" in str(exc.value)


def test_skool_authenticate_uses_request_session_path_with_bootstrap_fallback(tmp_path: Path) -> None:
    config = EnrichmentConfig()
    requested_path = ".build/custom/skool-storage.json"
    observed: dict[str, Any] = {}

    def bootstrap_runner(source: SupportedSource, **kwargs) -> None:
        observed["source"] = source
        observed["config_session_path"] = kwargs["config"].sources[SupportedSource.skool].session_state_path
        save_session_state(
            source,
            _storage_state(expires=datetime(2030, 2, 28, 19, 0, tzinfo=UTC).timestamp()),
            config=kwargs["config"],
            project_root=kwargs["project_root"],
        )

    adapter = SkoolSourceAdapter(
        config=config,
        project_root=tmp_path,
        fetch_command="skool-fetch",
        fetch_runner=lambda *_args, **_kwargs: SkoolFetchCommandResult(
            returncode=0,
            stdout=json.dumps({"facts": [{"attribute": "headline", "value": "Founder"}]}),
        ),
        bootstrap_login_runner=bootstrap_runner,
    )

    auth_result = adapter.authenticate(
        AuthenticationRequest(
            session_state_path=requested_path,
            headless=False,
        )
    )

    assert auth_result.authenticated is True
    assert auth_result.used_session_state_path is not None
    assert auth_result.used_session_state_path.endswith("custom/skool-storage.json")
    assert observed["source"] == SupportedSource.skool
    assert observed["config_session_path"] == requested_path


def _fetch_request(*, entity_slug: str = "example") -> FetchRequest:
    return FetchRequest(
        entity_ref="person/test/example.md",
        entity_slug=entity_slug,
        run_id="run-001",
        started_at=datetime(2026, 2, 28, 15, 30, tzinfo=UTC),
    )
