from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

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
