from __future__ import annotations

import random

from kb.enrichment_playwright_timing import (
    RandomWaitSettings,
    parse_random_wait_settings,
    wait_humanized_delay,
    wait_random_delay,
)


def test_parse_random_wait_settings_defaults_to_enabled() -> None:
    settings = parse_random_wait_settings({})
    assert settings.enabled is True
    assert settings.min_ms == 220
    assert settings.max_ms == 900
    assert settings.human_actions is True


def test_parse_random_wait_settings_applies_env_overrides() -> None:
    settings = parse_random_wait_settings(
        {
            "KB_ENRICHMENT_ACTION_RANDOM_WAITS": "false",
            "KB_ENRICHMENT_ACTION_RANDOM_WAIT_MIN_MS": "120",
            "KB_ENRICHMENT_ACTION_RANDOM_WAIT_MAX_MS": "480",
            "KB_ENRICHMENT_ACTION_RANDOM_HUMAN_ACTIONS": "false",
        }
    )
    assert settings.enabled is False
    assert settings.min_ms == 120
    assert settings.max_ms == 480
    assert settings.human_actions is False


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


def test_wait_humanized_delay_skips_actions_when_disabled() -> None:
    page = _PageStub()
    delay = wait_humanized_delay(
        page,
        RandomWaitSettings(enabled=True, min_ms=200, max_ms=200, human_actions=False),
        rng=random.Random(1),
    )
    assert delay == 200
    assert page.waits == [200]
    assert page.mouse.moves == []
    assert page.mouse.wheels == []


def test_wait_humanized_delay_performs_noop_actions() -> None:
    page = _PageStub()
    delay = wait_humanized_delay(
        page,
        RandomWaitSettings(enabled=True, min_ms=500, max_ms=500, human_actions=True),
        rng=_DeterministicRng([500, 1, 1, 120, 60]),
    )
    assert delay == 560
    assert page.waits == [500, 60]
    assert page.mouse.wheels == [(0, 120)]


class _PageStub:
    def __init__(self) -> None:
        self.waits: list[int] = []
        self.mouse = _MouseStub()
        self.viewport_size = {"width": 1280, "height": 720}
        self.evaluated: list[tuple[str, object]] = []

    def wait_for_timeout(self, value: int) -> None:
        self.waits.append(value)

    def evaluate(self, expression: str, arg: object) -> None:
        self.evaluated.append((expression, arg))


class _MouseStub:
    def __init__(self) -> None:
        self.moves: list[tuple[int, int, int]] = []
        self.wheels: list[tuple[int, int]] = []

    def move(self, x: int, y: int, *, steps: int) -> None:
        self.moves.append((x, y, steps))

    def wheel(self, dx: int, dy: int) -> None:
        self.wheels.append((dx, dy))


class _DeterministicRng:
    def __init__(self, sequence: list[int]) -> None:
        self._sequence = list(sequence)

    def randint(self, minimum: int, maximum: int) -> int:
        if not self._sequence:
            raise AssertionError("rng sequence exhausted")
        value = self._sequence.pop(0)
        if value < minimum or value > maximum:
            raise AssertionError(f"value {value} out of range [{minimum}, {maximum}]")
        return value

    def choice(self, values):
        if len(values) > 1:
            return values[1]
        return values[0]
