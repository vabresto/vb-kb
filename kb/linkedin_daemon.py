from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DAEMON_MODE_AUTONOMOUS = "autonomous"
DAEMON_MODE_HUMAN = "human_control"
DAEMON_MODES = {DAEMON_MODE_AUTONOMOUS, DAEMON_MODE_HUMAN}

_AUTONOMOUS_ONLY_COMMANDS = {
    "assert_authenticated",
    "open_people_search",
    "scrape_people_cards",
    "next_page",
    "sleep",
}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def normalize_mode(value: str | None) -> str:
    mode = (value or "").strip().lower()
    if mode not in DAEMON_MODES:
        raise ValueError(f"mode must be one of: {', '.join(sorted(DAEMON_MODES))}")
    return mode


def default_state() -> dict[str, Any]:
    return {
        "mode": DAEMON_MODE_AUTONOMOUS,
        "updated_at": utc_now_iso(),
        "updated_by": "daemon-startup",
        "reason": "",
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_state()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return default_state()
    try:
        mode = normalize_mode(str(payload.get("mode")))
    except ValueError:
        mode = DAEMON_MODE_AUTONOMOUS
    return {
        "mode": mode,
        "updated_at": str(payload.get("updated_at") or utc_now_iso()),
        "updated_by": str(payload.get("updated_by") or "daemon-startup"),
        "reason": str(payload.get("reason") or ""),
    }


def persist_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_mode_state(*, mode: str, actor: str, reason: str) -> dict[str, Any]:
    return {
        "mode": normalize_mode(mode),
        "updated_at": utc_now_iso(),
        "updated_by": actor.strip() or "unknown",
        "reason": reason.strip(),
    }


def command_allowed_in_mode(*, mode: str, cmd: str) -> bool:
    normalized_mode = normalize_mode(mode)
    if normalized_mode == DAEMON_MODE_AUTONOMOUS:
        return True
    return cmd not in _AUTONOMOUS_ONLY_COMMANDS

