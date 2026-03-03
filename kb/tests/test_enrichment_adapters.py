from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kb.enrichment_adapters import (
    AdapterNotFoundError,
    AntiBotChallengeError,
    AuthenticationRequest,
    AuthenticationResult,
    FetchRequest,
    FetchResult,
    MFAChallengeError,
    NormalizeRequest,
    NormalizeResult,
    NormalizedFact,
    SnapshotRequest,
    SnapshotResult,
    SourceAdapter,
    SourceAdapterRegistry,
)
from kb.enrichment_config import ConfidenceLevel, SupportedSource


class StubLinkedInAdapter:
    source = SupportedSource.linkedin

    def authenticate(self, request: AuthenticationRequest) -> AuthenticationResult:
        return AuthenticationResult(
            authenticated=True,
            used_session_state_path=request.session_state_path,
        )

    def fetch(self, request: FetchRequest) -> FetchResult:
        return FetchResult(
            source_url="https://example.com/linkedin-example",
            retrieved_at=request.started_at,
            payload={"entity": request.entity_slug, "run_id": request.run_id},
        )

    def normalize(self, request: NormalizeRequest) -> NormalizeResult:
        return NormalizeResult(
            facts=[
                NormalizedFact(
                    attribute="headline",
                    value="Founder",
                    confidence=ConfidenceLevel.high,
                    source_url=request.fetch_result.source_url,
                    retrieved_at=request.fetch_result.retrieved_at,
                    metadata={"adapter": "stub"},
                )
            ]
        )

    def snapshot(self, request: SnapshotRequest) -> SnapshotResult:
        return SnapshotResult(
            snapshot_path=request.output_path,
            content_type="text/html",
        )


def test_source_adapter_protocol_and_pipeline_shape() -> None:
    adapter = StubLinkedInAdapter()
    assert isinstance(adapter, SourceAdapter)

    auth = adapter.authenticate(
        AuthenticationRequest(
            session_state_path=".build/enrichment/sessions/linkedin.com/storage-state.json",
            headless=True,
        )
    )
    assert auth.authenticated is True
    assert auth.used_session_state_path is not None

    fetch = adapter.fetch(
        FetchRequest(
            entity_ref="person/ab/person@example",
            entity_slug="example",
            run_id="run-001",
            started_at=datetime(2026, 2, 28, 10, 0, tzinfo=UTC),
        )
    )
    normalized = adapter.normalize(NormalizeRequest(fetch_result=fetch))
    assert normalized.facts[0].confidence == ConfidenceLevel.high

    snapshot = adapter.snapshot(
        SnapshotRequest(
            fetch_result=fetch,
            output_path=".build/enrichment/source-evidence/linkedin.com/example.html",
        )
    )
    assert snapshot.content_type == "text/html"


def test_registry_lookup_returns_registered_adapter_for_enum_and_string() -> None:
    adapter = StubLinkedInAdapter()
    registry = SourceAdapterRegistry(adapters=[adapter])

    assert registry.get(SupportedSource.linkedin) is adapter
    assert registry.get("linkedin.com") is adapter
    assert registry.has("linkedin.com") is True
    assert registry.registered_sources() == (SupportedSource.linkedin,)


def test_registry_raises_typed_not_found_for_unknown_and_unregistered_source() -> None:
    registry = SourceAdapterRegistry(adapters=[StubLinkedInAdapter()])

    with pytest.raises(AdapterNotFoundError) as unknown:
        registry.get("example.com")
    assert unknown.value.source == "example.com"

    with pytest.raises(AdapterNotFoundError) as missing:
        registry.get(SupportedSource.skool)
    assert missing.value.source == "skool.com"


def test_typed_challenge_errors_include_source_and_details() -> None:
    anti_bot = AntiBotChallengeError(
        source=SupportedSource.linkedin,
        details="captcha checkpoint",
    )
    assert anti_bot.source == "linkedin.com"
    assert "anti-bot challenge encountered" in str(anti_bot)
    assert "captcha checkpoint" in str(anti_bot)

    mfa = MFAChallengeError(
        source=SupportedSource.skool,
        details="totp required",
    )
    assert mfa.source == "skool.com"
    assert "mfa challenge encountered" in str(mfa)
    assert "totp required" in str(mfa)
