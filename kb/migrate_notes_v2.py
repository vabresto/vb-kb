from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kb.migrate_v2 import (
    render_frontmatter,
    rewrite_entity_links_in_text,
    rewrite_links_in_value,
    shard_for_slug,
    split_frontmatter,
)
from kb.validate import gather_entities

DATE_PREFIX_RE = re.compile(r"^(?P<date>\d{4}(?:-\d{2})?(?:-\d{2})?)")
HEADING_RE = re.compile(r"^\s*#{1,3}\s+(?P<title>.+?)\s*$")
FRONTMATTER_RAW_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n?", re.DOTALL)


@dataclass(frozen=True)
class NoteSource:
    old_path: Path
    old_rel_path: Path
    slug: str
    new_dir: Path
    new_index: Path


def slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def note_slug_for_rel_path(relative_path: Path) -> str:
    return slugify(relative_path.with_suffix("").as_posix())


def normalize_note_type(value: str | None) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower().replace("-", "_")
    text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
    return text


def infer_note_type(metadata: dict[str, Any], old_rel_path: Path) -> str:
    existing = normalize_note_type(metadata.get("note-type"))
    if existing:
        return existing

    parts = [part.lower() for part in old_rel_path.parts]
    joined = "/".join(parts)

    if "debrief" in joined:
        return "debrief"
    if "transcript" in joined:
        return "transcript"
    if "memo" in joined:
        return "memo"
    if "reflection" in joined or "long-form" in joined:
        return "reflection"
    return "note"


def infer_date(metadata: dict[str, Any], old_rel_path: Path) -> str | None:
    existing = str(metadata.get("date") or "").strip()
    if existing:
        return existing

    match = DATE_PREFIX_RE.match(old_rel_path.stem)
    if match:
        return match.group("date")
    return None


def title_from_slug(slug: str) -> str:
    parts = [part for part in slug.split("-") if part]
    if not parts:
        return "Untitled Note"
    return " ".join(part.capitalize() for part in parts)


def infer_title(metadata: dict[str, Any], body: str, old_rel_path: Path) -> str:
    existing = str(metadata.get("title") or "").strip()
    if existing:
        return existing

    for line in body.splitlines():
        match = HEADING_RE.match(line)
        if match:
            title = match.group("title").strip()
            if title:
                return title

    return title_from_slug(old_rel_path.stem)


def gather_note_sources(*, data_notes_root: Path, output_root: Path) -> list[NoteSource]:
    sources: list[NoteSource] = []
    seen_slugs: dict[str, Path] = {}

    for old_path in sorted(data_notes_root.rglob("*.md")):
        old_rel_path = old_path.relative_to(data_notes_root)
        if any(part.startswith("_") for part in old_rel_path.parts):
            continue

        slug = note_slug_for_rel_path(old_rel_path)
        if not slug:
            continue

        if slug in seen_slugs:
            first = seen_slugs[slug].as_posix()
            raise ValueError(
                f"duplicate note slug '{slug}' for {old_rel_path.as_posix()} and {first}"
            )
        seen_slugs[slug] = old_rel_path

        shard = shard_for_slug(slug)
        new_dir = output_root / "note" / shard / f"note@{slug}"
        sources.append(
            NoteSource(
                old_path=old_path.resolve(),
                old_rel_path=old_rel_path,
                slug=slug,
                new_dir=new_dir,
                new_index=new_dir / "index.md",
            )
        )

    return sources


def build_old_to_new_index(
    *,
    project_root: Path,
    data_new_root: Path,
    notes: list[NoteSource],
) -> dict[Path, Path]:
    mapping: dict[Path, Path] = {}

    entities = gather_entities(data_new_root)
    for entity in entities.values():
        legacy = project_root / "data" / entity.kind / f"{entity.entity_id}.md"
        if legacy.exists():
            mapping[legacy.resolve()] = entity.index_path

    for note in notes:
        mapping[note.old_path.resolve()] = note.new_index

    return mapping


def render_note_index(
    *,
    note: NoteSource,
    project_root: Path,
    old_to_new_index: dict[Path, Path],
) -> str:
    markdown = note.old_path.read_text(encoding="utf-8")
    metadata, body = split_frontmatter(markdown)

    rewritten_metadata = rewrite_links_in_value(
        metadata,
        old_source_path=note.old_path,
        new_source_dir=note.new_dir,
        old_to_new_index=old_to_new_index,
    )
    rewritten_body = rewrite_entity_links_in_text(
        body,
        old_source_path=note.old_path,
        new_source_dir=note.new_dir,
        old_to_new_index=old_to_new_index,
    )

    title = infer_title(rewritten_metadata, rewritten_body, note.old_rel_path)
    note_type = infer_note_type(rewritten_metadata, note.old_rel_path)
    date_value = infer_date(rewritten_metadata, note.old_rel_path)
    source_path = note.old_path.relative_to(project_root).as_posix()
    source_category = note.old_rel_path.parent.as_posix()

    # Preserve existing keys while ensuring canonical note identity keys are present.
    canonical_metadata: dict[str, Any] = dict(rewritten_metadata)
    canonical_metadata["id"] = f"note@{note.slug}"
    canonical_metadata["title"] = title
    canonical_metadata["note-type"] = note_type
    canonical_metadata["date"] = date_value
    canonical_metadata["source-path"] = source_path
    canonical_metadata["source-category"] = source_category

    content_parts: list[str] = [render_frontmatter(canonical_metadata).rstrip()]
    if rewritten_body.strip():
        content_parts.extend(["", rewritten_body.strip()])
    return "\n".join(content_parts).rstrip() + "\n"


def write_mapping_file(
    *,
    project_root: Path,
    output_root: Path,
    notes: list[NoteSource],
) -> None:
    mapping_path = output_root / "migration-path-map.csv"
    rows: dict[str, str] = {}

    if mapping_path.exists():
        with mapping_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                old_path = str(row.get("old_path") or "").strip()
                new_path = str(row.get("new_index_path") or "").strip()
                if old_path and new_path:
                    rows[old_path] = new_path

    for note in notes:
        old_path = note.old_path.relative_to(project_root).as_posix()
        new_path = note.new_index.relative_to(project_root).as_posix()
        rows[old_path] = new_path

    with mapping_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["old_path", "new_index_path"])
        for old_path in sorted(rows):
            writer.writerow([old_path, rows[old_path]])


def run_notes_migration(
    *,
    project_root: Path,
    output_dir: str = "data",
) -> Path:
    output_root = project_root / output_dir
    data_notes_root = project_root / "data" / "notes"
    if not data_notes_root.exists():
        return output_root / "note"

    notes = gather_note_sources(data_notes_root=data_notes_root, output_root=output_root)
    note_root = output_root / "note"
    note_root.mkdir(parents=True, exist_ok=True)
    (note_root / ".gitkeep").write_text("", encoding="utf-8")

    old_to_new_index = build_old_to_new_index(
        project_root=project_root,
        data_new_root=output_root,
        notes=notes,
    )

    for note in notes:
        rendered = render_note_index(
            note=note,
            project_root=project_root,
            old_to_new_index=old_to_new_index,
        )
        note.new_dir.mkdir(parents=True, exist_ok=True)
        note.new_index.write_text(rendered, encoding="utf-8")

    rewrite_note_links_in_entity_indexes(
        project_root=project_root,
        data_new_root=output_root,
        old_to_new_index=old_to_new_index,
    )

    write_mapping_file(project_root=project_root, output_root=output_root, notes=notes)
    return note_root


def split_raw_frontmatter(text: str) -> tuple[str, str]:
    match = FRONTMATTER_RAW_RE.match(text)
    if not match:
        return "", text
    return text[: match.end()], text[match.end() :]


def rewrite_note_links_in_entity_indexes(
    *,
    project_root: Path,
    data_new_root: Path,
    old_to_new_index: dict[Path, Path],
) -> None:
    for kind in ("person", "org"):
        for index_path in sorted((data_new_root / kind).rglob("index.md")):
            entity_dir = index_path.parent
            expected_prefix = f"{kind}@"
            if not entity_dir.name.startswith(expected_prefix):
                continue

            slug = entity_dir.name[len(expected_prefix) :]
            legacy_source = project_root / "data" / kind / f"{slug}.md"
            if not legacy_source.exists():
                continue

            original = index_path.read_text(encoding="utf-8")
            frontmatter_raw, body = split_raw_frontmatter(original)
            rewritten_body = rewrite_entity_links_in_text(
                body,
                old_source_path=legacy_source,
                new_source_dir=entity_dir,
                old_to_new_index=old_to_new_index,
            )
            updated = frontmatter_raw + rewritten_body
            if updated != original:
                index_path.write_text(updated, encoding="utf-8")
