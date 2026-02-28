from __future__ import annotations

import random

from kb.enrichment_playwright_timing import RandomWaitSettings, parse_random_wait_settings, wait_random_delay


def test_parse_random_wait_settings_defaults_to_enabled() -> None:
    settings = parse_random_wait_settings({})
    assert settings.enabled is True
    assert settings.min_ms == 220
    assert settings.max_ms == 900


def test_parse_random_wait_settings_applies_env_overrides() -> None:
    settings = parse_random_wait_settings(
        {
            "KB_ENRICHMENT_ACTION_RANDOM_WAITS": "false",
            "KB_ENRICHMENT_ACTION_RANDOM_WAIT_MIN_MS": "120",
            "KB_ENRICHMENT_ACTION_RANDOM_WAIT_MAX_MS": "480",
        }
    )
    assert settings.enabled is False
    assert settings.min_ms == 120
    assert settings.max_ms == 480


def test_wait_random_delay_respects_disabled_setting() -> None:
    page = _PageStub()
    delay = wait_random_delay(page, RandomWaitSettings(enabled=False, min_ms=10, max_ms=20), rng=random.Random(1))
    assert delay == 0
    assert page.waits == []


def test_wait_random_delay_uses_expected_range() -> None:
    page = _PageStub()
    delay = wait_random_delay(
        page,
        RandomWaitSettings(enabled=True, min_ms=10, max_ms=20),
        rng=random.Random(7),
    )
    assert 10 <= delay <= 20
    assert page.waits == [delay]


class _PageStub:
    def __init__(self) -> None:
        self.waits: list[int] = []

    def wait_for_timeout(self, value: int) -> None:
        self.waits.append(value)
