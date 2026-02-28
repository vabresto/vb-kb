from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import Field, field_validator

from kb.enrichment_config import ConfidenceLevel, SupportedSource
from kb.schemas import KBBaseModel, normalize_path_token


class AuthenticationRequest(KBBaseModel):
    session_state_path: str
    headless: bool
    username: str | None = None
    password: str | None = None

    @field_validator("session_state_path")
    @classmethod
    def validate_session_state_path(cls, value: str) -> str:
        normalized = normalize_path_token(value)
        if normalized is None:
            raise ValueError("session_state_path must be non-empty")
        return normalized


class AuthenticationResult(KBBaseModel):
    authenticated: bool
    used_session_state_path: str | None = None
    expires_at: datetime | None = None

    @field_validator("used_session_state_path")
    @classmethod
    def validate_used_session_state_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_path_token(value)
        if normalized is None:
            raise ValueError("used_session_state_path must be non-empty")
        return normalized


class FetchRequest(KBBaseModel):
    entity_ref: str
    entity_slug: str
    run_id: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))

    @field_validator("entity_ref", "entity_slug", "run_id")
    @classmethod
    def validate_non_empty_token(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must be non-empty")
        return text


class FetchResult(KBBaseModel):
    source_url: str
    retrieved_at: datetime
    payload: dict[str, Any]

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("source_url must be non-empty")
        return text


class NormalizedFact(KBBaseModel):
    attribute: str
    value: str
    confidence: ConfidenceLevel
    source_url: str
    retrieved_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attribute", "value", "source_url")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must be non-empty")
        return text


class NormalizeRequest(KBBaseModel):
    fetch_result: FetchResult


class NormalizeResult(KBBaseModel):
    facts: list[NormalizedFact]


class SnapshotRequest(KBBaseModel):
    fetch_result: FetchResult
    output_path: str

    @field_validator("output_path")
    @classmethod
    def validate_output_path(cls, value: str) -> str:
        normalized = normalize_path_token(value)
        if normalized is None:
            raise ValueError("output_path must be non-empty")
        return normalized


class SnapshotResult(KBBaseModel):
    snapshot_path: str
    content_type: str

    @field_validator("snapshot_path")
    @classmethod
    def validate_snapshot_path(cls, value: str) -> str:
        normalized = normalize_path_token(value)
        if normalized is None:
            raise ValueError("snapshot_path must be non-empty")
        return normalized

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("content_type must be non-empty")
        return text


class SourceAdapterError(RuntimeError):
    """Base class for enrichment adapter failures."""

    source: str
    details: str | None

    def __init__(self, *, source: SupportedSource | str, message: str, details: str | None = None) -> None:
        source_text = source.value if isinstance(source, SupportedSource) else str(source)
        self.source = source_text
        self.details = details
        full_message = message
        if details:
            full_message = f"{message} ({details})"
        super().__init__(full_message)


class AdapterNotFoundError(SourceAdapterError):
    def __init__(self, *, source: SupportedSource | str) -> None:
        source_text = source.value if isinstance(source, SupportedSource) else str(source)
        super().__init__(
            source=source,
            message=f"no source adapter registered for '{source_text}'",
        )


class AuthenticationError(SourceAdapterError):
    pass


class AntiBotChallengeError(AuthenticationError):
    def __init__(self, *, source: SupportedSource | str, details: str | None = None) -> None:
        super().__init__(source=source, message="anti-bot challenge encountered", details=details)


class MFAChallengeError(AuthenticationError):
    def __init__(self, *, source: SupportedSource | str, details: str | None = None) -> None:
        super().__init__(source=source, message="mfa challenge encountered", details=details)


@runtime_checkable
class SourceAdapter(Protocol):
    source: SupportedSource

    def authenticate(self, request: AuthenticationRequest) -> AuthenticationResult: ...

    def fetch(self, request: FetchRequest) -> FetchResult: ...

    def normalize(self, request: NormalizeRequest) -> NormalizeResult: ...

    def snapshot(self, request: SnapshotRequest) -> SnapshotResult: ...


class SourceAdapterRegistry:
    def __init__(self, adapters: Iterable[SourceAdapter] | None = None) -> None:
        self._adapters: dict[SupportedSource, SourceAdapter] = {}
        if adapters is None:
            return
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: SourceAdapter) -> None:
        self._adapters[adapter.source] = adapter

    def get(self, source: SupportedSource | str) -> SourceAdapter:
        key = self._normalize_source(source)
        adapter = self._adapters.get(key)
        if adapter is None:
            raise AdapterNotFoundError(source=key)
        return adapter

    def has(self, source: SupportedSource | str) -> bool:
        try:
            key = self._normalize_source(source)
        except AdapterNotFoundError:
            return False
        return key in self._adapters

    def registered_sources(self) -> tuple[SupportedSource, ...]:
        return tuple(sorted(self._adapters.keys(), key=lambda item: item.value))

    @staticmethod
    def _normalize_source(source: SupportedSource | str) -> SupportedSource:
        if isinstance(source, SupportedSource):
            return source
        try:
            return SupportedSource(str(source).strip())
        except ValueError as exc:
            raise AdapterNotFoundError(source=source) from exc
