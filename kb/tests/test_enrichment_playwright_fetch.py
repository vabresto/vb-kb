from __future__ import annotations

from kb.enrichment_config import SupportedSource
from kb.enrichment_playwright_fetch import (
    _canonical_profile_url,
    _deduplicate_fact_rows,
    _extract_linkedin_facts,
    _extract_skool_facts,
    _normalize_linkedin_detail_url,
    _profile_resolution_reason,
    _resolve_source,
    _search_query_from_slug,
    _select_best_profile_candidate,
    _unsupported_reason,
    _unwrap_search_redirect_url,
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
    current_company_facts = [fact for fact in facts if fact["attribute"] == "current_company"]
    assert len(current_company_facts) == 1
    assert current_company_facts[0]["confidence"] == "medium"
    experience_values = [fact["value"] for fact in facts if fact["attribute"] == "experience"]
    experience_confidences = [fact["confidence"] for fact in facts if fact["attribute"] == "experience"]
    assert experience_values == [
        "Founder | Future Labs · Full-time | Jan 2021 - Present",
        "Engineer | Prior Co | 2018 - 2020",
    ]
    assert experience_confidences == ["medium", "medium"]
    current_role_fact = next(fact for fact in facts if fact["attribute"] == "current_role")
    assert current_role_fact["confidence"] == "medium"


def test_extract_linkedin_facts_strips_badge_prefix_and_prefers_profile_headline() -> None:
    facts = _extract_linkedin_facts(
        title="(2) Jose Luis Avilez | LinkedIn",
        description="",
        profile_headline="Quantitative Researcher at Squarepoint Capital",
        section_entries=[
            "Experience | Quantitative Researcher | Squarepoint Capital",
            "Publications | Efficient Portfolio Construction",
            "Publications | Efficient Portfolio Construction",
        ],
    )

    values_by_attribute = {fact["attribute"]: fact["value"] for fact in facts if fact["attribute"] != "section_entry"}
    assert values_by_attribute["headline"] == "Quantitative Researcher at Squarepoint Capital"
    assert values_by_attribute["current_company"] == "Squarepoint Capital"
    current_company_fact = next(fact for fact in facts if fact["attribute"] == "current_company")
    assert current_company_fact["confidence"] == "medium"
    section_values = [fact["value"] for fact in facts if fact["attribute"] == "section_entry"]
    assert section_values == [
        "Experience | Quantitative Researcher | Squarepoint Capital",
        "Publications | Efficient Portfolio Construction",
    ]


def test_extract_linkedin_facts_strips_badge_prefix_from_title_fallback() -> None:
    facts = _extract_linkedin_facts(
        title="(7) Jane Founder | LinkedIn",
        description=None,
    )
    values_by_attribute = {fact["attribute"]: fact["value"] for fact in facts}
    assert values_by_attribute["headline"] == "Jane Founder"


def test_extract_skool_facts_from_title_and_description() -> None:
    facts = _extract_skool_facts(
        title="Founders Circle - Jane Founder | Skool",
        description="Community for startup builders.",
        profile_entries=[
            "Community: Founders Circle | 4,201 members",
            "Community: Founders Circle | 4,201 members",
            "Top post: Hiring AI engineers",
        ],
    )

    values_by_attribute = {fact["attribute"]: fact["value"] for fact in facts if fact["attribute"] != "profile_entry"}
    assert values_by_attribute["headline"] == "Founders Circle - Jane Founder"
    assert values_by_attribute["community"] == "Founders Circle"
    assert values_by_attribute["about"] == "Community for startup builders."
    profile_values = [fact["value"] for fact in facts if fact["attribute"] == "profile_entry"]
    assert profile_values == [
        "Community: Founders Circle | 4,201 members",
        "Top post: Hiring AI engineers",
    ]


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


def test_profile_resolution_reason_detects_non_profile_url() -> None:
    reason = _profile_resolution_reason(
        source=SupportedSource.linkedin,
        url="https://www.linkedin.com/search/results/people/?keywords=jane",
        title="People Search | LinkedIn",
        html="<html><body>People results</body></html>",
    )
    assert reason == "did not land on a linkedin.com profile page"


def test_profile_resolution_reason_detects_missing_profile_text() -> None:
    reason = _profile_resolution_reason(
        source=SupportedSource.skool,
        url="https://www.skool.com/@missing-user",
        title="Page not found | Skool",
        html="<html><body>This page is unavailable</body></html>",
    )
    assert reason == "skool.com profile not found"


def test_search_query_from_slug_normalizes_tokens() -> None:
    assert _search_query_from_slug("jose-luis_avilez") == "jose luis avilez"


def test_unwrap_search_redirect_url_decodes_duckduckgo_target() -> None:
    wrapped = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.linkedin.com%2Fin%2Fjane-founder%2F"
    assert _unwrap_search_redirect_url(wrapped) == "https://www.linkedin.com/in/jane-founder/"


def test_canonical_profile_url_normalizes_source_specific_paths() -> None:
    linkedin = _canonical_profile_url(
        SupportedSource.linkedin,
        "https://www.linkedin.com/in/Jane-Founder/?trk=abc",
    )
    skool = _canonical_profile_url(
        SupportedSource.skool,
        "https://www.skool.com/@jane-founder/?a=1",
    )
    assert linkedin == "https://www.linkedin.com/in/Jane-Founder/"
    assert skool == "https://www.skool.com/@jane-founder"


def test_normalize_linkedin_detail_url_filters_to_known_sections_for_profile() -> None:
    experience_url = "hxxps://www.linkedin.com/in/Jane-Founder/details/experience/?trk=public_profile".replace(
        "hxxps://", "https://"
    )
    people_also_viewed_url = "hxxps://www.linkedin.com/in/jane-founder/details/people-also-viewed/".replace(
        "hxxps://", "https://"
    )
    other_profile_url = "hxxps://www.linkedin.com/in/another-person/details/experience/".replace(
        "hxxps://", "https://"
    )
    normalized_expected = "hxxps://www.linkedin.com/in/jane-founder/details/experience/".replace(
        "hxxps://", "https://"
    )
    assert (
        _normalize_linkedin_detail_url(
            experience_url,
            profile_slug="jane-founder",
        )
        == normalized_expected
    )
    assert (
        _normalize_linkedin_detail_url(
            people_also_viewed_url,
            profile_slug="jane-founder",
        )
        is None
    )
    assert (
        _normalize_linkedin_detail_url(
            other_profile_url,
            profile_slug="jane-founder",
        )
        is None
    )


def test_select_best_profile_candidate_prefers_slug_match() -> None:
    selected = _select_best_profile_candidate(
        source=SupportedSource.linkedin,
        entity_slug="jose-luis-avilez",
        candidates=[
            "https://www.linkedin.com/in/jose-luis/",
            "https://www.linkedin.com/in/jose-luis-avilez-123456/",
            "https://www.linkedin.com/in/jose/",
        ],
    )
    assert selected == "https://www.linkedin.com/in/jose-luis-avilez-123456/"
