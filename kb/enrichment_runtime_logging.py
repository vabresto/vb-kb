from __future__ import annotations

import os
import sys
from collections.abc import Mapping

_RUNTIME_LOGS_ENV = "KB_ENRICHMENT_RUNTIME_LOGS"


def runtime_logs_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return _parse_bool(env.get(_RUNTIME_LOGS_ENV), default=True)


def runtime_log(component: str, message: str, *, environ: Mapping[str, str] | None = None) -> None:
    if not runtime_logs_enabled(environ):
        return
    print(f"[kb-enrichment][{component}] {message}", file=sys.stderr)


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default
