#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from kb.schemas import SourceType, shard_for_slug

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]]+)\](?!:)")
FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]]+)\]:\s*(.+)$", re.MULTILINE)
SOURCE_LINK_RE = re.compile(r"\[(?P<label>[^\]]+)\]\((?P<url>[^)]+)\)")
DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


@dataclass(frozen=True)
class CitationDefinition:
    original_key: str
    resolved_key: str
    definition: str
    source_old_path: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate KB citations and notes into source records.")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository root.",
    )
    parser.add_argument(
        "--legacy-commit",
        default="8c38d92",
        help="Git commit containing data-old footnote definitions.",
    )
    return parser.parse_args()


def run_git(project_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(project_root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "git command failed")
    return completed.stdout


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    payload = yaml.safe_load(match.group(1)) or {}
    if not isinstance(payload, dict):
        payload = {}
    body = text[match.end() :]
    return payload, body


def render_markdown(frontmatter: dict[str, Any], body: str) -> str:
    dumped = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).rstrip()
    chunks = ["---", dumped, "---", "", body.strip()]
    return "\n".join(chunks).rstrip() + "\n"


def extract_footnote_defs(markdown: str) -> dict[str, str]:
    return {match.group(1).strip(): match.group(2).strip() for match in FOOTNOTE_DEF_RE.finditer(markdown)}


def extract_footnote_refs(text: str) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for match in FOOTNOTE_REF_RE.finditer(text):
        key = match.group(1).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def load_migration_map(project_root: Path) -> tuple[dict[str, str], dict[str, str]]:
    mapping_file = project_root / "data" / "migration-path-map.csv"
    old_to_current: dict[str, str] = {}
    current_to_old: dict[str, str] = {}

    with mapping_file.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            old_path = str(row["old_path"]).strip()
            new_index_path = str(row["new_index_path"]).strip()
            current_path = new_index_path.replace("data-new/", "data/")
            old_to_current[old_path] = current_path
            current_to_old[current_path] = old_path

    return old_to_current, current_to_old


def load_legacy_defs(
    *,
    project_root: Path,
    legacy_commit: str,
    old_paths: set[str],
) -> tuple[dict[str, dict[str, str]], dict[str, set[str]]]:
    defs_by_old_path: dict[str, dict[str, str]] = {}
    defs_by_key: dict[str, set[str]] = {}

    for old_path in sorted(old_paths):
        git_path = old_path.replace("data/", "data-old/", 1)
        try:
            content = run_git(project_root, "show", f"{legacy_commit}:{git_path}")
        except RuntimeError:
            continue
        per_file_defs = extract_footnote_defs(content)
        defs_by_old_path[old_path] = per_file_defs
        for key, definition in per_file_defs.items():
            defs_by_key.setdefault(key, set()).add(definition)

    return defs_by_old_path, defs_by_key


def canonicalize_definition(definition: str) -> str:
    return " ".join(definition.split())


def slugify(text: str) -> str:
    lowered = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return lowered or "unknown"


def parse_definition_metadata(definition: str) -> dict[str, Any]:
    clean_definition = canonicalize_definition(definition)
    clean_no_prefix = clean_definition
    for prefix in ("Source image:", "Source:"):
        if clean_no_prefix.startswith(prefix):
            clean_no_prefix = clean_no_prefix[len(prefix) :].strip()

    link_match = SOURCE_LINK_RE.search(clean_no_prefix)
    link_label = link_match.group("label").strip() if link_match else ""
    link_url = link_match.group("url").strip() if link_match else ""
    local_note_link = bool(link_url and (link_url.startswith("../notes/") or link_url.startswith("data/notes/")))
    if local_note_link and link_match:
        clean_definition = clean_definition.replace(link_match.group(0), link_label)
        clean_no_prefix = clean_no_prefix.replace(link_match.group(0), link_label)
        link_url = ""

    parsed_url = urlparse(link_url) if link_url else None
    has_http_url = bool(parsed_url and parsed_url.scheme in {"http", "https"} and parsed_url.netloc)

    source_type = SourceType.document
    if has_http_url:
        source_type = SourceType.website
    elif local_note_link:
        source_type = SourceType.note
    elif "internal" in clean_no_prefix.lower() or "user-provided" in clean_no_prefix.lower():
        source_type = SourceType.internal
    elif link_url.startswith("../") or link_url.startswith("data/"):
        source_type = SourceType.note

    title = link_label or clean_no_prefix.split(".", 1)[0].strip()
    if not title:
        title = "Untitled Source"

    retrieved_at_match = DATE_RE.search(clean_no_prefix)
    retrieved_at = retrieved_at_match.group(1) if retrieved_at_match else None

    source_category = "citations/misc"
    if source_type == SourceType.internal:
        source_category = "citations/internal"
    elif source_type == SourceType.note:
        source_category = "citations/notes"
    elif has_http_url:
        host = (parsed_url.netloc or "web").lower().removeprefix("www.")
        source_category = f"citations/{slugify(host)}"

    return {
        "title": title,
        "source_type": source_type,
        "url": link_url if has_http_url else None,
        "retrieved_at": retrieved_at,
        "source_category": source_category,
        "citation_text": clean_definition,
    }


def replace_footnote_refs(text: str, key_map: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        resolved = key_map.get(key, key)
        return f"[^${resolved}]".replace("$", "")

    return FOOTNOTE_REF_RE.sub(replace, text)


def rewrite_jsonl_citations(path: Path, key_map: dict[str, str]) -> None:
    if not path.exists():
        return

    updated_lines: list[str] = []
    changed = False
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                updated_lines.append(line)
                continue
            payload = json.loads(stripped)
            for key, value in list(payload.items()):
                if isinstance(value, str):
                    replaced = replace_footnote_refs(value, key_map)
                    if replaced != value:
                        payload[key] = replaced
                        changed = True
            updated_lines.append(json.dumps(payload, sort_keys=True) + "\n")

    if changed:
        path.write_text("".join(updated_lines), encoding="utf-8")


def extract_jsonl_refs(path: Path) -> set[str]:
    refs: set[str] = set()
    if not path.exists():
        return refs

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            if not isinstance(payload, dict):
                continue
            for value in payload.values():
                if isinstance(value, str):
                    refs.update(extract_footnote_refs(value))
    return refs


def normalize_source_paths(path: Path, old_to_current: dict[str, str]) -> None:
    if not path.exists():
        return

    updated_lines: list[str] = []
    changed = False
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                updated_lines.append(line)
                continue
            payload = json.loads(stripped)
            source_path = str(payload.get("source_path") or "").strip()
            mapped = old_to_current.get(source_path)
            if mapped and mapped != source_path:
                payload["source_path"] = mapped
                changed = True
            updated_lines.append(json.dumps(payload, sort_keys=True) + "\n")

    if changed:
        path.write_text("".join(updated_lines), encoding="utf-8")


def ensure_source_edges_dir(source_dir: Path) -> None:
    edges_dir = source_dir / "edges"
    edges_dir.mkdir(parents=True, exist_ok=True)
    gitkeep = edges_dir / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text("", encoding="utf-8")


def migrate_note_records(project_root: Path) -> None:
    note_root = project_root / "data" / "note"
    if not note_root.exists():
        return

    source_root = project_root / "data" / "source"
    source_root.mkdir(parents=True, exist_ok=True)

    for index_path in sorted(note_root.rglob("index.md")):
        note_dir = index_path.parent
        if not note_dir.name.startswith("note@"):
            continue

        slug = note_dir.name[len("note@") :]
        target_dir = source_root / shard_for_slug(slug) / f"source@{slug}"
        target_dir.parent.mkdir(parents=True, exist_ok=True)

        text = index_path.read_text(encoding="utf-8")
        frontmatter, body = split_frontmatter(text)

        frontmatter["id"] = f"source@{slug}"
        frontmatter["source-type"] = SourceType.note.value
        frontmatter["note-type"] = str(frontmatter.get("note-type") or "note")
        frontmatter["citation-key"] = str(frontmatter.get("citation-key") or slug)
        frontmatter["source-path"] = f"data/source/{shard_for_slug(slug)}/source@{slug}/index.md"
        frontmatter["source-category"] = str(frontmatter.get("source-category") or "notes").strip("/")

        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(note_dir, target_dir)
        (target_dir / "index.md").write_text(render_markdown(frontmatter, body), encoding="utf-8")
        ensure_source_edges_dir(target_dir)

    shutil.rmtree(note_root)


def determine_citation_key_mapping(
    *,
    project_root: Path,
    current_file_to_old_path: dict[str, str],
    refs_by_file: dict[str, set[str]],
    defs_by_old_path: dict[str, dict[str, str]],
    defs_by_key: dict[str, set[str]],
) -> tuple[dict[tuple[str, str], CitationDefinition], dict[str, list[CitationDefinition]]]:
    per_file_key_defs: dict[tuple[str, str], CitationDefinition] = {}
    all_defs_by_key: dict[str, list[CitationDefinition]] = {}

    for current_path, keys in sorted(refs_by_file.items()):
        old_path = current_file_to_old_path.get(current_path)
        per_file_defs = defs_by_old_path.get(old_path or "", {})

        for key in sorted(keys):
            definition = per_file_defs.get(key)
            if definition is None:
                candidates = sorted(defs_by_key.get(key, set()))
                if len(candidates) == 1:
                    definition = candidates[0]
                elif len(candidates) > 1:
                    # Resolve ambiguity by preferring non-LinkedIn definition in person pages only when exact match exists.
                    definition = candidates[0]
                else:
                    source_index = (
                        project_root / "data" / "source" / shard_for_slug(key) / f"source@{key}" / "index.md"
                    )
                    if source_index.exists():
                        existing_frontmatter, _ = split_frontmatter(source_index.read_text(encoding="utf-8"))
                        existing_definition = str(existing_frontmatter.get("citation-text") or "").strip()
                        if existing_definition:
                            definition = existing_definition
            if definition is None:
                raise RuntimeError(f"No definition found for citation key {key} in {current_path}")

            citation = CitationDefinition(
                original_key=key,
                resolved_key=key,
                definition=definition,
                source_old_path=old_path or "",
            )
            per_file_key_defs[(current_path, key)] = citation
            all_defs_by_key.setdefault(key, []).append(citation)

    # Disambiguate keys reused with different definitions.
    for key, citations in all_defs_by_key.items():
        unique_defs = sorted({canonicalize_definition(item.definition) for item in citations})
        if len(unique_defs) <= 1:
            continue

        definition_to_new_key: dict[str, str] = {}
        for normalized_def in unique_defs:
            definition_hash = hashlib.sha1(normalized_def.encode("utf-8")).hexdigest()[:8]
            definition_to_new_key[normalized_def] = f"{key}-{definition_hash}"

        for idx, item in enumerate(citations):
            normalized_def = canonicalize_definition(item.definition)
            new_key = definition_to_new_key[normalized_def]
            citations[idx] = CitationDefinition(
                original_key=item.original_key,
                resolved_key=new_key,
                definition=item.definition,
                source_old_path=item.source_old_path,
            )

    # Refresh lookup after disambiguation.
    refreshed: dict[tuple[str, str], CitationDefinition] = {}
    for key, citations in all_defs_by_key.items():
        by_definition = {canonicalize_definition(item.definition): item for item in citations}
        for (current_path, original_key), existing in list(per_file_key_defs.items()):
            if original_key != key:
                continue
            normalized = canonicalize_definition(existing.definition)
            refreshed[(current_path, original_key)] = by_definition[normalized]

    return refreshed, all_defs_by_key


def create_source_record(
    *,
    project_root: Path,
    citation_key: str,
    definition: str,
) -> str:
    metadata = parse_definition_metadata(definition)
    shard = shard_for_slug(citation_key)
    source_dir = project_root / "data" / "source" / shard / f"source@{citation_key}"
    source_dir.mkdir(parents=True, exist_ok=True)
    ensure_source_edges_dir(source_dir)

    source_path = f"data/source/{shard}/source@{citation_key}/index.md"
    frontmatter: dict[str, Any] = {
        "id": f"source@{citation_key}",
        "title": metadata["title"],
        "source-type": metadata["source_type"].value,
        "citation-key": citation_key,
        "source-path": source_path,
        "source-category": metadata["source_category"],
        "url": metadata["url"],
        "retrieved-at": metadata["retrieved_at"],
        "citation-text": metadata["citation_text"],
    }

    # Remove null fields for cleaner frontmatter.
    frontmatter = {key: value for key, value in frontmatter.items() if value not in (None, "")}

    body_lines = [
        f"# {metadata['title']}",
        "",
        "Imported from legacy footnote definition.",
        "",
        f"- Citation key: `{citation_key}`",
        f"- Legacy definition: {metadata['citation_text']}",
    ]
    if metadata["url"]:
        body_lines.append(f"- URL: {metadata['url']}")

    (source_dir / "index.md").write_text(render_markdown(frontmatter, "\n".join(body_lines)), encoding="utf-8")
    return source_path


def rewrite_markdown_links(project_root: Path) -> None:
    for path in sorted(project_root.glob("data/**/*.md")):
        text = path.read_text(encoding="utf-8")
        replaced = text.replace("/note/", "/source/")
        if replaced != text:
            path.write_text(replaced, encoding="utf-8")


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()

    old_to_current, current_to_old = load_migration_map(project_root)

    migrate_note_records(project_root)

    markdown_files = sorted(project_root.glob("data/person/**/index.md")) + sorted(project_root.glob("data/org/**/index.md"))
    refs_by_file: dict[str, set[str]] = {}
    for path in markdown_files:
        rel = path.relative_to(project_root).as_posix()
        refs = set(extract_footnote_refs(path.read_text(encoding="utf-8")))
        for jsonl_name in ("employment-history.jsonl", "looking-for.jsonl", "changelog.jsonl"):
            refs.update(extract_jsonl_refs(path.parent / jsonl_name))
        refs_by_file[rel] = refs

    old_paths = {old for old in current_to_old.values()}
    defs_by_old_path, defs_by_key = load_legacy_defs(
        project_root=project_root,
        legacy_commit=args.legacy_commit,
        old_paths=old_paths,
    )

    resolved_per_file, defs_grouped = determine_citation_key_mapping(
        project_root=project_root,
        current_file_to_old_path=current_to_old,
        refs_by_file=refs_by_file,
        defs_by_old_path=defs_by_old_path,
        defs_by_key=defs_by_key,
    )

    # Create source records for all referenced citation definitions.
    created_source_paths: set[str] = set()
    for citations in defs_grouped.values():
        for citation in citations:
            source_path = create_source_record(
                project_root=project_root,
                citation_key=citation.resolved_key,
                definition=citation.definition,
            )
            created_source_paths.add(source_path)

    # Rewrite markdown citations.
    for path in markdown_files:
        rel = path.relative_to(project_root).as_posix()
        key_map: dict[str, str] = {}
        for key in refs_by_file.get(rel, set()):
            resolved = resolved_per_file[(rel, key)].resolved_key
            key_map[key] = resolved
        text = path.read_text(encoding="utf-8")
        replaced = replace_footnote_refs(text, key_map)
        if replaced != text:
            path.write_text(replaced, encoding="utf-8")

    # Rewrite JSONL citations + canonical source_path fields.
    for entity_index in markdown_files:
        rel = entity_index.relative_to(project_root).as_posix()
        old_path = current_to_old.get(rel)
        if not old_path:
            continue

        key_map: dict[str, str] = {}
        for key in refs_by_file.get(rel, set()):
            key_map[key] = resolved_per_file[(rel, key)].resolved_key

        entity_dir = entity_index.parent
        for jsonl_name in ("employment-history.jsonl", "looking-for.jsonl", "changelog.jsonl"):
            jsonl_path = entity_dir / jsonl_name
            rewrite_jsonl_citations(jsonl_path, key_map)
            normalize_source_paths(jsonl_path, old_to_current)

    # Normalize source paths in source-note records and add edges directory.
    for index_path in sorted(project_root.glob("data/source/**/index.md")):
        source_dir = index_path.parent
        ensure_source_edges_dir(source_dir)

        text = index_path.read_text(encoding="utf-8")
        frontmatter, body = split_frontmatter(text)
        if not frontmatter:
            continue
        rel = index_path.relative_to(project_root).as_posix()
        frontmatter["source-path"] = rel
        if source_dir.name.startswith("source@"):
            slug = source_dir.name[len("source@") :]
            frontmatter["id"] = f"source@{slug}"
            frontmatter["citation-key"] = str(frontmatter.get("citation-key") or slug)
            if frontmatter.get("source-type") == SourceType.note.value and "note-type" not in frontmatter:
                frontmatter["note-type"] = "note"
        index_path.write_text(render_markdown(frontmatter, body), encoding="utf-8")

    rewrite_markdown_links(project_root)

    print(
        json.dumps(
            {
                "ok": True,
                "legacy_commit": args.legacy_commit,
                "source_records_created_or_updated": len(created_source_paths),
                "source_note_records": len(list(project_root.glob("data/source/**/source@*/index.md"))),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
