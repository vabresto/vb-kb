from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from kb import mcp_server


def _run_git(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(project_root), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def _init_repo(tmp_path: Path) -> tuple[Path, Path]:
    project_root = tmp_path / "repo"
    data_root = project_root / "data"
    data_root.mkdir(parents=True)

    _run_git(project_root, "init")
    _run_git(project_root, "config", "user.email", "tests@example.com")
    _run_git(project_root, "config", "user.name", "Tests")

    (project_root / ".gitignore").write_text(".kb-write.lock\n", encoding="utf-8")
    _run_git(project_root, "add", ".")
    commit = _run_git(project_root, "commit", "-m", "test init")
    assert commit.returncode == 0, commit.stderr

    return project_root, data_root


def _call_tool(server, name: str, arguments: dict[str, object]) -> dict[str, object]:
    result = asyncio.run(server.call_tool(name, arguments))
    return result.structured_content


def test_run_transaction_rolls_back_new_file_on_validation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, data_root = _init_repo(tmp_path)
    target = data_root / "note" / "te" / "note@test-note" / "index.md"

    def apply_changes() -> dict[str, object]:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("---\nid: note@test-note\n---\n\ncontent\n", encoding="utf-8")
        return {"index_path": "data/note/te/note@test-note/index.md"}

    monkeypatch.setattr(
        mcp_server,
        "run_validation",
        lambda **_: {
            "ok": False,
            "scope": "mcp-transaction",
            "error_count": 1,
            "errors": [{"code": "schema_error"}],
        },
    )

    result = mcp_server.run_transaction(
        project_root=project_root,
        data_root=data_root,
        commit_message="test commit",
        apply_changes=apply_changes,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "validation_failed"
    assert not target.exists()

    status = _run_git(project_root, "status", "--short", "--", data_root.relative_to(project_root).as_posix())
    assert status.stdout.strip() == ""


def test_run_transaction_rolls_back_modified_file_on_commit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, data_root = _init_repo(tmp_path)
    target = data_root / "note" / "te" / "note@test-note" / "index.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("before\n", encoding="utf-8")
    _run_git(project_root, "add", ".")
    commit = _run_git(project_root, "commit", "-m", "add note")
    assert commit.returncode == 0, commit.stderr

    monkeypatch.setattr(
        mcp_server,
        "run_validation",
        lambda **_: {
            "ok": True,
            "scope": "mcp-transaction",
            "error_count": 0,
            "errors": [],
        },
    )

    original_run_git = mcp_server.run_git

    def run_git_with_commit_failure(
        project_root_value: Path,
        args: list[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if args and args[0] == "commit":
            return subprocess.CompletedProcess(args=["git", *args], returncode=1, stdout="", stderr="forced commit failure")
        return original_run_git(project_root_value, args, check=check)

    monkeypatch.setattr(mcp_server, "run_git", run_git_with_commit_failure)

    def apply_changes() -> dict[str, object]:
        target.write_text("after\n", encoding="utf-8")
        return {"index_path": "data/note/te/note@test-note/index.md"}

    result = mcp_server.run_transaction(
        project_root=project_root,
        data_root=data_root,
        commit_message="test commit",
        apply_changes=apply_changes,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "commit_failed"
    assert target.read_text(encoding="utf-8") == "before\n"

    status = _run_git(project_root, "status", "--short", "--", target.relative_to(project_root).as_posix())
    assert status.stdout.strip() == ""


def test_upsert_note_requires_auth_token_when_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, data_root = _init_repo(tmp_path)
    monkeypatch.setenv(mcp_server.AUTH_TOKEN_ENV_VAR, "secret-token")

    called = {"value": False}

    def fake_run_transaction(**_: object) -> dict[str, object]:
        called["value"] = True
        return {"ok": True, "committed": False, "changed_paths": [], "apply": {}, "validation": None}

    monkeypatch.setattr(mcp_server, "run_transaction", fake_run_transaction)

    server = mcp_server.create_mcp_server(project_root=project_root, data_root=data_root)
    result = _call_tool(server, "upsert_note", {"slug": "auth-check-note"})

    assert result["ok"] is False
    assert result["error"]["code"] == "unauthorized"
    assert called["value"] is False


def test_upsert_note_accepts_valid_auth_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, data_root = _init_repo(tmp_path)
    monkeypatch.setenv(mcp_server.AUTH_TOKEN_ENV_VAR, "secret-token")

    called = {"value": False}

    def fake_run_transaction(**_: object) -> dict[str, object]:
        called["value"] = True
        return {
            "ok": True,
            "committed": False,
            "changed_paths": [],
            "apply": {"slug": "auth-check-note"},
            "validation": None,
        }

    monkeypatch.setattr(mcp_server, "run_transaction", fake_run_transaction)

    server = mcp_server.create_mcp_server(project_root=project_root, data_root=data_root)
    result = _call_tool(
        server,
        "upsert_note",
        {"slug": "auth-check-note", "auth_token": "secret-token"},
    )

    assert result["ok"] is True
    assert called["value"] is True


def test_upsert_note_maps_busy_lock_to_retryable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, data_root = _init_repo(tmp_path)

    def raise_busy(**_: object) -> dict[str, object]:
        raise mcp_server.BusyLockError("lock is busy")

    monkeypatch.setattr(mcp_server, "run_transaction", raise_busy)

    server = mcp_server.create_mcp_server(project_root=project_root, data_root=data_root)
    result = _call_tool(server, "upsert_note", {"slug": "busy-lock-note"})

    assert result["ok"] is False
    assert result["error"]["code"] == "busy"
    assert result["error"]["retryable"] is True
