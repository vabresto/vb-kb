from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import ValidationError, field_validator

from kb.enrichment_adapters import AuthenticationError
from kb.enrichment_config import EnrichmentConfig, SupportedSource
from kb.schemas import KBBaseModel


class SessionLookupStatus(str, Enum):
    ready = "ready"
    missing = "missing"
    expired = "expired"


class SessionLookupDiagnostics(KBBaseModel):
    source: SupportedSource
    session_state_path: str
    status: SessionLookupStatus
    message: str
    expires_at: datetime | None = None
    storage_state: dict[str, Any] | None = None


class SessionTransferPayload(KBBaseModel):
    source: SupportedSource
    exported_at: datetime
    expires_at: datetime | None = None
    storage_state: dict[str, Any]

    @field_validator("storage_state")
    @classmethod
    def validate_storage_state(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_storage_state(value)


class SessionStateMissingError(AuthenticationError):
    def __init__(self, *, diagnostics: SessionLookupDiagnostics) -> None:
        super().__init__(
            source=diagnostics.source,
            message="session state not found",
            details=diagnostics.message,
        )


class SessionStateExpiredError(AuthenticationError):
    def __init__(self, *, diagnostics: SessionLookupDiagnostics) -> None:
        super().__init__(
            source=diagnostics.source,
            message="session state expired",
            details=diagnostics.message,
        )


class InvalidSessionStateError(AuthenticationError):
    def __init__(self, *, source: SupportedSource, details: str) -> None:
        super().__init__(
            source=source,
            message="invalid session state",
            details=details,
        )


def resolve_session_state_path(
    source: SupportedSource,
    *,
    config: EnrichmentConfig,
    project_root: Path,
) -> Path:
    session_path = config.sources[source].session_state_path
    return project_root.joinpath(session_path)


def save_session_state(
    source: SupportedSource,
    storage_state: dict[str, Any],
    *,
    config: EnrichmentConfig,
    project_root: Path,
) -> Path:
    session_path = resolve_session_state_path(source, config=config, project_root=project_root)
    try:
        normalized = _validate_storage_state(storage_state)
        _extract_latest_cookie_expiry(normalized)
    except ValueError as exc:
        raise InvalidSessionStateError(source=source, details=str(exc)) from exc

    session_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(normalized, indent=2, sort_keys=True)
    session_path.write_text(payload + "\n", encoding="utf-8")
    return session_path


def lookup_session_state(
    source: SupportedSource,
    *,
    config: EnrichmentConfig,
    project_root: Path,
    now: datetime | None = None,
) -> SessionLookupDiagnostics:
    session_path = resolve_session_state_path(source, config=config, project_root=project_root)
    session_path_text = str(session_path)
    if not session_path.exists():
        return SessionLookupDiagnostics(
            source=source,
            session_state_path=session_path_text,
            status=SessionLookupStatus.missing,
            message=(
                f"Session state not found at '{session_path_text}'. "
                "Run session bootstrap for this source before enrichment."
            ),
        )

    storage_state = _read_storage_state_json(session_path, source=source)
    expires_at = _extract_latest_cookie_expiry(storage_state)
    current_time = _normalize_now(now)
    if expires_at is not None and expires_at <= current_time:
        return SessionLookupDiagnostics(
            source=source,
            session_state_path=session_path_text,
            status=SessionLookupStatus.expired,
            message=(
                f"Session state at '{session_path_text}' expired at {expires_at.isoformat()}. "
                "Run session bootstrap to refresh credentials."
            ),
            expires_at=expires_at,
            storage_state=storage_state,
        )

    return SessionLookupDiagnostics(
        source=source,
        session_state_path=session_path_text,
        status=SessionLookupStatus.ready,
        message=f"Session state is ready at '{session_path_text}'.",
        expires_at=expires_at,
        storage_state=storage_state,
    )


def load_session_state(
    source: SupportedSource,
    *,
    config: EnrichmentConfig,
    project_root: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    diagnostics = lookup_session_state(
        source,
        config=config,
        project_root=project_root,
        now=now,
    )
    if diagnostics.status == SessionLookupStatus.missing:
        raise SessionStateMissingError(diagnostics=diagnostics)
    if diagnostics.status == SessionLookupStatus.expired:
        raise SessionStateExpiredError(diagnostics=diagnostics)
    if diagnostics.storage_state is None:
        raise InvalidSessionStateError(
            source=source,
            details=(
                "session lookup unexpectedly returned no storage_state "
                "for a ready session"
            ),
        )
    return diagnostics.storage_state


def export_session_state_json(
    source: SupportedSource,
    export_path: Path,
    *,
    config: EnrichmentConfig,
    project_root: Path,
    now: datetime | None = None,
) -> Path:
    storage_state = load_session_state(
        source,
        config=config,
        project_root=project_root,
        now=now,
    )
    expires_at = _extract_latest_cookie_expiry(storage_state)
    payload = SessionTransferPayload(
        source=source,
        exported_at=_normalize_now(now),
        expires_at=expires_at,
        storage_state=storage_state,
    )

    resolved_export_path = _resolve_external_path(export_path, project_root=project_root)
    resolved_export_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_export_path.write_text(
        json.dumps(payload.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return resolved_export_path


def import_session_state_json(
    source: SupportedSource,
    import_path: Path,
    *,
    config: EnrichmentConfig,
    project_root: Path,
) -> Path:
    resolved_import_path = _resolve_external_path(import_path, project_root=project_root)
    if not resolved_import_path.exists():
        raise InvalidSessionStateError(
            source=source,
            details=(
                f"session import file not found at '{resolved_import_path}'. "
                "Provide a valid exported session JSON file."
            ),
        )

    payload = _read_json_object(resolved_import_path, source=source)
    storage_state = _extract_storage_state_from_import_payload(payload, expected_source=source)
    return save_session_state(
        source,
        storage_state,
        config=config,
        project_root=project_root,
    )


def _resolve_external_path(path: Path, *, project_root: Path) -> Path:
    if path.is_absolute():
        return path
    return project_root.joinpath(path)


def _normalize_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(tz=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _read_storage_state_json(path: Path, *, source: SupportedSource) -> dict[str, Any]:
    payload = _read_json_object(path, source=source)
    try:
        return _validate_storage_state(payload)
    except ValueError as exc:
        raise InvalidSessionStateError(source=source, details=str(exc)) from exc


def _read_json_object(path: Path, *, source: SupportedSource) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InvalidSessionStateError(
            source=source,
            details=f"unable to parse JSON from '{path}': {exc.msg}",
        ) from exc
    except OSError as exc:
        raise InvalidSessionStateError(
            source=source,
            details=f"unable to read '{path}': {exc}",
        ) from exc

    if not isinstance(parsed, dict):
        raise InvalidSessionStateError(
            source=source,
            details=f"session payload at '{path}' must be a JSON object",
        )
    return parsed


def _validate_storage_state(storage_state: dict[str, Any]) -> dict[str, Any]:
    cookies = storage_state.get("cookies")
    if not isinstance(cookies, list):
        raise ValueError("storageState must include 'cookies' as a list")
    origins = storage_state.get("origins")
    if not isinstance(origins, list):
        raise ValueError("storageState must include 'origins' as a list")

    for index, cookie in enumerate(cookies):
        if not isinstance(cookie, dict):
            raise ValueError(f"cookie at index {index} must be an object")
    for index, origin in enumerate(origins):
        if not isinstance(origin, dict):
            raise ValueError(f"origin at index {index} must be an object")

    return storage_state


def _extract_latest_cookie_expiry(storage_state: dict[str, Any]) -> datetime | None:
    cookies = storage_state.get("cookies")
    if not isinstance(cookies, list):
        raise ValueError("storageState must include 'cookies' as a list")

    latest_expiry: float | None = None
    for index, cookie in enumerate(cookies):
        if not isinstance(cookie, dict):
            raise ValueError(f"cookie at index {index} must be an object")
        raw_expiry = cookie.get("expires")
        if raw_expiry is None:
            continue
        if isinstance(raw_expiry, bool):
            raise ValueError(f"cookie at index {index} has non-numeric 'expires' value")
        try:
            expiry_seconds = float(raw_expiry)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"cookie at index {index} has non-numeric 'expires' value") from exc
        if expiry_seconds <= 0:
            continue
        if latest_expiry is None or expiry_seconds > latest_expiry:
            latest_expiry = expiry_seconds

    if latest_expiry is None:
        return None

    try:
        return datetime.fromtimestamp(latest_expiry, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("cookie 'expires' timestamp is out of range") from exc


def _extract_storage_state_from_import_payload(
    payload: dict[str, Any],
    *,
    expected_source: SupportedSource,
) -> dict[str, Any]:
    if "storage_state" not in payload:
        try:
            return _validate_storage_state(payload)
        except ValueError as exc:
            raise InvalidSessionStateError(source=expected_source, details=str(exc)) from exc

    try:
        transfer_payload = SessionTransferPayload.model_validate(payload)
    except ValidationError as exc:
        raise InvalidSessionStateError(
            source=expected_source,
            details=f"invalid session transfer payload: {exc.errors()[0]['msg']}",
        ) from exc

    if transfer_payload.source != expected_source:
        raise InvalidSessionStateError(
            source=expected_source,
            details=(
                "session transfer source mismatch: "
                f"expected '{expected_source.value}', got '{transfer_payload.source.value}'"
            ),
        )

    return transfer_payload.storage_state
