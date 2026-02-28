from __future__ import annotations

from kb.enrichment_config import SupportedSource
from kb.enrichment_playwright_fetch import (
    _extract_linkedin_facts,
    _extract_skool_facts,
    _resolve_source,
    _unsupported_reason,
)


def test_extract_linkedin_facts_from_title_and_description() -> None:
    facts = _extract_linkedin_facts(
        title="Jane Founder at Future Labs | LinkedIn",
        description="Building applied AI tools.",
    )

    values_by_attribute = {fact["attribute"]: fact["value"] for fact in facts}
    assert values_by_attribute["headline"] == "Jane Founder at Future Labs"
    assert values_by_attribute["about"] == "Building applied AI tools."
    assert values_by_attribute["current_company"] == "Future Labs"


def test_extract_skool_facts_from_title_and_description() -> None:
    facts = _extract_skool_facts(
        title="Founders Circle - Jane Founder | Skool",
        description="Community for startup builders.",
    )

    values_by_attribute = {fact["attribute"]: fact["value"] for fact in facts}
    assert values_by_attribute["headline"] == "Founders Circle - Jane Founder"
    assert values_by_attribute["community"] == "Founders Circle"
    assert values_by_attribute["about"] == "Community for startup builders."


def test_unsupported_reason_detects_login_and_captcha() -> None:
    assert (
        _unsupported_reason(
            source=SupportedSource.linkedin,
            url="https://www.linkedin.com/login",
            title="Sign in | LinkedIn",
            html="<html><body>Sign in</body></html>",
        )
        == "authenticated linkedin session required"
    )
    assert (
        _unsupported_reason(
            source=SupportedSource.skool,
            url="https://www.skool.com/@founder",
            title="Skool",
            html="<div>Please verify you are human (captcha)</div>",
        )
        == "captcha challenge detected"
    )


def test_resolve_source_prefers_argument_over_environment(monkeypatch) -> None:
    monkeypatch.setenv("KB_ENRICHMENT_EXTRACT_SOURCE", "skool.com")
    assert _resolve_source("linkedin.com") == SupportedSource.linkedin
