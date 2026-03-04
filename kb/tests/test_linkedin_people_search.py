from __future__ import annotations

from kb.linkedin_people_search import (
    canonical_profile_url,
    is_nyc_text,
    parse_degree,
    parse_mutuals,
    parse_org,
)


def test_canonical_profile_url_normalizes_in_path() -> None:
    assert (
        canonical_profile_url("http://www.linkedin.com/in/jane-doe")
        == "https://www.linkedin.com/in/jane-doe/"
    )


def test_canonical_profile_url_rejects_non_profile_paths() -> None:
    assert canonical_profile_url("https://www.linkedin.com/feed/") == ""


def test_is_nyc_text_detects_common_nyc_locations() -> None:
    assert is_nyc_text("New York City Metropolitan Area")
    assert is_nyc_text("Based in Manhattan, New York")
    assert not is_nyc_text("San Francisco Bay Area")


def test_parse_org_prefers_at_delimiter() -> None:
    assert parse_org("Director of Claims Operations at Chubb") == "Chubb"
    assert parse_org("VP Operations @ Travelers") == "Travelers"


def test_parse_degree_reads_standard_connection_labels() -> None:
    assert parse_degree("2nd") == "2nd"
    assert parse_degree("Connect | 3rd") == "3rd"
    assert parse_degree("No degree marker") == ""


def test_parse_mutuals_handles_other_count_variant() -> None:
    names, total = parse_mutuals("Alice Smith and Bob Jones and 12 other mutual connections")
    assert names == "Alice Smith; Bob Jones"
    assert total == 14


def test_parse_mutuals_handles_single_variant() -> None:
    names, total = parse_mutuals("Charlie Day is a mutual connection")
    assert names == "Charlie Day"
    assert total == 1

