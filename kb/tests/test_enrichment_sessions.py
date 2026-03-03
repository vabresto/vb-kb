from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from kb.enrichment_config import EnrichmentConfig, SupportedSource
from kb.enrichment_sessions import (
    InvalidSessionStateError,
    SessionLookupStatus,
    SessionStateExpiredError,
    SessionStateMissingError,
    export_session_state_json,
    import_session_state_json,
    load_session_state,
    lookup_session_state,
    resolve_session_state_path,
    save_session_state,
)


def _storage_state_with_expiry(*, expires: float) -> dict[str, Any]:
    return {
        "cookies": [
            {
                "name": "session-cookie",
                "value": "token-value",
                "domain": ".linkedin.com",
                "path": "/",
                "expires": expires,
                "httpOnly": True,
                "secure": True,
            }
        ],
        "origins": [],
    }


def test_save_and_load_session_state_round_trips_for_source(tmp_path: Path) -> None:
    config = EnrichmentConfig()
    now = datetime(2026, 2, 28, 15, 0, tzinfo=UTC)
    storage_state = _storage_state_with_expiry(expires=now.timestamp() + 3600)

    saved_path = save_session_state(
        SupportedSource.linkedin,
        storage_state,
        config=config,
        project_root=tmp_path,
    )

    assert saved_path == resolve_session_state_path(
        SupportedSource.linkedin,
        config=config,
        project_root=tmp_path,
    )
    assert saved_path.exists()

    loaded = load_session_state(
        SupportedSource.linkedin,
        config=config,
        project_root=tmp_path,
        now=now,
    )
    assert loaded == storage_state


def test_lookup_session_state_reports_missing_with_actionable_message(tmp_path: Path) -> None:
    config = EnrichmentConfig()
    diagnostics = lookup_session_state(
        SupportedSource.skool,
        config=config,
        project_root=tmp_path,
    )

    assert diagnostics.status == SessionLookupStatus.missing
    assert "Run session bootstrap" in diagnostics.message
    assert diagnostics.expires_at is None


def test_load_session_state_raises_missing_typed_auth_error(tmp_path: Path) -> None:
    config = EnrichmentConfig()

    with pytest.raises(SessionStateMissingError) as exc:
        load_session_state(
            SupportedSource.linkedin,
            config=config,
            project_root=tmp_path,
        )

    assert "session state not found" in str(exc.value)
    assert "Run session bootstrap" in str(exc.value)


def test_load_session_state_raises_expired_typed_auth_error(tmp_path: Path) -> None:
    config = EnrichmentConfig()
    now = datetime(2026, 2, 28, 15, 0, tzinfo=UTC)
    expired_storage_state = _storage_state_with_expiry(expires=now.timestamp() - 30)

    save_session_state(
        SupportedSource.linkedin,
        expired_storage_state,
        config=config,
        project_root=tmp_path,
    )

    diagnostics = lookup_session_state(
        SupportedSource.linkedin,
        config=config,
        project_root=tmp_path,
        now=now,
    )
    assert diagnostics.status == SessionLookupStatus.expired
    assert diagnostics.expires_at is not None
    assert "Run session bootstrap" in diagnostics.message

    with pytest.raises(SessionStateExpiredError) as exc:
        load_session_state(
            SupportedSource.linkedin,
            config=config,
            project_root=tmp_path,
            now=now,
        )
    assert "session state expired" in str(exc.value)
    assert "expired at" in str(exc.value)


def test_load_session_state_rejects_invalid_json_payload(tmp_path: Path) -> None:
    config = EnrichmentConfig()
    session_path = resolve_session_state_path(
        SupportedSource.linkedin,
        config=config,
        project_root=tmp_path,
    )
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text("{ not valid json", encoding="utf-8")

    with pytest.raises(InvalidSessionStateError) as exc:
        load_session_state(
            SupportedSource.linkedin,
            config=config,
            project_root=tmp_path,
        )
    assert "invalid session state" in str(exc.value)
    assert "unable to parse JSON" in str(exc.value)


def test_load_session_state_rejects_invalid_storage_state_shape(tmp_path: Path) -> None:
    config = EnrichmentConfig()
    session_path = resolve_session_state_path(
        SupportedSource.linkedin,
        config=config,
        project_root=tmp_path,
    )
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        json.dumps({"cookies": "invalid", "origins": []}),
        encoding="utf-8",
    )

    with pytest.raises(InvalidSessionStateError) as exc:
        load_session_state(
            SupportedSource.linkedin,
            config=config,
            project_root=tmp_path,
        )
    assert "invalid session state" in str(exc.value)
    assert "cookies" in str(exc.value)


def test_export_and_import_session_state_json_round_trip(tmp_path: Path) -> None:
    config = EnrichmentConfig()
    now = datetime(2026, 2, 28, 15, 0, tzinfo=UTC)
    storage_state = _storage_state_with_expiry(expires=now.timestamp() + 900)
    save_session_state(
        SupportedSource.skool,
        storage_state,
        config=config,
        project_root=tmp_path,
    )

    export_file = export_session_state_json(
        SupportedSource.skool,
        Path("exports/skool-session.json"),
        config=config,
        project_root=tmp_path,
        now=now,
    )
    assert export_file.exists()

    save_session_state(
        SupportedSource.skool,
        _storage_state_with_expiry(expires=now.timestamp() - 60),
        config=config,
        project_root=tmp_path,
    )

    import_session_state_json(
        SupportedSource.skool,
        Path("exports/skool-session.json"),
        config=config,
        project_root=tmp_path,
    )

    loaded = load_session_state(
        SupportedSource.skool,
        config=config,
        project_root=tmp_path,
        now=now,
    )
    assert loaded == storage_state


def test_import_session_state_json_rejects_source_mismatch(tmp_path: Path) -> None:
    config = EnrichmentConfig()
    now = datetime(2026, 2, 28, 15, 0, tzinfo=UTC)
    save_session_state(
        SupportedSource.linkedin,
        _storage_state_with_expiry(expires=now.timestamp() + 600),
        config=config,
        project_root=tmp_path,
    )
    export_session_state_json(
        SupportedSource.linkedin,
        Path("exports/linkedin-session.json"),
        config=config,
        project_root=tmp_path,
        now=now,
    )

    with pytest.raises(InvalidSessionStateError) as exc:
        import_session_state_json(
            SupportedSource.skool,
            Path("exports/linkedin-session.json"),
            config=config,
            project_root=tmp_path,
        )

    assert "source mismatch" in str(exc.value)
