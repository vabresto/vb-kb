from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from kb.schemas import ChangelogRow, EdgeRecord, EmploymentHistoryRow, LookingForRow, SourceRecord

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]]+)\](?!:)")


@dataclass(frozen=True)
class EntityRecord:
    kind: str
    entity_id: str
    rel_dir: str
    directory: Path
    index_path: Path


@dataclass(frozen=True)
class EdgeFile:
    path: Path
    rel_path: str


@dataclass(frozen=True)
class SourceFile:
    source_id: str
    rel_dir: str
    directory: Path
    index_path: Path


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str
    line: int | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }
        if self.line is not None:
            payload["line"] = self.line
        return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate KB v2 data layout and schemas")
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
        "--changed",
        action="store_true",
        help="Validate only files changed relative to HEAD plus impacted references.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional explicit files/directories to validate.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    return parser.parse_args()


def infer_data_root(project_root: Path, requested: str | None) -> Path:
    if requested:
        root = Path(requested)
        if not root.is_absolute():
            root = (project_root / root).resolve()
        return root
    return project_root / "data"


def gather_entities(data_root: Path) -> dict[str, EntityRecord]:
    entities: dict[str, EntityRecord] = {}

    for kind in ("person", "org", "source"):
        base = data_root / kind
        if not base.exists():
            continue
        for index_path in sorted(base.rglob("index.md")):
            entity_dir = index_path.parent
            if not entity_dir.name.startswith(f"{kind}@"):
                continue
            entity_id = entity_dir.name.split("@", 1)[1]
            rel_dir = entity_dir.relative_to(data_root).as_posix()
            entities[rel_dir] = EntityRecord(
                kind=kind,
                entity_id=entity_id,
                rel_dir=rel_dir,
                directory=entity_dir,
                index_path=index_path,
            )

    return entities


def gather_edge_files(data_root: Path) -> list[EdgeFile]:
    edge_root = data_root / "edge"
    if not edge_root.exists():
        return []

    files: list[EdgeFile] = []
    for path in sorted(edge_root.rglob("edge@*.json")):
        files.append(
            EdgeFile(
                path=path,
                rel_path=path.relative_to(data_root).as_posix(),
            )
        )
    return files


def gather_source_files(data_root: Path) -> dict[str, SourceFile]:
    sources: dict[str, SourceFile] = {}
    source_root = data_root / "source"
    if not source_root.exists():
        return sources

    for index_path in sorted(source_root.rglob("index.md")):
        source_dir = index_path.parent
        if not source_dir.name.startswith("source@"):
            continue
        rel_dir = source_dir.relative_to(data_root).as_posix()
        sources[rel_dir] = SourceFile(
            source_id=source_dir.name,
            rel_dir=rel_dir,
            directory=source_dir,
            index_path=index_path,
        )
    return sources


def collect_changed_paths(project_root: Path, data_root: Path) -> set[Path]:
    rel_data_root = data_root.relative_to(project_root).as_posix()

    changed: set[Path] = set()
    diff_cmd = [
        "git",
        "-C",
        str(project_root),
        "diff",
        "--name-only",
        "--relative",
        "HEAD",
        "--",
        rel_data_root,
    ]
    diff_result = subprocess.run(diff_cmd, capture_output=True, text=True, check=False)
    if diff_result.returncode in (0, 1):
        for line in diff_result.stdout.splitlines():
            value = line.strip()
            if not value:
                continue
            changed.add((project_root / value).absolute())

    untracked_cmd = [
        "git",
        "-C",
        str(project_root),
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        rel_data_root,
    ]
    untracked_result = subprocess.run(
        untracked_cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if untracked_result.returncode == 0:
        for line in untracked_result.stdout.splitlines():
            value = line.strip()
            if not value:
                continue
            changed.add((project_root / value).absolute())

    return changed


def normalize_scope_paths(project_root: Path, paths: list[str]) -> set[Path]:
    scoped: set[Path] = set()
    for raw in paths:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = (project_root / candidate).absolute()
        else:
            candidate = candidate.absolute()
        scoped.add(candidate)
    return scoped


def is_within(path: Path, container: Path) -> bool:
    try:
        path.relative_to(container)
        return True
    except ValueError:
        return False


def is_entity_in_scope(entity: EntityRecord, scope_paths: set[Path] | None) -> bool:
    if scope_paths is None:
        return True
    if len(scope_paths) == 0:
        return False

    for scoped in scope_paths:
        if scoped == entity.directory or scoped == entity.index_path:
            return True
        if is_within(scoped, entity.directory):
            return True
        if is_within(entity.directory, scoped):
            return True
    return False


def is_edge_in_scope(edge: EdgeFile, scope_paths: set[Path] | None) -> bool:
    if scope_paths is None:
        return True
    if len(scope_paths) == 0:
        return False

    for scoped in scope_paths:
        if scoped == edge.path:
            return True
        if is_within(scoped, edge.path.parent):
            return True
        if is_within(edge.path, scoped):
            return True
    return False


def is_source_in_scope(source: SourceFile, scope_paths: set[Path] | None) -> bool:
    if scope_paths is None:
        return True
    if len(scope_paths) == 0:
        return False

    for scoped in scope_paths:
        if scoped == source.directory or scoped == source.index_path:
            return True
        if is_within(scoped, source.directory):
            return True
        if is_within(source.directory, scoped):
            return True
    return False


def append_issue(
    issues: list[ValidationIssue],
    *,
    code: str,
    path: Path,
    message: str,
    line: int | None = None,
    project_root: Path,
) -> None:
    try:
        rel = path.relative_to(project_root).as_posix()
    except ValueError:
        rel = path.as_posix()
    issues.append(ValidationIssue(code=code, path=rel, message=message, line=line))


def read_json_file(path: Path, issues: list[ValidationIssue], project_root: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        append_issue(
            issues,
            code="missing_file",
            path=path,
            message="missing file",
            project_root=project_root,
        )
        return None
    except json.JSONDecodeError as exc:
        append_issue(
            issues,
            code="invalid_json",
            path=path,
            message=f"JSON parse error: {exc.msg}",
            line=exc.lineno,
            project_root=project_root,
        )
        return None

    if not isinstance(data, dict):
        append_issue(
            issues,
            code="invalid_json",
            path=path,
            message="expected top-level JSON object",
            project_root=project_root,
        )
        return None
    return data


def validate_jsonl(
    *,
    path: Path,
    model_cls: type,
    issues: list[ValidationIssue],
    project_root: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    if not path.exists():
        append_issue(
            issues,
            code="missing_file",
            path=path,
            message="missing file",
            project_root=project_root,
        )
        return rows

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                append_issue(
                    issues,
                    code="invalid_jsonl",
                    path=path,
                    message=f"JSONL parse error: {exc.msg}",
                    line=line_number,
                    project_root=project_root,
                )
                continue

            try:
                record = model_cls.model_validate(payload)
            except ValidationError as exc:
                append_issue(
                    issues,
                    code="schema_error",
                    path=path,
                    message=exc.errors()[0]["msg"],
                    line=line_number,
                    project_root=project_root,
                )
                continue

            normalized = record.model_dump(by_alias=True)
            row_id = str(normalized.get("id") or "").strip()
            if row_id:
                if row_id in seen_ids:
                    append_issue(
                        issues,
                        code="duplicate_row_id",
                        path=path,
                        message=f"duplicate row id {row_id}",
                        line=line_number,
                        project_root=project_root,
                    )
                    continue
                seen_ids.add(row_id)

            rows.append(normalized)

    return rows


def read_index_frontmatter(
    *,
    path: Path,
    entity_label: str,
    issues: list[ValidationIssue],
    project_root: Path,
) -> dict[str, Any] | None:
    if not path.exists():
        append_issue(
            issues,
            code="missing_file",
            path=path,
            message="missing file",
            project_root=project_root,
        )
        return None

    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        append_issue(
            issues,
            code="missing_frontmatter",
            path=path,
            message=f"{entity_label} index.md must include YAML frontmatter",
            project_root=project_root,
        )
        return None

    try:
        payload = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        append_issue(
            issues,
            code="invalid_frontmatter",
            path=path,
            message=f"invalid YAML frontmatter: {exc}",
            project_root=project_root,
        )
        return None

    if not isinstance(payload, dict):
        append_issue(
            issues,
            code="invalid_frontmatter",
            path=path,
            message="frontmatter must be a YAML mapping",
            project_root=project_root,
        )
        return None

    return payload


def extract_citation_keys(text: str) -> set[str]:
    return {match.group(1).strip() for match in FOOTNOTE_REF_RE.finditer(text) if match.group(1).strip()}


def read_citations_from_markdown(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return extract_citation_keys(path.read_text(encoding="utf-8"))


def read_citations_from_jsonl(path: Path) -> set[str]:
    citations: set[str] = set()
    if not path.exists():
        return citations

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            for value in payload.values():
                if isinstance(value, str):
                    citations.update(extract_citation_keys(value))
    return citations


def validate_entities(
    *,
    project_root: Path,
    data_root: Path,
    entities: dict[str, EntityRecord],
    scope_paths: set[Path] | None,
    issues: list[ValidationIssue],
) -> tuple[dict[str, dict[str, list[dict[str, Any]]]], dict[str, set[str]]]:
    loaded_rows: dict[str, dict[str, list[dict[str, Any]]]] = {}
    citation_keys_by_entity: dict[str, set[str]] = {}

    for rel_dir in sorted(entities):
        entity = entities[rel_dir]
        if not is_entity_in_scope(entity, scope_paths):
            continue

        if not entity.index_path.exists():
            append_issue(
                issues,
                code="missing_file",
                path=entity.index_path,
                message="missing index.md",
                project_root=project_root,
            )

        edges_dir = entity.directory / "edges"
        if not edges_dir.exists() or not edges_dir.is_dir():
            append_issue(
                issues,
                code="missing_edges_dir",
                path=edges_dir,
                message="missing edges/ directory",
                project_root=project_root,
            )

        rows_for_entity: dict[str, list[dict[str, Any]]] = {}
        citations_for_entity = read_citations_from_markdown(entity.index_path)

        if entity.kind in {"person", "org"}:
            changelog_path = entity.directory / "changelog.jsonl"
            rows_for_entity["changelog"] = validate_jsonl(
                path=changelog_path,
                model_cls=ChangelogRow,
                issues=issues,
                project_root=project_root,
            )
            citations_for_entity.update(read_citations_from_jsonl(changelog_path))

        if entity.kind == "person":
            employment_path = entity.directory / "employment-history.jsonl"
            rows_for_entity["employment"] = validate_jsonl(
                path=employment_path,
                model_cls=EmploymentHistoryRow,
                issues=issues,
                project_root=project_root,
            )
            citations_for_entity.update(read_citations_from_jsonl(employment_path))
            looking_path = entity.directory / "looking-for.jsonl"
            rows_for_entity["looking_for"] = validate_jsonl(
                path=looking_path,
                model_cls=LookingForRow,
                issues=issues,
                project_root=project_root,
            )
            citations_for_entity.update(read_citations_from_jsonl(looking_path))

        loaded_rows[rel_dir] = rows_for_entity
        citation_keys_by_entity[rel_dir] = citations_for_entity

    entity_rel_paths = set(entities.keys())
    for rel_dir, row_groups in loaded_rows.items():
        for row in row_groups.get("employment", []):
            ref = row.get("organization_ref")
            if not ref:
                continue
            if ref not in entity_rel_paths:
                append_issue(
                    issues,
                    code="invalid_reference",
                    path=data_root / rel_dir / "employment-history.jsonl",
                    message=f"organization_ref not found: {ref}",
                    project_root=project_root,
                )

    return loaded_rows, citation_keys_by_entity


def validate_sources(
    *,
    project_root: Path,
    sources: dict[str, SourceFile],
    scope_paths: set[Path] | None,
    issues: list[ValidationIssue],
) -> tuple[dict[str, SourceRecord], dict[str, str]]:
    validated: dict[str, SourceRecord] = {}
    source_by_citation_key: dict[str, str] = {}

    for rel_dir in sorted(sources):
        source_file = sources[rel_dir]
        if not is_source_in_scope(source_file, scope_paths):
            continue

        if not source_file.index_path.exists():
            append_issue(
                issues,
                code="missing_file",
                path=source_file.index_path,
                message="missing index.md",
                project_root=project_root,
            )
            continue

        frontmatter = read_index_frontmatter(
            path=source_file.index_path,
            entity_label="source",
            issues=issues,
            project_root=project_root,
        )
        if frontmatter is None:
            continue

        try:
            record = SourceRecord.model_validate(frontmatter)
        except ValidationError as exc:
            append_issue(
                issues,
                code="schema_error",
                path=source_file.index_path,
                message=exc.errors()[0]["msg"],
                project_root=project_root,
            )
            continue

        if record.id != source_file.source_id:
            append_issue(
                issues,
                code="source_id_mismatch",
                path=source_file.index_path,
                message=f"frontmatter id {record.id} does not match directory {source_file.source_id}",
                project_root=project_root,
            )

        try:
            canonical_source_path = source_file.index_path.relative_to(project_root).as_posix()
        except ValueError:
            canonical_source_path = source_file.index_path.as_posix()
        if record.source_path != canonical_source_path:
            append_issue(
                issues,
                code="source_path_mismatch",
                path=source_file.index_path,
                message=f"source-path must be {canonical_source_path}",
                project_root=project_root,
            )

        source_path = (project_root / record.source_path).absolute()
        if not source_path.exists():
            append_issue(
                issues,
                code="invalid_reference",
                path=source_file.index_path,
                message=f"source-path not found: {record.source_path}",
                project_root=project_root,
            )

        if record.html_capture_path:
            html_capture = source_file.directory / record.html_capture_path
            if not html_capture.exists():
                append_issue(
                    issues,
                    code="invalid_reference",
                    path=source_file.index_path,
                    message=f"html-capture-path not found: {record.html_capture_path}",
                    project_root=project_root,
                )
        if record.screenshot_path:
            screenshot_capture = source_file.directory / record.screenshot_path
            if not screenshot_capture.exists():
                append_issue(
                    issues,
                    code="invalid_reference",
                    path=source_file.index_path,
                    message=f"screenshot-path not found: {record.screenshot_path}",
                    project_root=project_root,
                )

        existing_rel_dir = source_by_citation_key.get(record.citation_key)
        if existing_rel_dir and existing_rel_dir != rel_dir:
            append_issue(
                issues,
                code="duplicate_citation_key",
                path=source_file.index_path,
                message=(
                    f"citation-key {record.citation_key} already used by "
                    f"{(project_root / 'data' / existing_rel_dir / 'index.md').relative_to(project_root).as_posix()}"
                ),
                project_root=project_root,
            )
        else:
            source_by_citation_key[record.citation_key] = rel_dir

        validated[rel_dir] = record

    return validated, source_by_citation_key


def validate_edge_files(
    *,
    project_root: Path,
    data_root: Path,
    edge_files: list[EdgeFile],
    entities: dict[str, EntityRecord],
    scope_paths: set[Path] | None,
    issues: list[ValidationIssue],
) -> tuple[dict[str, tuple[EdgeRecord, EdgeFile]], dict[str, list[Path]]]:
    edge_by_id: dict[str, tuple[EdgeRecord, EdgeFile]] = {}
    duplicate_edge_files: dict[str, list[Path]] = {}

    for edge_file in edge_files:
        if not is_edge_in_scope(edge_file, scope_paths):
            continue
        payload = read_json_file(edge_file.path, issues, project_root)
        if payload is None:
            continue

        try:
            record = EdgeRecord.model_validate(payload)
        except ValidationError as exc:
            append_issue(
                issues,
                code="schema_error",
                path=edge_file.path,
                message=exc.errors()[0]["msg"],
                project_root=project_root,
            )
            continue

        raw_relation = payload.get("relation")
        canonical_relation = record.relation.value
        if isinstance(raw_relation, str) and raw_relation.strip() != canonical_relation:
            append_issue(
                issues,
                code="non_canonical_relation",
                path=edge_file.path,
                message=f"use canonical relation '{canonical_relation}' instead of '{raw_relation.strip()}'",
                project_root=project_root,
            )

        expected_name = f"edge@{record.id}.json"
        if edge_file.path.name != expected_name:
            append_issue(
                issues,
                code="edge_filename_mismatch",
                path=edge_file.path,
                message=f"expected filename {expected_name}",
                project_root=project_root,
            )

        if record.from_entity not in entities:
            append_issue(
                issues,
                code="invalid_reference",
                path=edge_file.path,
                message=f"from entity not found: {record.from_entity}",
                project_root=project_root,
            )
        if record.to_entity not in entities:
            append_issue(
                issues,
                code="invalid_reference",
                path=edge_file.path,
                message=f"to entity not found: {record.to_entity}",
                project_root=project_root,
            )
        for source_ref in record.sources:
            source_rel_dir = source_ref.split("#", 1)[0]
            source_entity = entities.get(source_rel_dir)
            if source_entity is None:
                append_issue(
                    issues,
                    code="invalid_reference",
                    path=edge_file.path,
                    message=f"source reference not found: {source_ref}",
                    project_root=project_root,
                )
                continue
            if source_entity.kind != "source":
                append_issue(
                    issues,
                    code="invalid_reference",
                    path=edge_file.path,
                    message=f"edge source must target a source entity: {source_ref}",
                    project_root=project_root,
                )

        if record.id in edge_by_id:
            existing_file = edge_by_id[record.id][1]
            duplicate_edge_files.setdefault(record.id, [existing_file.path]).append(edge_file.path)
        else:
            edge_by_id[record.id] = (record, edge_file)

    for edge_id, files in sorted(duplicate_edge_files.items()):
        unique_paths = sorted(set(files), key=lambda path: path.as_posix())
        for path in unique_paths:
            append_issue(
                issues,
                code="duplicate_edge_id",
                path=path,
                message=f"duplicate edge id {edge_id}",
                project_root=project_root,
            )

    symlinks_by_edge_file: dict[str, list[Path]] = {}
    edge_root = data_root / "edge"

    scan_entities: dict[str, EntityRecord] = {}
    if scope_paths is None:
        scan_entities = dict(entities)
    else:
        for rel_dir, entity in entities.items():
            if is_entity_in_scope(entity, scope_paths):
                scan_entities[rel_dir] = entity
        for record, _ in edge_by_id.values():
            if record.from_entity in entities:
                scan_entities[record.from_entity] = entities[record.from_entity]
            if record.to_entity in entities:
                scan_entities[record.to_entity] = entities[record.to_entity]

    for rel_dir, entity in sorted(scan_entities.items()):

        edges_dir = entity.directory / "edges"
        if not edges_dir.exists() or not edges_dir.is_dir():
            continue

        for candidate in sorted(edges_dir.glob("edge@*.json")):
            if not candidate.is_symlink():
                append_issue(
                    issues,
                    code="invalid_symlink",
                    path=candidate,
                    message="edge backlink must be a symlink",
                    project_root=project_root,
                )
                continue

            link_target_raw = os.readlink(candidate)
            if Path(link_target_raw).is_absolute():
                append_issue(
                    issues,
                    code="invalid_symlink",
                    path=candidate,
                    message="symlink target must be relative",
                    project_root=project_root,
                )
                continue

            resolved = (candidate.parent / link_target_raw).resolve()
            if not resolved.exists():
                append_issue(
                    issues,
                    code="broken_symlink",
                    path=candidate,
                    message="symlink target does not exist",
                    project_root=project_root,
                )
                continue

            if not is_within(resolved, edge_root):
                append_issue(
                    issues,
                    code="invalid_symlink",
                    path=candidate,
                    message="symlink target must resolve under data-root/edge",
                    project_root=project_root,
                )
                continue

            symlinks_by_edge_file.setdefault(resolved.as_posix(), []).append(candidate)

    for edge_id, (record, edge_file) in sorted(edge_by_id.items()):
        if scope_paths is not None and not is_edge_in_scope(edge_file, scope_paths):
            continue

        canonical_edge = edge_file.path.resolve().as_posix()
        observed_symlinks = sorted(
            symlinks_by_edge_file.get(canonical_edge, []),
            key=lambda path: path.as_posix(),
        )

        expected_paths: list[Path] = []
        if record.from_entity in entities:
            expected_paths.append(entities[record.from_entity].directory / "edges" / f"edge@{record.id}.json")
        if record.to_entity in entities:
            expected_paths.append(entities[record.to_entity].directory / "edges" / f"edge@{record.id}.json")

        unique_observed = {path.as_posix() for path in observed_symlinks}
        if len(unique_observed) != 2:
            append_issue(
                issues,
                code="edge_backlink_count",
                path=edge_file.path,
                message=f"expected exactly 2 endpoint symlinks, found {len(unique_observed)}",
                project_root=project_root,
            )

        for expected in expected_paths:
            if not expected.exists():
                append_issue(
                    issues,
                    code="missing_symlink",
                    path=expected,
                    message=f"missing endpoint symlink for edge {record.id}",
                    project_root=project_root,
                )

    return edge_by_id, symlinks_by_edge_file


def validate_entity_citations(
    *,
    project_root: Path,
    entities: dict[str, EntityRecord],
    citation_keys_by_entity: dict[str, set[str]],
    source_by_citation_key: dict[str, str],
    scope_paths: set[Path] | None,
    issues: list[ValidationIssue],
) -> None:
    for rel_dir, citation_keys in sorted(citation_keys_by_entity.items()):
        entity = entities.get(rel_dir)
        if entity is None:
            continue
        if not is_entity_in_scope(entity, scope_paths):
            continue
        for citation_key in sorted(citation_keys):
            if citation_key in source_by_citation_key:
                continue
            append_issue(
                issues,
                code="unresolved_citation",
                path=entity.index_path,
                message=f"footnote citation has no matching source key: {citation_key}",
                project_root=project_root,
            )


def run_validation(
    *,
    project_root: Path,
    data_root: Path,
    scope_paths: set[Path] | None,
    scope_label: str,
) -> dict[str, Any]:
    issues: list[ValidationIssue] = []

    if not data_root.exists() or not data_root.is_dir():
        issues.append(
            ValidationIssue(
                code="missing_data_root",
                path=data_root.as_posix(),
                message="data root does not exist",
            )
        )
        return format_result(
            project_root=project_root,
            data_root=data_root,
            scope_label=scope_label,
            issues=issues,
            checked_entities=0,
            checked_sources=0,
            checked_edges=0,
            checked_jsonl_files=0,
        )

    entities = gather_entities(data_root)
    sources = gather_source_files(data_root)
    edge_files = gather_edge_files(data_root)

    loaded_rows, citation_keys_by_entity = validate_entities(
        project_root=project_root,
        data_root=data_root,
        entities=entities,
        scope_paths=scope_paths,
        issues=issues,
    )

    _, source_by_citation_key = validate_sources(
        project_root=project_root,
        sources=sources,
        scope_paths=scope_paths,
        issues=issues,
    )
    validate_entity_citations(
        project_root=project_root,
        entities=entities,
        citation_keys_by_entity=citation_keys_by_entity,
        source_by_citation_key=source_by_citation_key,
        scope_paths=scope_paths,
        issues=issues,
    )

    validate_edge_files(
        project_root=project_root,
        data_root=data_root,
        edge_files=edge_files,
        entities=entities,
        scope_paths=scope_paths,
        issues=issues,
    )

    checked_entities = len([entity for entity in entities.values() if is_entity_in_scope(entity, scope_paths)])
    checked_sources = len([source for source in sources.values() if is_source_in_scope(source, scope_paths)])
    checked_edges = len([edge for edge in edge_files if is_edge_in_scope(edge, scope_paths)])
    checked_jsonl_files = 0
    for entity_rel_dir in loaded_rows:
        entity = entities[entity_rel_dir]
        if entity.kind in {"person", "org"}:
            checked_jsonl_files += 1
        if entity.kind == "person":
            checked_jsonl_files += 2

    return format_result(
        project_root=project_root,
        data_root=data_root,
        scope_label=scope_label,
        issues=issues,
        checked_entities=checked_entities,
        checked_sources=checked_sources,
        checked_edges=checked_edges,
        checked_jsonl_files=checked_jsonl_files,
    )


def format_result(
    *,
    project_root: Path,
    data_root: Path,
    scope_label: str,
    issues: list[ValidationIssue],
    checked_entities: int,
    checked_sources: int,
    checked_edges: int,
    checked_jsonl_files: int,
) -> dict[str, Any]:
    sorted_issues = sorted(
        issues,
        key=lambda issue: (
            issue.path,
            0 if issue.line is None else issue.line,
            issue.code,
            issue.message,
        ),
    )

    try:
        data_root_rel = data_root.relative_to(project_root).as_posix()
    except ValueError:
        data_root_rel = data_root.as_posix()

    return {
        "ok": len(sorted_issues) == 0,
        "scope": scope_label,
        "data_root": data_root_rel,
        "checked": {
            "entities": checked_entities,
            "sources": checked_sources,
            "edge_files": checked_edges,
            "jsonl_files": checked_jsonl_files,
        },
        "error_count": len(sorted_issues),
        "errors": [issue.as_dict() for issue in sorted_issues],
    }


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    data_root = infer_data_root(project_root, args.data_root)

    scope_paths: set[Path] | None = None
    scope_label = "full"

    if args.paths:
        scope_paths = normalize_scope_paths(project_root, args.paths)
        scope_label = "paths"
    elif args.changed:
        scope_paths = collect_changed_paths(project_root, data_root)
        scope_label = "changed"

    result = run_validation(
        project_root=project_root,
        data_root=data_root,
        scope_paths=scope_paths,
        scope_label=scope_label,
    )

    if args.pretty:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, sort_keys=True))

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
