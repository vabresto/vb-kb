from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Literal

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, ValidationError
import yaml

from kb.edges import sync_edge_backlinks
from kb.schemas import EdgeRecord, NoteRecord, shard_for_slug
from kb.validate import infer_data_root, run_validation

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LOCK_FILENAME = ".kb-write.lock"
AUTH_TOKEN_ENV_VAR = "KB_MCP_AUTH_TOKEN"


class BusyLockError(RuntimeError):
    pass


class EntityUpsertInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["person", "org"]
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    body: str = ""
    commit_message: str | None = None


class NoteUpsertInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    body: str = ""
    commit_message: str | None = None


def relpath(path: Path, project_root: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()


def run_git(project_root: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(project_root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "git command failed")
    return completed


def parse_porcelain_paths(porcelain_output: str) -> set[str]:
    changed: set[str] = set()
    for line in porcelain_output.splitlines():
        if len(line) < 4:
            continue
        path_part = line[3:].strip()
        if not path_part:
            continue

        if " -> " in path_part:
            left, right = path_part.split(" -> ", 1)
            left = left.strip().strip('"')
            right = right.strip().strip('"')
            if left:
                changed.add(left)
            if right:
                changed.add(right)
            continue

        changed.add(path_part.strip('"'))
    return changed


def list_data_changes(project_root: Path, data_root: Path) -> set[str]:
    data_root_rel = relpath(data_root, project_root)
    result = run_git(
        project_root,
        ["status", "--porcelain", "--untracked-files=all", "--", data_root_rel],
    )
    return parse_porcelain_paths(result.stdout)


@contextmanager
def repo_write_lock(project_root: Path):
    lock_path = project_root / LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise BusyLockError("write lock is already held") from exc

    try:
        yield lock_path
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def title_from_slug(slug: str) -> str:
    parts = [part for part in slug.split("-") if part]
    if not parts:
        return "Untitled"
    return " ".join(part.capitalize() for part in parts)


def render_markdown(frontmatter: dict[str, Any], body: str) -> str:
    chunks = [render_frontmatter(frontmatter).rstrip()]
    if body.strip():
        chunks.extend(["", body.strip()])
    return "\n".join(chunks).rstrip() + "\n"


def render_frontmatter(metadata: dict[str, Any]) -> str:
    dumped = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=False).rstrip()
    return f"---\n{dumped}\n---\n"


def default_commit_message(operation: str, path: str) -> str:
    return f"mcp(kbv2): {operation} {path}"


def rollback_changed_paths(project_root: Path, changed_paths: list[str]) -> None:
    scoped = sorted({path for path in changed_paths if path})
    if not scoped:
        return

    run_git(
        project_root,
        ["restore", "--staged", "--worktree", "--", *scoped],
        check=False,
    )
    run_git(
        project_root,
        ["clean", "-fd", "--", *scoped],
        check=False,
    )


def verify_auth_token(auth_token: str | None) -> None:
    expected = (os.getenv(AUTH_TOKEN_ENV_VAR) or "").strip()
    if not expected:
        return

    provided = (auth_token or "").strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise PermissionError("invalid or missing auth token")


def unauthorized_error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": "unauthorized", "retryable": False, "message": message}}


def run_transaction(
    *,
    project_root: Path,
    data_root: Path,
    commit_message: str,
    apply_changes: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    with repo_write_lock(project_root):
        before = list_data_changes(project_root, data_root)
        try:
            apply_meta = apply_changes()
        except Exception:
            try:
                after_failed_apply = list_data_changes(project_root, data_root)
                rollback_changed_paths(project_root, sorted(after_failed_apply - before))
            except Exception:
                pass
            raise

        after = list_data_changes(project_root, data_root)

        delta = sorted(after - before)
        if not delta:
            return {
                "ok": True,
                "committed": False,
                "changed_paths": [],
                "apply": apply_meta,
                "validation": None,
            }

        scope_paths = {(project_root / rel).absolute() for rel in delta}
        try:
            validation_result = run_validation(
                project_root=project_root,
                data_root=data_root,
                scope_paths=scope_paths,
                scope_label="mcp-transaction",
            )
        except Exception:
            rollback_changed_paths(project_root, delta)
            raise

        if not validation_result["ok"]:
            rollback_changed_paths(project_root, delta)
            return {
                "ok": False,
                "error": {
                    "code": "validation_failed",
                    "retryable": False,
                    "message": "validation failed for transaction scope",
                },
                "changed_paths": delta,
                "apply": apply_meta,
                "validation": validation_result,
            }

        try:
            run_git(project_root, ["add", "-A", "--", *delta])
            commit = run_git(
                project_root,
                ["commit", "-m", commit_message, "--", *delta],
                check=False,
            )
        except Exception:
            rollback_changed_paths(project_root, delta)
            raise

        if commit.returncode != 0:
            rollback_changed_paths(project_root, delta)
            return {
                "ok": False,
                "error": {
                    "code": "commit_failed",
                    "retryable": False,
                    "message": commit.stderr.strip() or commit.stdout.strip() or "git commit failed",
                },
                "changed_paths": delta,
                "apply": apply_meta,
                "validation": validation_result,
            }

        commit_sha = run_git(project_root, ["rev-parse", "HEAD"]).stdout.strip()
        return {
            "ok": True,
            "committed": True,
            "commit": commit_sha,
            "changed_paths": delta,
            "apply": apply_meta,
            "validation": validation_result,
        }


def ensure_slug(slug: str) -> str:
    cleaned = slug.strip().lower()
    if not SLUG_RE.match(cleaned):
        raise ValueError("slug must be lowercase kebab-case")
    return cleaned


def upsert_entity_file(
    *,
    project_root: Path,
    data_root: Path,
    payload: EntityUpsertInput,
) -> tuple[Path, dict[str, Any]]:
    slug = ensure_slug(payload.slug)
    entity_dir = data_root / payload.kind / shard_for_slug(slug) / f"{payload.kind}@{slug}"
    entity_dir.mkdir(parents=True, exist_ok=True)

    metadata = dict(payload.frontmatter)
    title_key = "person" if payload.kind == "person" else "org"
    metadata[title_key] = str(metadata.get(title_key) or title_from_slug(slug)).strip()
    index_path = entity_dir / "index.md"
    index_path.write_text(render_markdown(metadata, payload.body), encoding="utf-8")

    edges_dir = entity_dir / "edges"
    edges_dir.mkdir(parents=True, exist_ok=True)
    gitkeep = edges_dir / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text("", encoding="utf-8")

    changelog = entity_dir / "changelog.jsonl"
    if not changelog.exists():
        changelog.write_text("", encoding="utf-8")

    if payload.kind == "person":
        employment = entity_dir / "employment-history.jsonl"
        if not employment.exists():
            employment.write_text("", encoding="utf-8")
        looking_for = entity_dir / "looking-for.jsonl"
        if not looking_for.exists():
            looking_for.write_text("", encoding="utf-8")

    return index_path, {"kind": payload.kind, "slug": slug, "index_path": relpath(index_path, project_root)}


def upsert_note_file(
    *,
    project_root: Path,
    data_root: Path,
    payload: NoteUpsertInput,
) -> tuple[Path, dict[str, Any]]:
    slug = ensure_slug(payload.slug)
    note_dir = data_root / "note" / shard_for_slug(slug) / f"note@{slug}"
    note_dir.mkdir(parents=True, exist_ok=True)

    metadata = dict(payload.frontmatter)
    metadata["id"] = f"note@{slug}"
    metadata["title"] = str(metadata.get("title") or title_from_slug(slug)).strip()
    metadata["note-type"] = str(metadata.get("note-type") or "note").strip()
    metadata["source-path"] = str(
        metadata.get("source-path") or f"data/note/{shard_for_slug(slug)}/note@{slug}/index.md"
    ).strip()
    metadata["source-category"] = str(metadata.get("source-category") or "mcp").strip()

    # Validate canonical frontmatter before writing.
    NoteRecord.model_validate(metadata)

    index_path = note_dir / "index.md"
    index_path.write_text(render_markdown(metadata, payload.body), encoding="utf-8")
    return index_path, {"slug": slug, "index_path": relpath(index_path, project_root)}


def upsert_edge_file(
    *,
    project_root: Path,
    data_root: Path,
    edge_payload: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    record = EdgeRecord.model_validate(edge_payload)
    edge_path = data_root / "edge" / shard_for_slug(record.id) / f"edge@{record.id}.json"
    edge_path.parent.mkdir(parents=True, exist_ok=True)
    edge_path.write_text(
        json.dumps(record.model_dump(by_alias=True), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    sync_result = sync_edge_backlinks(project_root=project_root, data_root=data_root)
    if not sync_result["ok"]:
        raise RuntimeError(json.dumps(sync_result, sort_keys=True))

    return edge_path, {"edge_id": record.id, "edge_path": relpath(edge_path, project_root), "sync": sync_result}


def create_mcp_server(*, project_root: Path, data_root: Path) -> FastMCP:
    server = FastMCP(
        name="VB KB Write Server",
        instructions=(
            "Mutating MCP server for KB v2 canonical files. "
            "All writes are lock-protected, validated, and committed to git."
        ),
    )

    @server.tool
    def upsert_entity(
        kind: Literal["person", "org"],
        slug: str,
        frontmatter: dict[str, Any] | None = None,
        body: str = "",
        commit_message: str | None = None,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        try:
            verify_auth_token(auth_token)
            payload = EntityUpsertInput(
                kind=kind,
                slug=slug,
                frontmatter=frontmatter or {},
                body=body,
                commit_message=commit_message,
            )
        except PermissionError as exc:
            return unauthorized_error(str(exc))
        except ValidationError as exc:
            return {"ok": False, "error": {"code": "invalid_input", "retryable": False, "message": str(exc)}}

        try:
            message = payload.commit_message or default_commit_message(
                "upsert-entity",
                relpath(
                    data_root / payload.kind / shard_for_slug(payload.slug) / f"{payload.kind}@{payload.slug}" / "index.md",
                    project_root,
                ),
            )
        except Exception as exc:
            return {"ok": False, "error": {"code": "invalid_input", "retryable": False, "message": str(exc)}}

        def apply() -> dict[str, Any]:
            _, apply_meta = upsert_entity_file(
                project_root=project_root,
                data_root=data_root,
                payload=payload,
            )
            return apply_meta

        try:
            result = run_transaction(
                project_root=project_root,
                data_root=data_root,
                commit_message=message,
                apply_changes=apply,
            )
        except BusyLockError:
            return {
                "ok": False,
                "error": {"code": "busy", "retryable": True, "message": "write lock is currently held"},
            }
        except Exception as exc:
            return {"ok": False, "error": {"code": "write_failed", "retryable": False, "message": str(exc)}}

        return result

    @server.tool
    def upsert_note(
        slug: str,
        frontmatter: dict[str, Any] | None = None,
        body: str = "",
        commit_message: str | None = None,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        try:
            verify_auth_token(auth_token)
            payload = NoteUpsertInput(
                slug=slug,
                frontmatter=frontmatter or {},
                body=body,
                commit_message=commit_message,
            )
        except PermissionError as exc:
            return unauthorized_error(str(exc))
        except ValidationError as exc:
            return {"ok": False, "error": {"code": "invalid_input", "retryable": False, "message": str(exc)}}

        message = payload.commit_message or default_commit_message(
            "upsert-note",
            relpath(
                data_root / "note" / shard_for_slug(payload.slug) / f"note@{payload.slug}" / "index.md",
                project_root,
            ),
        )

        def apply() -> dict[str, Any]:
            _, apply_meta = upsert_note_file(
                project_root=project_root,
                data_root=data_root,
                payload=payload,
            )
            return apply_meta

        try:
            result = run_transaction(
                project_root=project_root,
                data_root=data_root,
                commit_message=message,
                apply_changes=apply,
            )
        except BusyLockError:
            return {
                "ok": False,
                "error": {"code": "busy", "retryable": True, "message": "write lock is currently held"},
            }
        except Exception as exc:
            return {"ok": False, "error": {"code": "write_failed", "retryable": False, "message": str(exc)}}

        return result

    @server.tool
    def upsert_edge(
        edge: dict[str, Any],
        commit_message: str | None = None,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        try:
            verify_auth_token(auth_token)
        except PermissionError as exc:
            return unauthorized_error(str(exc))

        def apply() -> dict[str, Any]:
            edge_path, apply_meta = upsert_edge_file(
                project_root=project_root,
                data_root=data_root,
                edge_payload=edge,
            )
            apply_meta["edge_path"] = relpath(edge_path, project_root)
            return apply_meta

        message = commit_message or "mcp(kbv2): upsert-edge"
        try:
            result = run_transaction(
                project_root=project_root,
                data_root=data_root,
                commit_message=message,
                apply_changes=apply,
            )
        except BusyLockError:
            return {
                "ok": False,
                "error": {"code": "busy", "retryable": True, "message": "write lock is currently held"},
            }
        except (ValidationError, ValueError) as exc:
            return {"ok": False, "error": {"code": "invalid_input", "retryable": False, "message": str(exc)}}
        except Exception as exc:
            return {"ok": False, "error": {"code": "write_failed", "retryable": False, "message": str(exc)}}

        return result

    return server


def run_server(
    *,
    project_root: Path,
    data_root: Path,
    transport: Literal["stdio", "http", "sse", "streamable-http"] = "stdio",
    host: str = "127.0.0.1",
    port: int = 8001,
    path: str | None = None,
) -> None:
    server = create_mcp_server(project_root=project_root, data_root=data_root)
    kwargs: dict[str, Any] = {}
    if transport in {"http", "sse", "streamable-http"}:
        kwargs["host"] = host
        kwargs["port"] = port
        if path:
            kwargs["path"] = path
    server.run(transport=transport, **kwargs)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run KB v2 FastMCP write server.")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    parser.add_argument(
        "--data-root",
        default=None,
        help="Data root directory (default: data).",
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "http", "sse", "streamable-http"],
        help="FastMCP transport to run.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host when using HTTP transports.")
    parser.add_argument("--port", type=int, default=8001, help="HTTP port when using HTTP transports.")
    parser.add_argument("--path", default=None, help="Optional HTTP route path for streamable-http/sse.")
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    data_root = infer_data_root(project_root, args.data_root)
    run_server(
        project_root=project_root,
        data_root=data_root,
        transport=args.transport,
        host=args.host,
        port=args.port,
        path=args.path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
