from __future__ import annotations

import os
import random
from dataclasses import dataclass
from typing import Any, Mapping

_RANDOM_WAITS_ENV = "KB_ENRICHMENT_ACTION_RANDOM_WAITS"
_RANDOM_WAIT_MIN_MS_ENV = "KB_ENRICHMENT_ACTION_RANDOM_WAIT_MIN_MS"
_RANDOM_WAIT_MAX_MS_ENV = "KB_ENRICHMENT_ACTION_RANDOM_WAIT_MAX_MS"
_MAX_WAIT_MS = 10_000
_DEFAULT_MIN_WAIT_MS = 220
_DEFAULT_MAX_WAIT_MS = 900


@dataclass(frozen=True)
class RandomWaitSettings:
    enabled: bool = True
    min_ms: int = _DEFAULT_MIN_WAIT_MS
    max_ms: int = _DEFAULT_MAX_WAIT_MS


def parse_random_wait_settings(environ: Mapping[str, str] | None = None) -> RandomWaitSettings:
    env = os.environ if environ is None else environ
    enabled = _parse_bool(env.get(_RANDOM_WAITS_ENV), default=True)
    min_ms = _parse_wait_ms(env.get(_RANDOM_WAIT_MIN_MS_ENV), default=_DEFAULT_MIN_WAIT_MS)
    max_ms = _parse_wait_ms(env.get(_RANDOM_WAIT_MAX_MS_ENV), default=_DEFAULT_MAX_WAIT_MS)
    if max_ms < min_ms:
        min_ms, max_ms = max_ms, min_ms
    return RandomWaitSettings(enabled=enabled, min_ms=min_ms, max_ms=max_ms)


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
