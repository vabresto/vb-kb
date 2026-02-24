from __future__ import annotations

import fcntl
import fnmatch
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
from kb.schemas import EdgeRecord, SourceRecord, SourceType, shard_for_slug
from kb.validate import infer_data_root, run_validation

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LOCK_FILENAME = ".kb-write.lock"
AUTH_TOKEN_ENV_VAR = "KB_MCP_AUTH_TOKEN"
TEXT_FILE_SUFFIXES = {".md", ".json", ".jsonl", ".txt", ".yaml", ".yml", ".csv"}
SEARCH_FILE_TYPE_GLOBS: dict[str, str | None] = {
    "all": None,
    "md": "*.md",
    "jsonl": "*.jsonl",
    "json": "*.json",
}


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


class SourceUpsertInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    source_type: SourceType = SourceType.document
    note_type: str | None = None
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    body: str = ""
    commit_message: str | None = None


class ReadDataFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    max_bytes: int = Field(default=200_000, ge=1, le=5_000_000)


class ListDataFilesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefix: str | None = None
    suffix: str | None = None
    limit: int = Field(default=200, ge=1, le=10_000)


class SearchDataInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    file_type: Literal["all", "md", "jsonl", "json"] = "all"
    glob: str | None = None
    case_sensitive: bool = False
    fixed_strings: bool = False
    max_results: int = Field(default=100, ge=1, le=2000)


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


def normalize_data_relative_path(path: str, *, allow_empty: bool = False) -> str:
    text = path.strip().replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    if text.startswith("data/"):
        text = text[5:]
    text = text.strip("/")
    if not text:
        if allow_empty:
            return ""
        raise ValueError("path must be non-empty")

    parts = [part for part in text.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise ValueError("path must stay within data root")
    return "/".join(parts)


def resolve_data_path(data_root: Path, path: str) -> Path:
    rel = normalize_data_relative_path(path)
    data_root_resolved = data_root.resolve()
    candidate = (data_root / rel).resolve()
    try:
        candidate.relative_to(data_root_resolved)
    except ValueError as exc:
        raise ValueError("path must stay within data root") from exc
    return candidate


def list_scoped_data_files(
    *,
    project_root: Path,
    data_root: Path,
    prefix: str | None,
    suffix: str | None,
    limit: int,
) -> tuple[list[str], bool, int]:
    data_root_resolved = data_root.resolve()
    scope_root = data_root_resolved
    if prefix:
        scope_root = resolve_data_path(data_root, prefix)
        if not scope_root.exists():
            return [], False, 0

    paths: list[str] = []
    total = 0
    truncated = False

    if scope_root.is_file():
        candidates = [scope_root]
    else:
        candidates = sorted(
            (path for path in scope_root.rglob("*") if path.is_file()),
            key=lambda path: path.as_posix(),
        )

    for path in candidates:
        try:
            rel = path.relative_to(data_root_resolved).as_posix()
        except ValueError:
            continue
        if suffix and not rel.endswith(suffix):
            continue
        total += 1
        if len(paths) < limit:
            paths.append(relpath(path, project_root))
        else:
            truncated = True
    return paths, truncated, total


def list_repo_changes(project_root: Path) -> set[str]:
    result = run_git(
        project_root,
        ["status", "--porcelain", "--untracked-files=all"],
    )
    return parse_porcelain_paths(result.stdout)


def is_path_within_data_root(path: str, data_root_rel: str) -> bool:
    normalized = path.strip("/")
    base = data_root_rel.strip("/")
    return normalized == base or normalized.startswith(f"{base}/")


def parse_rg_json_matches(stdout: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    summary_stats: dict[str, Any] = {}
    for line in stdout.splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        event_type = str(event.get("type") or "")
        data = event.get("data") or {}
        if event_type == "match":
            path = str(((data.get("path") or {}).get("text") or "")).replace("\\", "/")
            line_number = int(data.get("line_number") or 0)
            line_text = str(((data.get("lines") or {}).get("text") or "")).rstrip("\n")
            submatches_payload = data.get("submatches") or []
            submatches: list[dict[str, Any]] = []
            for sub in submatches_payload:
                if not isinstance(sub, dict):
                    continue
                match_payload = sub.get("match") or {}
                submatches.append(
                    {
                        "start": sub.get("start"),
                        "end": sub.get("end"),
                        "text": str(match_payload.get("text") or ""),
                    }
                )
            matches.append(
                {
                    "path": path,
                    "line_number": line_number,
                    "line": line_text,
                    "submatches": submatches,
                }
            )
        elif event_type == "summary":
            stats = data.get("stats")
            if isinstance(stats, dict):
                summary_stats = stats
    return matches, summary_stats


def search_data_with_ripgrep(
    *,
    project_root: Path,
    data_root: Path,
    payload: SearchDataInput,
) -> dict[str, Any]:
    args = [
        "rg",
        "--json",
        "--line-number",
        "--color",
        "never",
    ]
    if not payload.case_sensitive:
        args.append("-i")
    if payload.fixed_strings:
        args.append("-F")
    file_glob = SEARCH_FILE_TYPE_GLOBS[payload.file_type]
    if file_glob:
        args.extend(["--glob", file_glob])
    if payload.glob:
        args.extend(["--glob", payload.glob])
    args.extend([payload.query, relpath(data_root, project_root)])

    completed = subprocess.run(
        args,
        cwd=project_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode not in {0, 1}:
        error_text = completed.stderr.strip() or completed.stdout.strip() or "ripgrep query failed"
        if "regex parse error" in error_text.lower():
            raise ValueError(error_text)
        raise RuntimeError(error_text)

    raw_matches, summary = parse_rg_json_matches(completed.stdout)
    truncated = len(raw_matches) > payload.max_results
    return {
        "engine": "ripgrep",
        "query": payload.query,
        "matches": raw_matches[: payload.max_results],
        "match_count": len(raw_matches),
        "truncated": truncated,
        "summary": summary,
    }


def search_data_with_python_fallback(
    *,
    project_root: Path,
    data_root: Path,
    payload: SearchDataInput,
) -> dict[str, Any]:
    file_glob = SEARCH_FILE_TYPE_GLOBS[payload.file_type]
    pattern: re.Pattern[str] | None = None
    needle = payload.query
    if payload.fixed_strings:
        if not payload.case_sensitive:
            needle = needle.lower()
    else:
        flags = 0 if payload.case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(payload.query, flags)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc

    matches: list[dict[str, Any]] = []
    truncated = False

    for path in sorted(data_root.rglob("*"), key=lambda candidate: candidate.as_posix()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_FILE_SUFFIXES:
            continue
        rel_from_data = path.relative_to(data_root).as_posix()
        rel_from_project = relpath(path, project_root)
        if file_glob and not fnmatch.fnmatch(rel_from_data, file_glob):
            continue
        if payload.glob and not fnmatch.fnmatch(rel_from_data, payload.glob):
            continue

        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line_number, line in enumerate(lines, start=1):
            submatches: list[dict[str, Any]] = []
            if payload.fixed_strings:
                haystack = line if payload.case_sensitive else line.lower()
                start = haystack.find(needle)
                while start >= 0:
                    end = start + len(needle)
                    submatches.append({"start": start, "end": end, "text": line[start:end]})
                    start = haystack.find(needle, end)
            else:
                assert pattern is not None
                for match in pattern.finditer(line):
                    submatches.append(
                        {"start": match.start(), "end": match.end(), "text": match.group(0)}
                    )

            if not submatches:
                continue

            matches.append(
                {
                    "path": rel_from_project,
                    "line_number": line_number,
                    "line": line,
                    "submatches": submatches,
                }
            )

            if len(matches) > payload.max_results:
                truncated = True
                break
        if truncated:
            break

    return {
        "engine": "python-fallback",
        "query": payload.query,
        "matches": matches[: payload.max_results],
        "match_count": len(matches) if not truncated else payload.max_results + 1,
        "truncated": truncated,
        "summary": {},
    }


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

    tracked: list[str] = []
    for path in scoped:
        probe = run_git(
            project_root,
            ["ls-files", "--error-unmatch", "--", path],
            check=False,
        )
        if probe.returncode == 0:
            tracked.append(path)

    if tracked:
        run_git(
            project_root,
            ["restore", "--staged", "--worktree", "--", *tracked],
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
    push: bool = True,
) -> dict[str, Any]:
    with repo_write_lock(project_root):
        data_root_rel = relpath(data_root, project_root)
        before_repo = list_repo_changes(project_root)
        before = list_data_changes(project_root, data_root)
        try:
            apply_meta = apply_changes()
        except Exception:
            try:
                after_failed_apply = list_repo_changes(project_root)
                rollback_changed_paths(project_root, sorted(after_failed_apply - before_repo))
            except Exception:
                pass
            raise

        after_repo = list_repo_changes(project_root)
        repo_delta = sorted(after_repo - before_repo)
        non_data_delta = sorted(
            path for path in repo_delta if not is_path_within_data_root(path, data_root_rel)
        )
        if non_data_delta:
            rollback_changed_paths(project_root, repo_delta)
            return {
                "ok": False,
                "error": {
                    "code": "non_data_changes",
                    "retryable": False,
                    "message": "transaction touched paths outside data root",
                },
                "changed_paths": repo_delta,
                "non_data_changed_paths": non_data_delta,
                "apply": apply_meta,
                "validation": None,
                "committed": False,
                "pushed": False,
            }

        after = list_data_changes(project_root, data_root)

        delta = sorted(after - before)
        if not delta:
            return {
                "ok": True,
                "committed": False,
                "pushed": False,
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
                "committed": False,
                "pushed": False,
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
                "committed": False,
                "pushed": False,
            }

        commit_sha = run_git(project_root, ["rev-parse", "HEAD"]).stdout.strip()
        if push:
            push_result = run_git(project_root, ["push"], check=False)
            if push_result.returncode != 0:
                return {
                    "ok": False,
                    "error": {
                        "code": "push_failed",
                        "retryable": False,
                        "message": push_result.stderr.strip()
                        or push_result.stdout.strip()
                        or "git push failed",
                    },
                    "committed": True,
                    "pushed": False,
                    "commit": commit_sha,
                    "changed_paths": delta,
                    "apply": apply_meta,
                    "validation": validation_result,
                }
        return {
            "ok": True,
            "committed": True,
            "pushed": push,
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


def upsert_source_file(
    *,
    project_root: Path,
    data_root: Path,
    payload: SourceUpsertInput,
) -> tuple[Path, dict[str, Any]]:
    slug = ensure_slug(payload.slug)
    source_dir = data_root / "source" / shard_for_slug(slug) / f"source@{slug}"
    source_dir.mkdir(parents=True, exist_ok=True)

    metadata = dict(payload.frontmatter)
    metadata["id"] = f"source@{slug}"
    metadata["title"] = str(metadata.get("title") or title_from_slug(slug)).strip()
    metadata["source-type"] = str(metadata.get("source-type") or payload.source_type.value).strip()
    if metadata["source-type"] == SourceType.note.value:
        metadata["note-type"] = str(metadata.get("note-type") or payload.note_type or "note").strip()
    elif payload.note_type:
        metadata["note-type"] = payload.note_type.strip()
    metadata["citation-key"] = str(metadata.get("citation-key") or slug).strip()
    metadata["source-path"] = str(
        metadata.get("source-path") or f"data/source/{shard_for_slug(slug)}/source@{slug}/index.md"
    ).strip()
    metadata["source-category"] = str(metadata.get("source-category") or "mcp").strip()

    # Validate canonical frontmatter before writing.
    SourceRecord.model_validate(metadata)

    index_path = source_dir / "index.md"
    index_path.write_text(render_markdown(metadata, payload.body), encoding="utf-8")
    edges_dir = source_dir / "edges"
    edges_dir.mkdir(parents=True, exist_ok=True)
    gitkeep = edges_dir / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text("", encoding="utf-8")
    return index_path, {"slug": slug, "index_path": relpath(index_path, project_root)}


def upsert_note_file(
    *,
    project_root: Path,
    data_root: Path,
    payload: NoteUpsertInput,
) -> tuple[Path, dict[str, Any]]:
    source_payload = SourceUpsertInput(
        slug=payload.slug,
        source_type=SourceType.note,
        note_type="note",
        frontmatter=payload.frontmatter,
        body=payload.body,
        commit_message=payload.commit_message,
    )
    return upsert_source_file(
        project_root=project_root,
        data_root=data_root,
        payload=source_payload,
    )


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
        push: bool = True,
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
                push=push,
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
    def upsert_source(
        slug: str,
        source_type: str = SourceType.document.value,
        note_type: str | None = None,
        frontmatter: dict[str, Any] | None = None,
        body: str = "",
        commit_message: str | None = None,
        push: bool = True,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        try:
            verify_auth_token(auth_token)
            payload = SourceUpsertInput(
                slug=slug,
                source_type=SourceType(str(source_type).strip()),
                note_type=note_type,
                frontmatter=frontmatter or {},
                body=body,
                commit_message=commit_message,
            )
        except PermissionError as exc:
            return unauthorized_error(str(exc))
        except (ValidationError, ValueError) as exc:
            return {"ok": False, "error": {"code": "invalid_input", "retryable": False, "message": str(exc)}}

        message = payload.commit_message or default_commit_message(
            "upsert-source",
            relpath(
                data_root / "source" / shard_for_slug(payload.slug) / f"source@{payload.slug}" / "index.md",
                project_root,
            ),
        )

        def apply() -> dict[str, Any]:
            _, apply_meta = upsert_source_file(
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
                push=push,
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
        push: bool = True,
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
                data_root / "source" / shard_for_slug(payload.slug) / f"source@{payload.slug}" / "index.md",
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
                push=push,
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
        push: bool = True,
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
                push=push,
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

    @server.tool
    def list_data_files(
        prefix: str | None = None,
        suffix: str | None = None,
        limit: int = 200,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        try:
            verify_auth_token(auth_token)
            payload = ListDataFilesInput(prefix=prefix, suffix=suffix, limit=limit)
        except PermissionError as exc:
            return unauthorized_error(str(exc))
        except ValidationError as exc:
            return {"ok": False, "error": {"code": "invalid_input", "retryable": False, "message": str(exc)}}

        try:
            paths, truncated, total = list_scoped_data_files(
                project_root=project_root,
                data_root=data_root,
                prefix=payload.prefix,
                suffix=payload.suffix,
                limit=payload.limit,
            )
        except ValueError as exc:
            return {"ok": False, "error": {"code": "invalid_input", "retryable": False, "message": str(exc)}}
        except Exception as exc:
            return {"ok": False, "error": {"code": "query_failed", "retryable": False, "message": str(exc)}}

        return {
            "ok": True,
            "paths": paths,
            "path_count": len(paths),
            "total_matches": total,
            "truncated": truncated,
            "prefix": payload.prefix,
            "suffix": payload.suffix,
        }

    @server.tool
    def read_data_file(
        path: str,
        max_bytes: int = 200_000,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        try:
            verify_auth_token(auth_token)
            payload = ReadDataFileInput(path=path, max_bytes=max_bytes)
        except PermissionError as exc:
            return unauthorized_error(str(exc))
        except ValidationError as exc:
            return {"ok": False, "error": {"code": "invalid_input", "retryable": False, "message": str(exc)}}

        try:
            target = resolve_data_path(data_root, payload.path)
        except ValueError as exc:
            return {"ok": False, "error": {"code": "invalid_input", "retryable": False, "message": str(exc)}}

        if not target.exists() or not target.is_file():
            return {
                "ok": False,
                "error": {
                    "code": "not_found",
                    "retryable": False,
                    "message": f"data file not found: {payload.path}",
                },
            }

        raw = target.read_bytes()
        truncated = len(raw) > payload.max_bytes
        content = raw[: payload.max_bytes].decode("utf-8", errors="replace")
        return {
            "ok": True,
            "path": relpath(target, project_root),
            "size_bytes": len(raw),
            "truncated": truncated,
            "content": content,
        }

    @server.tool
    def search_data(
        query: str,
        file_type: Literal["all", "md", "jsonl", "json"] = "all",
        glob: str | None = None,
        case_sensitive: bool = False,
        fixed_strings: bool = False,
        max_results: int = 100,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        try:
            verify_auth_token(auth_token)
            payload = SearchDataInput(
                query=query,
                file_type=file_type,
                glob=glob,
                case_sensitive=case_sensitive,
                fixed_strings=fixed_strings,
                max_results=max_results,
            )
        except PermissionError as exc:
            return unauthorized_error(str(exc))
        except ValidationError as exc:
            return {"ok": False, "error": {"code": "invalid_input", "retryable": False, "message": str(exc)}}

        try:
            result = search_data_with_ripgrep(
                project_root=project_root,
                data_root=data_root,
                payload=payload,
            )
        except FileNotFoundError:
            try:
                result = search_data_with_python_fallback(
                    project_root=project_root,
                    data_root=data_root,
                    payload=payload,
                )
            except ValueError as exc:
                return {
                    "ok": False,
                    "error": {"code": "invalid_input", "retryable": False, "message": str(exc)},
                }
            except Exception as exc:
                return {
                    "ok": False,
                    "error": {"code": "query_failed", "retryable": False, "message": str(exc)},
                }
        except ValueError as exc:
            return {"ok": False, "error": {"code": "invalid_input", "retryable": False, "message": str(exc)}}
        except Exception as exc:
            return {"ok": False, "error": {"code": "query_failed", "retryable": False, "message": str(exc)}}

        return {"ok": True, **result}

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
