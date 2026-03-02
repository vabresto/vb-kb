from __future__ import annotations

import os
import random
from dataclasses import dataclass
from typing import Any, Mapping

_RANDOM_WAITS_ENV = "KB_ENRICHMENT_ACTION_RANDOM_WAITS"
_RANDOM_WAIT_MIN_MS_ENV = "KB_ENRICHMENT_ACTION_RANDOM_WAIT_MIN_MS"
_RANDOM_WAIT_MAX_MS_ENV = "KB_ENRICHMENT_ACTION_RANDOM_WAIT_MAX_MS"
_RANDOM_HUMAN_ACTIONS_ENV = "KB_ENRICHMENT_ACTION_RANDOM_HUMAN_ACTIONS"
_MAX_WAIT_MS = 10_000
_DEFAULT_MIN_WAIT_MS = 220
_DEFAULT_MAX_WAIT_MS = 900
_MIN_HUMAN_ACTION_WAIT_MS = 120


@dataclass(frozen=True)
class RandomWaitSettings:
    enabled: bool = True
    min_ms: int = _DEFAULT_MIN_WAIT_MS
    max_ms: int = _DEFAULT_MAX_WAIT_MS
    human_actions: bool = True


def parse_random_wait_settings(environ: Mapping[str, str] | None = None) -> RandomWaitSettings:
    env = os.environ if environ is None else environ
    enabled = _parse_bool(env.get(_RANDOM_WAITS_ENV), default=True)
    min_ms = _parse_wait_ms(env.get(_RANDOM_WAIT_MIN_MS_ENV), default=_DEFAULT_MIN_WAIT_MS)
    max_ms = _parse_wait_ms(env.get(_RANDOM_WAIT_MAX_MS_ENV), default=_DEFAULT_MAX_WAIT_MS)
    human_actions = _parse_bool(env.get(_RANDOM_HUMAN_ACTIONS_ENV), default=True)
    if max_ms < min_ms:
        min_ms, max_ms = max_ms, min_ms
    return RandomWaitSettings(
        enabled=enabled,
        min_ms=min_ms,
        max_ms=max_ms,
        human_actions=human_actions,
    )


def wait_random_delay(
    page: Any,
    settings: RandomWaitSettings,
    *,
    minimum_ms: int | None = None,
    maximum_ms: int | None = None,
    rng: random.Random | None = None,
) -> int:
    if not settings.enabled:
        return 0

    lower = settings.min_ms if minimum_ms is None else max(0, min(minimum_ms, _MAX_WAIT_MS))
    upper = settings.max_ms if maximum_ms is None else max(0, min(maximum_ms, _MAX_WAIT_MS))
    if upper < lower:
        upper = lower

    generator = rng or random
    wait_ms = generator.randint(lower, upper)
    if wait_ms > 0:
        page.wait_for_timeout(wait_ms)
    return wait_ms


def wait_humanized_delay(
    page: Any,
    settings: RandomWaitSettings,
    *,
    minimum_ms: int | None = None,
    maximum_ms: int | None = None,
    rng: random.Random | None = None,
    allow_actions: bool = True,
) -> int:
    wait_ms = wait_random_delay(
        page,
        settings,
        minimum_ms=minimum_ms,
        maximum_ms=maximum_ms,
        rng=rng,
    )
    if wait_ms <= 0:
        return 0
    if not allow_actions or not settings.human_actions or wait_ms < _MIN_HUMAN_ACTION_WAIT_MS:
        return wait_ms

    generator = rng or random
    action_budget = 1 if wait_ms >= _MIN_HUMAN_ACTION_WAIT_MS else 0
    if wait_ms >= 700:
        action_budget = 2
    action_count = generator.randint(0, action_budget)
    if action_count <= 0:
        return wait_ms

    consumed_wait_ms = 0
    for _ in range(action_count):
        _perform_random_human_action(page, generator=generator)
        settle_ms = generator.randint(30, 140)
        try:
            page.wait_for_timeout(settle_ms)
            consumed_wait_ms += settle_ms
        except Exception:
            continue
    return wait_ms + consumed_wait_ms


def _perform_random_human_action(page: Any, *, generator: random.Random) -> None:
    actions = (
        _random_mouse_move,
        _random_scroll_nudge,
        _random_scroll_nudge,
    )
    action = generator.choice(actions)
    try:
        action(page, generator=generator)
    except Exception:
        return


def _random_mouse_move(page: Any, *, generator: random.Random) -> None:
    width = 1_280
    height = 720
    try:
        viewport = getattr(page, "viewport_size", None)
        if isinstance(viewport, dict):
            width = int(viewport.get("width", width))
            height = int(viewport.get("height", height))
    except Exception:
        pass
    target_x = generator.randint(max(1, width // 8), max(2, (width * 7) // 8))
    target_y = generator.randint(max(1, height // 8), max(2, (height * 7) // 8))
    steps = generator.randint(4, 16)
    page.mouse.move(target_x, target_y, steps=steps)


def _random_scroll_nudge(page: Any, *, generator: random.Random) -> None:
    direction = -1 if generator.randint(0, 1) == 0 else 1
    delta = direction * generator.randint(70, 240)
    try:
        page.mouse.wheel(0, delta)
        return
    except Exception:
        pass
    page.evaluate("delta => window.scrollBy(0, delta)", delta)


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_wait_ms(value: str | None, *, default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value.strip())
    except ValueError:
        return default
    return max(0, min(parsed, _MAX_WAIT_MS))
