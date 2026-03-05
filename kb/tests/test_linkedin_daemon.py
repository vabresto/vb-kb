from __future__ import annotations

from pathlib import Path

import pytest

from kb.linkedin_daemon import (
    DAEMON_MODE_AUTONOMOUS,
    DAEMON_MODE_HUMAN,
    build_mode_state,
    command_allowed_in_mode,
    load_state,
    persist_state,
)


def test_build_mode_state_normalizes_mode_and_actor() -> None:
    state = build_mode_state(mode="autonomous", actor=" human ", reason="r")
    assert state["mode"] == DAEMON_MODE_AUTONOMOUS
    assert state["updated_by"] == "human"
    assert state["reason"] == "r"


def test_load_state_defaults_when_missing(tmp_path: Path) -> None:
    state = load_state(tmp_path / "missing.json")
    assert state["mode"] == DAEMON_MODE_AUTONOMOUS


def test_persist_and_load_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = build_mode_state(mode=DAEMON_MODE_HUMAN, actor="tester", reason="manual")
    persist_state(path, state)
    loaded = load_state(path)
    assert loaded["mode"] == DAEMON_MODE_HUMAN
    assert loaded["updated_by"] == "tester"
    assert loaded["reason"] == "manual"


@pytest.mark.parametrize(
    ("mode", "cmd", "allowed"),
    [
        (DAEMON_MODE_AUTONOMOUS, "open_people_search", True),
        (DAEMON_MODE_AUTONOMOUS, "current_page", True),
        (DAEMON_MODE_HUMAN, "open_people_search", False),
        (DAEMON_MODE_HUMAN, "current_page", True),
    ],
)
def test_command_allowed_in_mode(mode: str, cmd: str, allowed: bool) -> None:
    assert command_allowed_in_mode(mode=mode, cmd=cmd) is allowed

