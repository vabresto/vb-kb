from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from kb import mcp_server
from kb import semantic


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


def _init_repo_with_remote(tmp_path: Path) -> tuple[Path, Path, Path]:
    project_root, data_root = _init_repo(tmp_path)
    remote_root = tmp_path / "remote.git"
    remote_init = subprocess.run(
        ["git", "init", "--bare", str(remote_root)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert remote_init.returncode == 0, remote_init.stderr

    rename = _run_git(project_root, "branch", "-M", "main")
    assert rename.returncode == 0, rename.stderr
    add_remote = _run_git(project_root, "remote", "add", "origin", str(remote_root))
    assert add_remote.returncode == 0, add_remote.stderr
    push = _run_git(project_root, "push", "-u", "origin", "main")
    assert push.returncode == 0, push.stderr
    return project_root, data_root, remote_root


def _write_minimal_mkdocs_files(project_root: Path) -> None:
    (project_root / ".build" / "docs").mkdir(parents=True, exist_ok=True)
    (project_root / "mkdocs_hooks.py").write_text(
        (
            "from pathlib import Path\n"
            "from kb.tools.build_site_content import build_site_content\n\n"
            "def on_pre_build(config) -> None:\n"
            "    project_root = Path(config.config_file_path).resolve().parent\n"
            "    build_site_content(project_root=project_root)\n"
        ),
        encoding="utf-8",
    )
    (project_root / "mkdocs.yml").write_text(
        (
            "site_name: MCP Test KB\n"
            "site_description: MCP integration test\n"
            "docs_dir: .build/docs\n"
            "site_dir: .build/site\n"
            "hooks:\n"
            "  - mkdocs_hooks.py\n"
            "validation:\n"
            "  nav:\n"
            "    omitted_files: ignore\n"
            "nav:\n"
            "  - Home: index.md\n"
            "  - Sources: sources.md\n"
        ),
        encoding="utf-8",
    )


def _call_tool(server, name: str, arguments: dict[str, object]) -> dict[str, object]:
    result = asyncio.run(server.call_tool(name, arguments))
    return result.structured_content


def test_run_transaction_rolls_back_new_file_on_validation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, data_root = _init_repo(tmp_path)
    target = data_root / "source" / "te" / "source@test-note" / "index.md"

    def apply_changes() -> dict[str, object]:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("---\nid: source@test-note\n---\n\ncontent\n", encoding="utf-8")
        return {"index_path": "data/source/te/source@test-note/index.md"}

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
    target = data_root / "source" / "te" / "source@test-note" / "index.md"
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
        return {"index_path": "data/source/te/source@test-note/index.md"}

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


def test_run_transaction_rejects_non_data_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, data_root = _init_repo(tmp_path)
    gitignore = project_root / ".gitignore"
    original_gitignore = gitignore.read_text(encoding="utf-8")
    data_target = data_root / "source" / "te" / "source@test-source" / "index.md"

    validation_called = {"value": False}

    def fake_validation(**_: object) -> dict[str, object]:
        validation_called["value"] = True
        return {"ok": True, "scope": "mcp-transaction", "error_count": 0, "errors": []}

    monkeypatch.setattr(mcp_server, "run_validation", fake_validation)

    def apply_changes() -> dict[str, object]:
        gitignore.write_text(original_gitignore + "# non-data change\n", encoding="utf-8")
        data_target.parent.mkdir(parents=True, exist_ok=True)
        data_target.write_text("---\nid: source@test-source\n---\n", encoding="utf-8")
        return {"index_path": "data/source/te/source@test-source/index.md"}

    result = mcp_server.run_transaction(
        project_root=project_root,
        data_root=data_root,
        commit_message="test non-data guard",
        apply_changes=apply_changes,
        push=False,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "non_data_changes"
    assert validation_called["value"] is False
    assert gitignore.read_text(encoding="utf-8") == original_gitignore
    assert not data_target.exists()
    assert any(path == ".gitignore" for path in result["non_data_changed_paths"])

    status = _run_git(project_root, "status", "--short")
    assert status.stdout.strip() == ""


def test_upsert_source_commits_pushes_and_keeps_repo_valid(tmp_path: Path) -> None:
    project_root, data_root, remote_root = _init_repo_with_remote(tmp_path)
    server = mcp_server.create_mcp_server(project_root=project_root, data_root=data_root)
    result = _call_tool(
        server,
        "upsert_source",
        {
            "slug": "mcp-created-source",
            "frontmatter": {
                "title": "MCP Created Source",
                "source-category": "citations/mcp",
                "url": "https://example.com/mcp-created-source",
            },
            "body": "Created through MCP for integration testing.",
        },
    )

    assert result["ok"] is True
    assert result["committed"] is True
    assert result["pushed"] is True
    assert result["commit"]
    assert result["changed_paths"]
    assert all(path.startswith("data/") for path in result["changed_paths"])

    source_path = data_root / "source" / "mc" / "source@mcp-created-source" / "index.md"
    assert source_path.exists()
    assert "MCP Created Source" in source_path.read_text(encoding="utf-8")

    validation_result = mcp_server.run_validation(
        project_root=project_root,
        data_root=data_root,
        scope_paths=None,
        scope_label="test-full",
    )
    assert validation_result["ok"] is True

    local_sha = _run_git(project_root, "rev-parse", "HEAD").stdout.strip()
    remote_sha = subprocess.run(
        ["git", "-C", str(remote_root), "rev-parse", "refs/heads/main"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert remote_sha.returncode == 0, remote_sha.stderr
    assert local_sha == remote_sha.stdout.strip()

    status = _run_git(project_root, "status", "--short")
    assert status.stdout.strip() == ""


def test_upsert_source_changes_are_reflected_by_mkdocs_build(tmp_path: Path) -> None:
    project_root, data_root = _init_repo(tmp_path)
    _write_minimal_mkdocs_files(project_root)
    _run_git(project_root, "add", "mkdocs.yml", "mkdocs_hooks.py")
    setup_commit = _run_git(project_root, "commit", "-m", "add mkdocs config for test")
    assert setup_commit.returncode == 0, setup_commit.stderr

    server = mcp_server.create_mcp_server(project_root=project_root, data_root=data_root)
    result = _call_tool(
        server,
        "upsert_source",
        {
            "slug": "build-reflection-source",
            "frontmatter": {
                "title": "Build Reflection Source",
                "source-category": "citations/mcp",
                "url": "https://example.com/build-reflection-source",
            },
            "body": "This source should appear in generated sources index after mkdocs build.",
            "push": False,
        },
    )

    assert result["ok"] is True
    assert result["committed"] is True
    assert result["pushed"] is False
    assert all(path.startswith("data/") for path in result["changed_paths"])

    build = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "-f", str(project_root / "mkdocs.yml")],
        cwd=project_root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr or build.stdout

    sources_index = project_root / ".build" / "docs" / "sources.md"
    assert sources_index.exists()
    sources_text = sources_index.read_text(encoding="utf-8")
    assert "Build Reflection Source" in sources_text


def test_upsert_source_sets_allow_orphan_source_flag(tmp_path: Path) -> None:
    project_root, data_root = _init_repo(tmp_path)
    server = mcp_server.create_mcp_server(project_root=project_root, data_root=data_root)

    default_result = _call_tool(
        server,
        "upsert_source",
        {
            "slug": "default-orphan-flag-source",
            "frontmatter": {
                "title": "Default Orphan Flag Source",
                "source-category": "citations/mcp",
                "url": "https://example.com/default-orphan-flag-source",
            },
            "push": False,
        },
    )
    assert default_result["ok"] is True

    default_path = (
        data_root
        / "source"
        / mcp_server.shard_for_slug("default-orphan-flag-source")
        / "source@default-orphan-flag-source"
        / "index.md"
    )
    default_payload = mcp_server.SourceRecord.model_validate(
        mcp_server.yaml.safe_load(
            default_path.read_text(encoding="utf-8").split("---", 2)[1]
        )
    )
    assert default_payload.allow_orphan_source is False

    explicit_result = _call_tool(
        server,
        "upsert_source",
        {
            "slug": "explicit-orphan-flag-source",
            "frontmatter": {
                "title": "Explicit Orphan Flag Source",
                "source-category": "citations/mcp",
                "allow-orphan-source": True,
            },
            "push": False,
        },
    )
    assert explicit_result["ok"] is True

    explicit_path = (
        data_root
        / "source"
        / mcp_server.shard_for_slug("explicit-orphan-flag-source")
        / "source@explicit-orphan-flag-source"
        / "index.md"
    )
    explicit_payload = mcp_server.SourceRecord.model_validate(
        mcp_server.yaml.safe_load(
            explicit_path.read_text(encoding="utf-8").split("---", 2)[1]
        )
    )
    assert explicit_payload.allow_orphan_source is True


def test_relation_tools_upsert_update_and_sync_symlinks(tmp_path: Path) -> None:
    project_root, data_root = _init_repo(tmp_path)
    server = mcp_server.create_mcp_server(project_root=project_root, data_root=data_root)

    for slug in ("alice", "bob"):
        person_result = _call_tool(
            server,
            "upsert_person",
            {
                "slug": slug,
                "frontmatter": {"person": slug.title()},
                "push": False,
            },
        )
        assert person_result["ok"] is True

    org_result = _call_tool(
        server,
        "upsert_org",
        {
            "slug": "acme",
            "frontmatter": {"org": "Acme"},
            "push": False,
        },
    )
    assert org_result["ok"] is True

    source_slug = "relation-source"
    source_result = _call_tool(
        server,
        "upsert_source",
        {
            "slug": source_slug,
            "frontmatter": {
                "title": "Relation Source",
                "source-category": "citations/tests",
                "url": "https://example.com/relation-source",
            },
            "body": "Source used by relation tools tests.",
            "push": False,
        },
    )
    assert source_result["ok"] is True

    source_ref = f"source/{mcp_server.shard_for_slug(source_slug)}/source@{source_slug}"

    works_at_edge_id = "works-at-alice-acme-current"
    works_result = _call_tool(
        server,
        "upsert_works_at_relation",
        {
            "edge_id": works_at_edge_id,
            "person_ref": "person/al/person@alice",
            "org_ref": "org/ac/org@acme",
            "first_noted_at": "2026-01-01",
            "last_verified_at": "2026-01-10",
            "sources": [source_ref],
            "notes": "Initial role",
            "push": False,
        },
    )
    assert works_result["ok"] is True

    works_path = project_root / works_result["apply"]["edge_path"]
    works_payload = json.loads(works_path.read_text(encoding="utf-8"))
    assert works_payload["relation"] == "works_at"
    assert works_payload["from"] == "person/al/person@alice"
    assert works_payload["to"] == "org/ac/org@acme"

    for link in (
        data_root / "person" / "al" / "person@alice" / "edges" / f"edge@{works_at_edge_id}.json",
        data_root / "org" / "ac" / "org@acme" / "edges" / f"edge@{works_at_edge_id}.json",
    ):
        assert link.is_symlink()
        target = os.readlink(link)
        assert not os.path.isabs(target)
        assert (link.parent / target).resolve() == works_path.resolve()

    update_works = _call_tool(
        server,
        "update_works_at_relation",
        {
            "edge_id": works_at_edge_id,
            "patch": {"valid_to": "2025-12-31", "notes": "Ended role"},
            "push": False,
        },
    )
    assert update_works["ok"] is True
    works_payload = json.loads(works_path.read_text(encoding="utf-8"))
    assert works_payload["valid_to"] == "2025-12-31"
    assert works_payload["notes"] == "Ended role"

    clear_works = _call_tool(
        server,
        "update_works_at_relation",
        {
            "edge_id": works_at_edge_id,
            "patch": {"valid_to": None},
            "push": False,
        },
    )
    assert clear_works["ok"] is True
    works_payload = json.loads(works_path.read_text(encoding="utf-8"))
    assert works_payload["valid_to"] is None

    knows_result = _call_tool(
        server,
        "upsert_knows_relation",
        {
            "person_a_ref": "person/bo/person@bob",
            "person_b_ref": "person/al/person@alice",
            "strength": -3,
            "first_noted_at": "2026-01-05",
            "last_verified_at": "2026-01-10",
            "sources": [source_ref],
            "push": False,
        },
    )
    assert knows_result["ok"] is True
    knows_edge_id = knows_result["apply"]["edge_id"]
    knows_path = project_root / knows_result["apply"]["edge_path"]
    knows_payload = json.loads(knows_path.read_text(encoding="utf-8"))
    assert knows_payload["relation"] == "knows"
    assert knows_payload["directed"] is False
    assert knows_payload["from"] == "person/al/person@alice"
    assert knows_payload["to"] == "person/bo/person@bob"
    assert knows_payload["strength"] == -3
    assert knows_edge_id.startswith("knows-alice-bob")

    update_knows = _call_tool(
        server,
        "update_knows_relation",
        {
            "edge_id": knows_edge_id,
            "patch": {"strength": -8, "valid_to": "2024-01-01"},
            "push": False,
        },
    )
    assert update_knows["ok"] is True
    knows_payload = json.loads(knows_path.read_text(encoding="utf-8"))
    assert knows_payload["strength"] == -8
    assert knows_payload["valid_to"] == "2024-01-01"

    cites_result = _call_tool(
        server,
        "upsert_cites_relation",
        {
            "source_entity_ref": "org/ac/org@acme",
            "target_source_ref": source_ref,
            "first_noted_at": "2026-01-06",
            "last_verified_at": "2026-01-10",
            "sources": [source_ref],
            "push": False,
        },
    )
    assert cites_result["ok"] is True
    cites_edge_id = cites_result["apply"]["edge_id"]
    cites_path = project_root / cites_result["apply"]["edge_path"]
    cites_payload = json.loads(cites_path.read_text(encoding="utf-8"))
    assert cites_payload["relation"] == "cites"
    assert cites_payload["directed"] is True

    source_link = (
        data_root
        / "source"
        / mcp_server.shard_for_slug(source_slug)
        / f"source@{source_slug}"
        / "edges"
        / f"edge@{cites_edge_id}.json"
    )
    assert source_link.is_symlink()
    assert (source_link.parent / os.readlink(source_link)).resolve() == cites_path.resolve()

    bad_patch = _call_tool(
        server,
        "update_works_at_relation",
        {
            "edge_id": works_at_edge_id,
            "patch": {"strength": 1},
            "push": False,
        },
    )
    assert bad_patch["ok"] is False
    assert bad_patch["error"]["code"] == "invalid_input"


def test_apply_sourced_changes_creates_source_and_appends_entity_paragraph(tmp_path: Path) -> None:
    project_root, data_root = _init_repo(tmp_path)
    server = mcp_server.create_mcp_server(project_root=project_root, data_root=data_root)

    person_result = _call_tool(
        server,
        "upsert_person",
        {
            "slug": "alice",
            "frontmatter": {"person": "Alice"},
            "body": "# Alice\n\n## Snapshot\n\n- Baseline.\n\n## Bio\n\nInitial bio.\n\n## Conversation Notes\n\n- Initial note.\n",
            "push": False,
        },
    )
    assert person_result["ok"] is True

    sourced_result = _call_tool(
        server,
        "apply_sourced_changes",
        {
            "operations": [
                {
                    "op": "create_source",
                    "slug": "inline-cited-source",
                    "frontmatter": {
                        "title": "Inline Cited Source",
                        "source-category": "citations/tests",
                        "url": "https://example.com/inline-cited-source",
                    },
                    "body": "Source body",
                },
                {
                    "op": "append_entity_section_paragraph",
                    "entity_ref": "person/al/person@alice",
                    "section": "Bio",
                    "paragraph": "New sourced paragraph for Alice.[^inline-cited-source]",
                    "changelog_note": "Added sourced bio detail.",
                },
            ],
            "push": False,
        },
    )
    assert sourced_result["ok"] is True
    assert sourced_result["apply"]["operation_count"] == 2
    assert sourced_result["apply"]["created_source_refs"] == [
        "source/in/source@inline-cited-source"
    ]
    assert sourced_result["apply"]["consumed_source_refs"] == [
        "source/in/source@inline-cited-source"
    ]

    person_index_path = data_root / "person" / "al" / "person@alice" / "index.md"
    person_index_text = person_index_path.read_text(encoding="utf-8")
    assert "New sourced paragraph for Alice.[^inline-cited-source]" in person_index_text

    changelog_path = data_root / "person" / "al" / "person@alice" / "changelog.jsonl"
    changelog_lines = [line for line in changelog_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert changelog_lines
    changelog_last = json.loads(changelog_lines[-1])
    assert changelog_last["note"].endswith("[^inline-cited-source]")

    source_index_path = data_root / "source" / "in" / "source@inline-cited-source" / "index.md"
    source_payload = mcp_server.SourceRecord.model_validate(
        mcp_server.yaml.safe_load(source_index_path.read_text(encoding="utf-8").split("---", 2)[1])
    )
    assert source_payload.allow_orphan_source is False


def test_apply_sourced_changes_rejects_orphan_source_by_default(tmp_path: Path) -> None:
    project_root, data_root = _init_repo(tmp_path)
    server = mcp_server.create_mcp_server(project_root=project_root, data_root=data_root)

    result = _call_tool(
        server,
        "apply_sourced_changes",
        {
            "operations": [
                {
                    "op": "create_source",
                    "slug": "orphan-source-disallowed",
                    "frontmatter": {
                        "title": "Orphan Source Disallowed",
                        "source-category": "citations/tests",
                    },
                }
            ],
            "push": False,
        },
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_input"
    assert "newly created sources must be consumed" in result["error"]["message"]


def test_apply_sourced_changes_allows_orphan_source_when_flagged(tmp_path: Path) -> None:
    project_root, data_root = _init_repo(tmp_path)
    server = mcp_server.create_mcp_server(project_root=project_root, data_root=data_root)

    result = _call_tool(
        server,
        "apply_sourced_changes",
        {
            "operations": [
                {
                    "op": "create_source",
                    "slug": "orphan-source-allowed",
                    "frontmatter": {
                        "title": "Orphan Source Allowed",
                        "source-category": "citations/tests",
                        "allow-orphan-source": True,
                    },
                }
            ],
            "push": False,
        },
    )
    assert result["ok"] is True
    assert result["apply"]["created_source_refs"] == ["source/or/source@orphan-source-allowed"]
    assert result["apply"]["consumed_source_refs"] == []


def test_append_entity_section_paragraph_suggests_sections_and_can_create_new(tmp_path: Path) -> None:
    project_root, data_root = _init_repo(tmp_path)
    server = mcp_server.create_mcp_server(project_root=project_root, data_root=data_root)

    org_result = _call_tool(
        server,
        "upsert_org",
        {
            "slug": "acme",
            "frontmatter": {"org": "Acme"},
            "body": "# Acme\n\n## Snapshot\n\n- Baseline.\n\n## Bio\n\nInitial bio.\n\n## Notes\n\nInitial notes.\n",
            "push": False,
        },
    )
    assert org_result["ok"] is True

    source_result = _call_tool(
        server,
        "upsert_source",
        {
            "slug": "org-section-source",
            "frontmatter": {
                "title": "Org Section Source",
                "source-category": "citations/tests",
                "url": "https://example.com/org-section-source",
            },
            "push": False,
        },
    )
    assert source_result["ok"] is True

    missing_section = _call_tool(
        server,
        "append_entity_section_paragraph",
        {
            "entity_ref": "org/ac/org@acme",
            "section": "Random Stuff",
            "paragraph": "Added section content.",
            "source_refs": ["org-section-source"],
            "changelog_note": "Added random section paragraph.",
            "push": False,
        },
    )
    assert missing_section["ok"] is False
    assert missing_section["error"]["code"] == "invalid_input"
    assert "Existing sections" in missing_section["error"]["message"]
    assert "Recommended sections" in missing_section["error"]["message"]

    create_section = _call_tool(
        server,
        "append_entity_section_paragraph",
        {
            "entity_ref": "org/ac/org@acme",
            "section": "Random Stuff",
            "paragraph": "Added section content.",
            "source_refs": ["org-section-source"],
            "changelog_note": "Added random section paragraph.",
            "create_section_if_missing": True,
            "push": False,
        },
    )
    assert create_section["ok"] is True
    assert create_section["apply"]["created_section"] is True

    org_index_path = data_root / "org" / "ac" / "org@acme" / "index.md"
    org_index_text = org_index_path.read_text(encoding="utf-8")
    assert "## Random Stuff" in org_index_text
    assert "Added section content. [^org-section-source]" in org_index_text

    changelog_path = data_root / "org" / "ac" / "org@acme" / "changelog.jsonl"
    changelog_lines = [line for line in changelog_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert changelog_lines
    latest = json.loads(changelog_lines[-1])
    assert latest["note"].endswith("[^org-section-source]")


def test_read_only_query_tools_list_search_and_read(tmp_path: Path) -> None:
    project_root, data_root = _init_repo(tmp_path)
    source_dir = data_root / "source" / "te" / "source@test-query-source"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_dir / "index.md"
    source_path.write_text(
        (
            "---\n"
            "id: source@test-query-source\n"
            "title: Test Query Source\n"
            "source-type: document\n"
            "citation-key: test-query-source\n"
            "source-path: data/source/te/source@test-query-source/index.md\n"
            "source-category: citations/test\n"
            "---\n\n"
            "Unique Query Token 42 appears in this source body.\n"
        ),
        encoding="utf-8",
    )
    (source_dir / "edges").mkdir(parents=True, exist_ok=True)
    (source_dir / "edges" / ".gitkeep").write_text("", encoding="utf-8")

    server = mcp_server.create_mcp_server(project_root=project_root, data_root=data_root)

    listed = _call_tool(
        server,
        "list_data_files",
        {"prefix": "source", "suffix": "index.md", "limit": 100},
    )
    assert listed["ok"] is True
    assert any(path.endswith("data/source/te/source@test-query-source/index.md") for path in listed["paths"])

    search = _call_tool(
        server,
        "search_data",
        {
            "query": "Unique Query Token 42",
            "fixed_strings": True,
            "file_type": "md",
            "max_results": 20,
        },
    )
    assert search["ok"] is True
    assert search["matches"]
    assert any(
        match["path"].endswith("data/source/te/source@test-query-source/index.md")
        for match in search["matches"]
    )

    read = _call_tool(
        server,
        "read_data_file",
        {"path": "source/te/source@test-query-source/index.md"},
    )
    assert read["ok"] is True
    assert read["path"].endswith("data/source/te/source@test-query-source/index.md")
    assert "Unique Query Token 42" in read["content"]


def test_read_only_query_tools_have_non_destructive_annotations(tmp_path: Path) -> None:
    project_root, data_root = _init_repo(tmp_path)
    server = mcp_server.create_mcp_server(project_root=project_root, data_root=data_root)

    tools = asyncio.run(server.list_tools())
    tools_by_name = {tool.name: tool for tool in tools}

    for name in ("list_data_files", "read_data_file", "search_data", "semantic_search_data"):
        annotations = tools_by_name[name].annotations
        assert annotations is not None
        assert annotations.readOnlyHint is True
        assert annotations.destructiveHint is False
        assert annotations.idempotentHint is True


def test_semantic_search_data_tool_uses_test_repo_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, data_root = _init_repo(tmp_path)

    alice_path = data_root / "person" / "al" / "person@alice" / "index.md"
    alice_path.parent.mkdir(parents=True, exist_ok=True)
    alice_path.write_text(
        (
            "---\n"
            "person: Alice\n"
            "---\n\n"
            "Alice is a founder building payments infrastructure.\n"
        ),
        encoding="utf-8",
    )
    bob_path = data_root / "person" / "bo" / "person@bob" / "index.md"
    bob_path.parent.mkdir(parents=True, exist_ok=True)
    bob_path.write_text(
        (
            "---\n"
            "person: Bob\n"
            "---\n\n"
            "Bob focuses on gaming graphics systems.\n"
        ),
        encoding="utf-8",
    )

    class FakeBackend(semantic.EmbeddingBackend):
        backend_id = "fake"
        model_name = "fake-model"

        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            vectors: list[list[float]] = []
            for text in texts:
                lowered = text.lower()
                vectors.append(
                    [
                        float(lowered.count("founder")),
                        float(lowered.count("payments")),
                        float(lowered.count("infrastructure")),
                        float(lowered.count("gaming")),
                    ]
                )
            return vectors

    fake_backend = FakeBackend()
    index_path = project_root / ".build" / "semantic" / "index.json"
    semantic.build_semantic_index(
        project_root=project_root,
        data_root=data_root,
        index_path=index_path,
        embedding_backend=fake_backend,
        max_chars=256,
        min_chars=1,
        overlap_chars=0,
    )

    monkeypatch.setattr(
        mcp_server,
        "FastEmbedBackend",
        lambda model_name, cache_dir=None: fake_backend,
    )

    server = mcp_server.create_mcp_server(project_root=project_root, data_root=data_root)
    result = _call_tool(
        server,
        "semantic_search_data",
        {
            "query": "founder payments infra",
            "limit": 3,
            "index_path": ".build/semantic/index.json",
        },
    )

    assert result["ok"] is True
    assert result["results"]
    assert result["results"][0]["data_path"].endswith("person/al/person@alice/index.md")
    assert result["index_path"] == ".build/semantic/index.json"


def test_semantic_search_data_tool_returns_not_found_for_missing_index(tmp_path: Path) -> None:
    project_root, data_root = _init_repo(tmp_path)
    server = mcp_server.create_mcp_server(project_root=project_root, data_root=data_root)

    result = _call_tool(
        server,
        "semantic_search_data",
        {
            "query": "founder payments infra",
            "index_path": ".build/semantic/missing-index.json",
        },
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "not_found"


def test_read_data_file_rejects_path_outside_data_root(tmp_path: Path) -> None:
    project_root, data_root = _init_repo(tmp_path)
    server = mcp_server.create_mcp_server(project_root=project_root, data_root=data_root)
    result = _call_tool(
        server,
        "read_data_file",
        {"path": "../.git/config"},
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_input"


def test_streamable_http_oauth_discovery_alias_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root, data_root = _init_repo(tmp_path)
    monkeypatch.setenv(mcp_server.OAUTH_MODE_ENV_VAR, "in-memory")

    auth_provider = mcp_server.create_http_oauth_provider(
        project_root=project_root,
        transport="streamable-http",
        host="127.0.0.1",
        port=8001,
    )
    assert auth_provider is not None

    server = mcp_server.create_mcp_server(
        project_root=project_root,
        data_root=data_root,
        auth_provider=auth_provider,
        oauth_discovery_mcp_path="/mcp",
    )
    app = server.http_app(path="/mcp", transport="streamable-http")

    with TestClient(app) as client:
        metadata = client.get("/.well-known/oauth-authorization-server")
        assert metadata.status_code == 200
        metadata_payload = metadata.json()
        assert metadata_payload["issuer"].rstrip("/") == "http://127.0.0.1:8001"
        assert metadata_payload["token_endpoint"] == "http://127.0.0.1:8001/token"
        assert metadata_payload["registration_endpoint"] == "http://127.0.0.1:8001/register"

        register = client.post("/register", json={})
        assert register.status_code == 400

        suffix_alias = client.get(
            "/.well-known/oauth-authorization-server/mcp",
            follow_redirects=False,
        )
        assert suffix_alias.status_code == 307
        assert suffix_alias.headers["location"] == "/.well-known/oauth-authorization-server"

        prefix_alias = client.get(
            "/mcp/.well-known/oauth-authorization-server",
            follow_redirects=False,
        )
        assert prefix_alias.status_code == 307
        assert prefix_alias.headers["location"] == "/.well-known/oauth-authorization-server"


def test_streamable_http_external_jwt_metadata_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root, data_root = _init_repo(tmp_path)
    monkeypatch.setenv(mcp_server.OAUTH_MODE_ENV_VAR, "external-jwt")
    monkeypatch.setenv(mcp_server.EXTERNAL_JWT_JWKS_URI_ENV_VAR, "https://idp.example/oauth/jwks")
    monkeypatch.setenv(
        mcp_server.EXTERNAL_AUTHORIZATION_SERVERS_ENV_VAR,
        "https://idp.example",
    )
    monkeypatch.setenv(mcp_server.EXTERNAL_REQUIRED_SCOPES_ENV_VAR, "mcp.read,mcp.write")

    auth_provider = mcp_server.create_http_oauth_provider(
        project_root=project_root,
        transport="streamable-http",
        host="127.0.0.1",
        port=8001,
    )
    assert auth_provider is not None
    assert isinstance(auth_provider, mcp_server.RemoteAuthProvider)

    server = mcp_server.create_mcp_server(
        project_root=project_root,
        data_root=data_root,
        auth_provider=auth_provider,
        oauth_discovery_mcp_path="/mcp",
    )
    app = server.http_app(path="/mcp", transport="streamable-http")

    with TestClient(app) as client:
        protected = client.get("/.well-known/oauth-protected-resource/mcp")
        assert protected.status_code == 200
        payload = protected.json()
        assert payload["resource"] == "http://127.0.0.1:8001/mcp"
        assert payload["authorization_servers"] == ["https://idp.example/"]
        assert payload["scopes_supported"] == ["mcp.read", "mcp.write"]

        authorization_server = client.get("/.well-known/oauth-authorization-server")
        assert authorization_server.status_code == 404

        suffix_alias = client.get(
            "/.well-known/oauth-authorization-server/mcp",
            follow_redirects=False,
        )
        assert suffix_alias.status_code == 404


def test_external_jwt_mode_requires_verification_key_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, _ = _init_repo(tmp_path)
    monkeypatch.setenv(mcp_server.OAUTH_MODE_ENV_VAR, "external-jwt")
    monkeypatch.setenv(
        mcp_server.EXTERNAL_AUTHORIZATION_SERVERS_ENV_VAR,
        "https://idp.example",
    )
    monkeypatch.delenv(mcp_server.EXTERNAL_JWT_JWKS_URI_ENV_VAR, raising=False)
    monkeypatch.delenv(mcp_server.EXTERNAL_JWT_PUBLIC_KEY_ENV_VAR, raising=False)

    with pytest.raises(ValueError, match="requires either"):
        mcp_server.create_http_oauth_provider(
            project_root=project_root,
            transport="streamable-http",
            host="127.0.0.1",
            port=8001,
        )


def test_persistent_oauth_refresh_token_can_be_reused(tmp_path: Path) -> None:
    state_path = tmp_path / "oauth-state.json"
    provider = mcp_server.PersistentInMemoryOAuthProvider(
        base_url="http://127.0.0.1:8001",
        state_path=state_path,
    )
    client = mcp_server.OAuthClientInformationFull(
        client_id="test-client",
        redirect_uris=["http://localhost/callback"],
        scope="mcp",
    )
    asyncio.run(provider.register_client(client))

    refresh_token = mcp_server.RefreshToken(
        token="test-refresh-token",
        client_id="test-client",
        scopes=["mcp"],
        expires_at=int(time.time()) + 3600,
    )
    provider.refresh_tokens[refresh_token.token] = refresh_token
    provider._refresh_to_access_map[refresh_token.token] = "seed-access-token"
    provider.access_tokens["seed-access-token"] = mcp_server.AccessToken(
        token="seed-access-token",
        client_id="test-client",
        scopes=["mcp"],
        expires_at=int(time.time()) + 3600,
    )
    provider._access_to_refresh_map["seed-access-token"] = refresh_token.token

    first = asyncio.run(provider.exchange_refresh_token(client, refresh_token, ["mcp"]))
    second = asyncio.run(provider.exchange_refresh_token(client, refresh_token, ["mcp"]))

    assert first.refresh_token == refresh_token.token
    assert second.refresh_token == refresh_token.token
    assert first.access_token != second.access_token
    assert asyncio.run(provider.load_access_token(first.access_token)) is not None
    assert asyncio.run(provider.load_access_token(second.access_token)) is not None
    assert asyncio.run(provider.load_refresh_token(client, refresh_token.token)) is not None

    reloaded = mcp_server.PersistentInMemoryOAuthProvider(
        base_url="http://127.0.0.1:8001",
        state_path=state_path,
    )
    persisted_client = asyncio.run(reloaded.get_client("test-client"))
    assert persisted_client is not None
    assert asyncio.run(reloaded.load_access_token(first.access_token)) is not None
    assert asyncio.run(reloaded.load_access_token(second.access_token)) is not None
    assert asyncio.run(reloaded.load_refresh_token(persisted_client, refresh_token.token)) is not None


def test_persistent_oauth_verify_token_reloads_state_on_miss(tmp_path: Path) -> None:
    state_path = tmp_path / "oauth-state.json"
    writer = mcp_server.PersistentInMemoryOAuthProvider(
        base_url="http://127.0.0.1:8001",
        state_path=state_path,
    )
    reader = mcp_server.PersistentInMemoryOAuthProvider(
        base_url="http://127.0.0.1:8001",
        state_path=state_path,
    )
    client = mcp_server.OAuthClientInformationFull(
        client_id="reload-client",
        redirect_uris=["http://localhost/callback"],
        scope="mcp",
    )
    asyncio.run(writer.register_client(client))

    token_value = "test-access-token-reload"
    writer.access_tokens[token_value] = mcp_server.AccessToken(
        token=token_value,
        client_id="reload-client",
        scopes=["mcp"],
        expires_at=int(time.time()) + 3600,
    )
    writer._save_state()

    verified = asyncio.run(reader.verify_token(token_value))
    assert verified is not None
    assert verified.token == token_value
