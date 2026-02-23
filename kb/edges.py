from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from kb.schemas import EdgeRecord, EmploymentHistoryRow, SourceRecord, parse_partial_date
from kb.validate import gather_edge_files, gather_entities, gather_source_files

EDGE_ID_SANITIZE_RE = re.compile(r"[^a-z0-9]+")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]]+)\](?!:)")


def relpath(path: Path, project_root: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()


def sanitize_fragment(value: str) -> str:
    lowered = value.strip().lower()
    normalized = EDGE_ID_SANITIZE_RE.sub("-", lowered).strip("-")
    return normalized or "unknown"


def shard_for_value(value: str) -> str:
    letters = re.sub(r"[^a-z0-9]", "", value.lower())
    if not letters:
        return "zz"
    if len(letters) == 1:
        return f"{letters}z"
    return letters[:2]


def relation_for_employment() -> str:
    return "works_at"


def relation_for_citation() -> str:
    return "cites"


def extract_citation_keys(text: str | None) -> list[str]:
    if not text:
        return []

    seen: set[str] = set()
    ordered: list[str] = []
    for match in FOOTNOTE_REF_RE.finditer(text):
        key = match.group(1).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def parse_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    payload = yaml.safe_load(match.group(1)) or {}
    if not isinstance(payload, dict):
        return {}
    return payload


def load_source_lookup(data_root: Path, project_root: Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    source_by_citation_key: dict[str, str] = {}
    issues: list[dict[str, Any]] = []

    for rel_dir, source in sorted(gather_source_files(data_root).items()):
        frontmatter = parse_frontmatter(source.index_path)
        try:
            record = SourceRecord.model_validate(frontmatter)
        except ValidationError as exc:
            issues.append(
                {
                    "code": "schema_error",
                    "path": relpath(source.index_path, project_root),
                    "message": exc.errors()[0]["msg"],
                }
            )
            continue

        existing = source_by_citation_key.get(record.citation_key)
        if existing and existing != rel_dir:
            issues.append(
                {
                    "code": "duplicate_citation_key",
                    "path": relpath(source.index_path, project_root),
                    "message": (
                        f"citation-key {record.citation_key} already mapped to "
                        f"{existing}"
                    ),
                }
            )
            continue
        source_by_citation_key[record.citation_key] = rel_dir

    return source_by_citation_key, issues


def load_employment_rows(path: Path, project_root: Path) -> tuple[list[EmploymentHistoryRow], list[dict[str, Any]]]:
    rows: list[EmploymentHistoryRow] = []
    issues: list[dict[str, Any]] = []

    if not path.exists():
        return rows, issues

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                issues.append(
                    {
                        "code": "invalid_jsonl",
                        "path": relpath(path, project_root),
                        "line": line_number,
                        "message": f"JSON parse error: {exc.msg}",
                    }
                )
                continue
            try:
                row = EmploymentHistoryRow.model_validate(payload)
            except ValidationError as exc:
                issues.append(
                    {
                        "code": "schema_error",
                        "path": relpath(path, project_root),
                        "line": line_number,
                        "message": exc.errors()[0]["msg"],
                    }
                )
                continue
            rows.append(row)
    return rows, issues


def resolve_citation_source_refs(
    raw_source_value: str | None,
    source_by_citation_key: dict[str, str],
) -> tuple[list[str], list[str]]:
    refs: list[str] = []
    missing_keys: list[str] = []

    for key in extract_citation_keys(raw_source_value):
        source_ref = source_by_citation_key.get(key)
        if source_ref is None:
            if key not in missing_keys:
                missing_keys.append(key)
            continue
        if source_ref not in refs:
            refs.append(source_ref)

    return refs, missing_keys


def build_edge_notes(row: EmploymentHistoryRow) -> str:
    parts = [
        f"Role: {row.role}",
        f"Period: {row.period}",
    ]
    if row.notes:
        parts.append(f"Details: {row.notes}")
    return " | ".join(parts)


def index_existing_edges(data_root: Path) -> tuple[dict[str, Path], set[str]]:
    edge_path_by_id: dict[str, Path] = {}
    duplicate_existing_ids: set[str] = set()

    for edge_file in gather_edge_files(data_root):
        if not edge_file.path.name.startswith("edge@") or not edge_file.path.name.endswith(".json"):
            continue
        edge_id = edge_file.path.name[len("edge@") : -len(".json")]
        if edge_id in edge_path_by_id:
            duplicate_existing_ids.add(edge_id)
            continue
        edge_path_by_id[edge_id] = edge_file.path

    return edge_path_by_id, duplicate_existing_ids


def write_edge_record(
    *,
    edge_record: EdgeRecord,
    edge_root: Path,
    project_root: Path,
    edge_path_by_id: dict[str, Path],
    created_paths: list[str],
    updated_paths: list[str],
    issues: list[dict[str, Any]],
) -> bool:
    edge_id = edge_record.id
    edge_path = edge_root / shard_for_value(edge_id) / f"edge@{edge_id}.json"
    edge_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(edge_record.model_dump(by_alias=True), indent=2, sort_keys=True) + "\n"

    existing_path = edge_path_by_id.get(edge_id)
    if existing_path is not None and existing_path != edge_path:
        issues.append(
            {
                "code": "edge_path_conflict",
                "path": relpath(existing_path, project_root),
                "message": (
                    f"edge id {edge_id} exists at {relpath(existing_path, project_root)}; "
                    f"expected {relpath(edge_path, project_root)}"
                ),
            }
        )
        return False

    if edge_path.exists():
        current = edge_path.read_text(encoding="utf-8")
        if current == rendered:
            return False

    edge_path.write_text(rendered, encoding="utf-8")
    edge_path_by_id[edge_id] = edge_path

    rel = relpath(edge_path, project_root)
    if existing_path is None:
        created_paths.append(rel)
    else:
        updated_paths.append(rel)
    return True


def derive_employment_edges(
    *,
    project_root: Path,
    data_root: Path,
    as_of: str | None = None,
) -> dict[str, Any]:
    effective_as_of = parse_partial_date(as_of or dt.date.today().isoformat())
    entities = gather_entities(data_root)
    edge_root = data_root / "edge"
    edge_root.mkdir(parents=True, exist_ok=True)

    source_by_citation_key, source_issues = load_source_lookup(data_root, project_root)

    edge_path_by_id, duplicate_existing_ids = index_existing_edges(data_root)
    created_paths: list[str] = []
    updated_paths: list[str] = []
    issues: list[dict[str, Any]] = [*source_issues]
    person_entities_scanned = 0
    employment_rows_scanned = 0
    candidate_rows = 0
    unchanged_existing = 0

    for edge_id in sorted(duplicate_existing_ids):
        issues.append(
            {
                "code": "duplicate_edge_id",
                "path": relpath(edge_path_by_id[edge_id], project_root),
                "message": f"duplicate existing edge id {edge_id}",
            }
        )

    for rel_dir in sorted(entities):
        entity = entities[rel_dir]
        if entity.kind != "person":
            continue
        person_entities_scanned += 1

        employment_path = entity.directory / "employment-history.jsonl"
        rows, row_issues = load_employment_rows(employment_path, project_root)
        issues.extend(row_issues)
        employment_rows_scanned += len(rows)

        for row in rows:
            if not row.organization_ref:
                continue
            candidate_rows += 1

            if row.organization_ref not in entities:
                issues.append(
                    {
                        "code": "invalid_reference",
                        "path": relpath(employment_path, project_root),
                        "message": f"organization_ref not found: {row.organization_ref}",
                    }
                )
                continue

            source_refs, missing_keys = resolve_citation_source_refs(row.source, source_by_citation_key)
            for citation_key in missing_keys:
                issues.append(
                    {
                        "code": "invalid_reference",
                        "path": relpath(employment_path, project_root),
                        "message": f"unknown citation key in source field: {citation_key}",
                    }
                )

            if not source_refs:
                issues.append(
                    {
                        "code": "missing_source_reference",
                        "path": relpath(employment_path, project_root),
                        "message": f"employment row {row.id} does not resolve to any source references",
                    }
                )
                continue

            edge_id = sanitize_fragment(f"employment-{entity.entity_id}-{row.id}")

            edge_record = EdgeRecord.model_validate(
                {
                    "id": edge_id,
                    "relation": relation_for_employment(),
                    "directed": True,
                    "from": entity.rel_dir,
                    "to": row.organization_ref,
                    "first_noted_at": effective_as_of,
                    "last_verified_at": effective_as_of,
                    "valid_from": None,
                    "valid_to": None,
                    "sources": source_refs,
                    "notes": build_edge_notes(row),
                }
            )

            changed = write_edge_record(
                edge_record=edge_record,
                edge_root=edge_root,
                project_root=project_root,
                edge_path_by_id=edge_path_by_id,
                created_paths=created_paths,
                updated_paths=updated_paths,
                issues=issues,
            )
            if not changed and edge_id in edge_path_by_id:
                edge_path = edge_root / shard_for_value(edge_id) / f"edge@{edge_id}.json"
                if edge_path.exists() and edge_path.read_text(encoding="utf-8") == (
                    json.dumps(edge_record.model_dump(by_alias=True), indent=2, sort_keys=True) + "\n"
                ):
                    unchanged_existing += 1

    created_paths.sort()
    updated_paths.sort()
    return {
        "ok": len(issues) == 0,
        "data_root": relpath(data_root, project_root),
        "as_of": effective_as_of,
        "person_entities_scanned": person_entities_scanned,
        "employment_rows_scanned": employment_rows_scanned,
        "candidate_rows_with_org_ref": candidate_rows,
        "created_edge_files": len(created_paths),
        "updated_edge_files": len(updated_paths),
        "unchanged_existing": unchanged_existing,
        "issue_count": len(issues),
        "issues": issues,
        "created_paths": created_paths,
        "updated_paths": updated_paths,
    }


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


def read_citations_from_markdown(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(extract_citation_keys(path.read_text(encoding="utf-8")))


def derive_citation_edges(
    *,
    project_root: Path,
    data_root: Path,
    as_of: str | None = None,
) -> dict[str, Any]:
    effective_as_of = parse_partial_date(as_of or dt.date.today().isoformat())
    entities = gather_entities(data_root)
    edge_root = data_root / "edge"
    edge_root.mkdir(parents=True, exist_ok=True)

    source_by_citation_key, source_issues = load_source_lookup(data_root, project_root)

    edge_path_by_id, duplicate_existing_ids = index_existing_edges(data_root)
    created_paths: list[str] = []
    updated_paths: list[str] = []
    issues: list[dict[str, Any]] = [*source_issues]
    entities_scanned = 0
    citation_links_scanned = 0
    unchanged_existing = 0

    for edge_id in sorted(duplicate_existing_ids):
        issues.append(
            {
                "code": "duplicate_edge_id",
                "path": relpath(edge_path_by_id[edge_id], project_root),
                "message": f"duplicate existing edge id {edge_id}",
            }
        )

    for rel_dir in sorted(entities):
        entity = entities[rel_dir]
        entities_scanned += 1
        citation_keys = read_citations_from_markdown(entity.index_path)

        if entity.kind == "person":
            citation_keys.update(read_citations_from_jsonl(entity.directory / "employment-history.jsonl"))
            citation_keys.update(read_citations_from_jsonl(entity.directory / "looking-for.jsonl"))
            citation_keys.update(read_citations_from_jsonl(entity.directory / "changelog.jsonl"))
        elif entity.kind == "org":
            citation_keys.update(read_citations_from_jsonl(entity.directory / "changelog.jsonl"))

        for citation_key in sorted(citation_keys):
            citation_links_scanned += 1
            target_source_rel = source_by_citation_key.get(citation_key)
            if target_source_rel is None:
                issues.append(
                    {
                        "code": "unresolved_citation",
                        "path": relpath(entity.index_path, project_root),
                        "message": f"citation key has no source record: {citation_key}",
                    }
                )
                continue

            if target_source_rel == entity.rel_dir:
                continue

            edge_id = sanitize_fragment(
                f"citation-{entity.kind}-{entity.entity_id}-{citation_key}"
            )
            edge_record = EdgeRecord.model_validate(
                {
                    "id": edge_id,
                    "relation": relation_for_citation(),
                    "directed": True,
                    "from": entity.rel_dir,
                    "to": target_source_rel,
                    "first_noted_at": effective_as_of,
                    "last_verified_at": effective_as_of,
                    "valid_from": None,
                    "valid_to": None,
                    "sources": [target_source_rel],
                    "notes": (
                        f"Derived citation edge from footnote [^{citation_key}] "
                        f"in {entity.rel_dir}"
                    ),
                }
            )

            changed = write_edge_record(
                edge_record=edge_record,
                edge_root=edge_root,
                project_root=project_root,
                edge_path_by_id=edge_path_by_id,
                created_paths=created_paths,
                updated_paths=updated_paths,
                issues=issues,
            )
            if not changed and edge_id in edge_path_by_id:
                edge_path = edge_root / shard_for_value(edge_id) / f"edge@{edge_id}.json"
                if edge_path.exists() and edge_path.read_text(encoding="utf-8") == (
                    json.dumps(edge_record.model_dump(by_alias=True), indent=2, sort_keys=True) + "\n"
                ):
                    unchanged_existing += 1

    created_paths.sort()
    updated_paths.sort()
    return {
        "ok": len(issues) == 0,
        "data_root": relpath(data_root, project_root),
        "as_of": effective_as_of,
        "entities_scanned": entities_scanned,
        "citation_links_scanned": citation_links_scanned,
        "created_edge_files": len(created_paths),
        "updated_edge_files": len(updated_paths),
        "unchanged_existing": unchanged_existing,
        "issue_count": len(issues),
        "issues": issues,
        "created_paths": created_paths,
        "updated_paths": updated_paths,
    }


def read_edge_record(path: Path) -> EdgeRecord:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("expected top-level JSON object")
    return EdgeRecord.model_validate(payload)


def sync_edge_backlinks(*, project_root: Path, data_root: Path) -> dict[str, Any]:
    entities = gather_entities(data_root)
    edge_files = gather_edge_files(data_root)

    issues: list[dict[str, Any]] = []
    planned_links: dict[Path, Path] = {}
    seen_edge_ids: dict[str, Path] = {}
    valid_edge_files = 0

    for edge_file in edge_files:
        try:
            edge_record = read_edge_record(edge_file.path)
        except FileNotFoundError:
            issues.append(
                {
                    "code": "missing_file",
                    "path": relpath(edge_file.path, project_root),
                    "message": "edge file missing",
                }
            )
            continue
        except json.JSONDecodeError as exc:
            issues.append(
                {
                    "code": "invalid_json",
                    "path": relpath(edge_file.path, project_root),
                    "line": exc.lineno,
                    "message": f"JSON parse error: {exc.msg}",
                }
            )
            continue
        except (ValidationError, ValueError) as exc:
            issues.append(
                {
                    "code": "schema_error",
                    "path": relpath(edge_file.path, project_root),
                    "message": str(exc),
                }
            )
            continue

        if edge_record.id in seen_edge_ids:
            issues.append(
                {
                    "code": "duplicate_edge_id",
                    "path": relpath(edge_file.path, project_root),
                    "message": (
                        f"duplicate edge id {edge_record.id}; "
                        f"already defined in {relpath(seen_edge_ids[edge_record.id], project_root)}"
                    ),
                }
            )
            continue
        seen_edge_ids[edge_record.id] = edge_file.path

        from_entity = entities.get(edge_record.from_entity)
        to_entity = entities.get(edge_record.to_entity)
        if from_entity is None:
            issues.append(
                {
                    "code": "invalid_reference",
                    "path": relpath(edge_file.path, project_root),
                    "message": f"from entity not found: {edge_record.from_entity}",
                }
            )
            continue
        if to_entity is None:
            issues.append(
                {
                    "code": "invalid_reference",
                    "path": relpath(edge_file.path, project_root),
                    "message": f"to entity not found: {edge_record.to_entity}",
                }
            )
            continue

        valid_edge_files += 1
        for entity in (from_entity, to_entity):
            link_path = entity.directory / "edges" / f"edge@{edge_record.id}.json"
            existing_target = planned_links.get(link_path)
            if existing_target is not None and existing_target != edge_file.path:
                issues.append(
                    {
                        "code": "conflicting_backlink",
                        "path": relpath(link_path, project_root),
                        "message": (
                            f"conflicting symlink targets for edge {edge_record.id}: "
                            f"{relpath(existing_target, project_root)} vs {relpath(edge_file.path, project_root)}"
                        ),
                    }
                )
                continue
            planned_links[link_path] = edge_file.path

    links_removed = 0
    for entity in entities.values():
        edges_dir = entity.directory / "edges"
        edges_dir.mkdir(parents=True, exist_ok=True)
        gitkeep = edges_dir / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.write_text("", encoding="utf-8")

        for existing in sorted(edges_dir.glob("edge@*.json"), key=lambda path: path.as_posix()):
            if existing.is_dir():
                issues.append(
                    {
                        "code": "invalid_backlink_path",
                        "path": relpath(existing, project_root),
                        "message": "expected file or symlink, found directory",
                    }
                )
                continue
            existing.unlink()
            links_removed += 1

    links_created = 0
    for link_path, target_path in sorted(planned_links.items(), key=lambda item: item[0].as_posix()):
        relative_target = os.path.relpath(target_path, start=link_path.parent).replace(os.sep, "/")
        os.symlink(relative_target, link_path)
        links_created += 1

    return {
        "ok": len(issues) == 0,
        "data_root": relpath(data_root, project_root),
        "edge_files_scanned": len(edge_files),
        "edge_files_valid": valid_edge_files,
        "planned_backlinks": len(planned_links),
        "links_removed": links_removed,
        "links_created": links_created,
        "issue_count": len(issues),
        "issues": issues,
    }
