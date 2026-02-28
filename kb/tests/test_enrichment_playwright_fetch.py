from __future__ import annotations

from kb.enrichment_config import SupportedSource
from kb.enrichment_playwright_fetch import (
    _deduplicate_fact_rows,
    _extract_linkedin_facts,
    _extract_skool_facts,
    _resolve_source,
    _unsupported_reason,
)


def test_extract_linkedin_facts_from_title_and_description() -> None:
    facts = _extract_linkedin_facts(
        title="Jane Founder at Future Labs | LinkedIn",
        description="Building applied AI tools.",
        experience_entries=[
            "Founder | Future Labs · Full-time | Jan 2021 - Present",
            "Founder | Future Labs · Full-time | Jan 2021 - Present",
            "Engineer | Prior Co | 2018 - 2020",
        ],
    )

    values_by_attribute = {fact["attribute"]: fact["value"] for fact in facts if fact["attribute"] != "experience"}
    assert values_by_attribute["headline"] == "Jane Founder at Future Labs"
    assert values_by_attribute["about"] == "Building applied AI tools."
    assert values_by_attribute["current_company"] == "Future Labs"
    experience_values = [fact["value"] for fact in facts if fact["attribute"] == "experience"]
    assert experience_values == [
        "Founder | Future Labs · Full-time | Jan 2021 - Present",
        "Engineer | Prior Co | 2018 - 2020",
    ]


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


def test_unsupported_reason_does_not_flag_profile_with_captcha_script_only() -> None:
    assert (
        _unsupported_reason(
            source=SupportedSource.linkedin,
            url="https://www.linkedin.com/in/founder/",
            title="Jane Founder - Founder - Future Labs | LinkedIn",
            html=(
                "<html><head>"
                "<script src='https://www.google.com/recaptcha/api.js'></script>"
                "</head><body>"
                "<h1>Jane Founder</h1><p>Founder at Future Labs</p>"
                "</body></html>"
            ),
        )
        is None
    )


def test_unsupported_reason_prefers_login_reason_over_captcha_tokens() -> None:
    assert (
        _unsupported_reason(
            source=SupportedSource.linkedin,
            url="https://www.linkedin.com/login",
            title="Sign in | LinkedIn",
            html="<script>var x='captcha';</script><div>Sign in</div>",
        )
        == "authenticated linkedin session required"
    )


def test_resolve_source_prefers_argument_over_environment(monkeypatch) -> None:
    monkeypatch.setenv("KB_ENRICHMENT_EXTRACT_SOURCE", "skool.com")
    assert _resolve_source("linkedin.com") == SupportedSource.linkedin


def test_deduplicate_fact_rows_removes_exact_duplicates() -> None:
    deduplicated = _deduplicate_fact_rows(
        [
            {
                "attribute": "headline",
                "value": "Founder",
                "confidence": "medium",
                "metadata": {"extractor": "playwright-default"},
            },
            {
                "attribute": "headline",
                "value": "Founder",
                "confidence": "medium",
                "metadata": {"extractor": "playwright-default"},
            },
            {
                "attribute": "about",
                "value": "Building products",
                "confidence": "low",
                "metadata": {"extractor": "playwright-default"},
            },
        ]
    )
    assert deduplicated == [
        {
            "attribute": "headline",
            "value": "Founder",
            "confidence": "medium",
            "metadata": {"extractor": "playwright-default"},
        },
        {
            "attribute": "about",
            "value": "Building products",
            "confidence": "low",
            "metadata": {"extractor": "playwright-default"},
        },
    ]
