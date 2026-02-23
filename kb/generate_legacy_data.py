from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from kb.migrate_v2 import render_frontmatter
from kb.schemas import ChangelogRow, EdgeRecord, EmploymentHistoryRow, LookingForRow, NoteRecord

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
SECTION_HEADING_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
RELATION_TEXT_RE = re.compile(r"_+")


@dataclass(frozen=True)
class EntityPage:
    kind: str
    slug: str
    ref: str
    title: str
    index_path: Path
    directory: Path
    output_path: Path
    metadata: dict[str, Any]
    body: str
    employment_rows: list[EmploymentHistoryRow]
    looking_for_rows: list[LookingForRow]
    changelog_rows: list[ChangelogRow]


@dataclass(frozen=True)
class NotePage:
    slug: str
    index_path: Path
    output_path: Path
    metadata: dict[str, Any]
    body: str


def split_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(markdown)
    if not match:
        return {}, markdown
    metadata = yaml.safe_load(match.group(1)) or {}
    body = markdown[match.end() :].lstrip("\n")
    return metadata, body


def parse_entity_slug(entity_dir_name: str, *, kind: str) -> str:
    prefix = f"{kind}@"
    if not entity_dir_name.startswith(prefix):
        raise ValueError(f"unexpected {kind} directory: {entity_dir_name}")
    return entity_dir_name[len(prefix) :]


def parse_note_slug(note_dir_name: str) -> str:
    prefix = "note@"
    if not note_dir_name.startswith(prefix):
        raise ValueError(f"unexpected note directory: {note_dir_name}")
    return note_dir_name[len(prefix) :]


def strip_leading_h1(body: str) -> str:
    lines = body.splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index < len(lines) and lines[index].startswith("# "):
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
    return "\n".join(lines[index:]).strip()


def read_jsonl_rows(path: Path, model_cls: type[Any]) -> list[Any]:
    if not path.exists():
        return []
    rows: list[Any] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.as_posix()}:{line_number} JSON parse error: {exc.msg}") from exc
            try:
                rows.append(model_cls.model_validate(payload))
            except ValidationError as exc:
                raise ValueError(
                    f"{path.as_posix()}:{line_number} schema error: {exc.errors()[0]['msg']}"
                ) from exc
    return rows


def normalize_note_output_path(note_metadata: dict[str, Any], *, output_root: Path, slug: str) -> Path:
    source_path = str(note_metadata.get("source-path") or "").strip()
    if source_path.startswith("data/") and source_path.endswith(".md"):
        rel = Path(source_path).relative_to("data")
        return output_root / rel

    category = str(note_metadata.get("source-category") or "").strip().strip("/")
    if category:
        return output_root / "notes" / category / f"{slug}.md"
    return output_root / "notes" / f"{slug}.md"


def escape_cell(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", "<br>")


def as_text(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(items) if items else "-"
    text = str(value).strip()
    return text or "-"


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    divider_line = "| " + " | ".join(["---"] * len(headers)) + " |"
    body_lines = ["| " + " | ".join(escape_cell(cell) for cell in row) + " |" for row in rows]
    return "\n".join([header_line, divider_line, *body_lines])


def split_link_target(raw_target: str) -> tuple[str, str]:
    first_special = len(raw_target)
    for marker in ("#", "?"):
        marker_index = raw_target.find(marker)
        if marker_index != -1:
            first_special = min(first_special, marker_index)
    if first_special == len(raw_target):
        return raw_target, ""
    return raw_target[:first_special], raw_target[first_special:]


def rewrite_markdown_links(
    markdown: str,
    *,
    source_path: Path,
    output_path: Path,
    path_map: dict[Path, Path],
) -> str:
    def replace(match: re.Match[str]) -> str:
        label = match.group(1)
        target = match.group(2).strip()
        if not target:
            return match.group(0)
        if target.startswith("#"):
            return match.group(0)
        if target.startswith("/") and not target.startswith("//"):
            return match.group(0)
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
            return match.group(0)

        path_part, suffix = split_link_target(target)
        if not path_part:
            return match.group(0)

        resolved = (source_path.parent / path_part).resolve()
        rewritten = path_map.get(resolved)
        if rewritten is None:
            return match.group(0)

        rel = os.path.relpath(rewritten, output_path.parent).replace(os.sep, "/")
        return f"[{label}]({rel}{suffix})"

    return MARKDOWN_LINK_RE.sub(replace, markdown)


def rewrite_entity_ref_links(value: str, *, entity_by_ref: dict[str, EntityPage], output_path: Path) -> str:
    text = value.strip()
    entity = entity_by_ref.get(text)
    if entity is None:
        return text
    rel = os.path.relpath(entity.output_path, output_path.parent).replace(os.sep, "/")
    return f"[{entity.title}]({rel})"


def relation_text(relation: str) -> str:
    return RELATION_TEXT_RE.sub(" ", relation).strip()


def load_entities(*, source_root: Path, output_root: Path) -> dict[str, EntityPage]:
    entities: dict[str, EntityPage] = {}
    for kind in ("person", "org"):
        kind_root = source_root / kind
        if not kind_root.exists():
            continue
        for index_path in sorted(kind_root.rglob("index.md")):
            directory = index_path.parent
            slug = parse_entity_slug(directory.name, kind=kind)
            rel_dir = directory.relative_to(source_root).as_posix()

            metadata, body = split_frontmatter(index_path.read_text(encoding="utf-8"))
            title_key = "person" if kind == "person" else "org"
            title = str(metadata.get(title_key) or slug.replace("-", " ").title()).strip()

            employment_rows: list[EmploymentHistoryRow] = []
            looking_for_rows: list[LookingForRow] = []
            if kind == "person":
                employment_rows = read_jsonl_rows(directory / "employment-history.jsonl", EmploymentHistoryRow)
                looking_for_rows = read_jsonl_rows(directory / "looking-for.jsonl", LookingForRow)

            changelog_rows = read_jsonl_rows(directory / "changelog.jsonl", ChangelogRow)

            output_path = output_root / kind / f"{slug}.md"
            entities[rel_dir] = EntityPage(
                kind=kind,
                slug=slug,
                ref=rel_dir,
                title=title,
                index_path=index_path,
                directory=directory,
                output_path=output_path,
                metadata=metadata,
                body=body,
                employment_rows=employment_rows,
                looking_for_rows=looking_for_rows,
                changelog_rows=changelog_rows,
            )
    return entities


def load_notes(*, source_root: Path, output_root: Path) -> list[NotePage]:
    pages: list[NotePage] = []
    note_root = source_root / "note"
    if not note_root.exists():
        return pages

    for index_path in sorted(note_root.rglob("index.md")):
        directory = index_path.parent
        slug = parse_note_slug(directory.name)
        metadata, body = split_frontmatter(index_path.read_text(encoding="utf-8"))
        # Validate note frontmatter schema before emitting.
        NoteRecord.model_validate(metadata)
        output_path = normalize_note_output_path(metadata, output_root=output_root, slug=slug)
        pages.append(
            NotePage(
                slug=slug,
                index_path=index_path,
                output_path=output_path,
                metadata=metadata,
                body=body,
            )
        )

    return pages


def load_edges(*, source_root: Path) -> list[EdgeRecord]:
    edge_root = source_root / "edge"
    if not edge_root.exists():
        return []

    records: list[EdgeRecord] = []
    for edge_path in sorted(edge_root.rglob("edge@*.json")):
        payload = json.loads(edge_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"edge file must contain an object: {edge_path.as_posix()}")
        records.append(EdgeRecord.model_validate(payload))
    return records


def contains_section(body: str, section_title: str) -> bool:
    normalized = section_title.strip().lower()
    for match in SECTION_HEADING_RE.finditer(body):
        if match.group(1).strip().lower() == normalized:
            return True
    return False


def render_employment_section(
    page: EntityPage,
    *,
    entity_by_ref: dict[str, EntityPage],
    path_map: dict[Path, Path],
) -> str:
    if not page.employment_rows:
        return "- No employment history entries recorded."

    rows: list[list[str]] = []
    for row in page.employment_rows:
        if row.organization_ref:
            organization = rewrite_entity_ref_links(
                row.organization_ref,
                entity_by_ref=entity_by_ref,
                output_path=page.output_path,
            )
        else:
            organization = row.organization
        notes = rewrite_markdown_links(
            row.notes or "-",
            source_path=page.index_path,
            output_path=page.output_path,
            path_map=path_map,
        )
        source = rewrite_markdown_links(
            row.source or row.source_path,
            source_path=page.index_path,
            output_path=page.output_path,
            path_map=path_map,
        )
        rows.append([row.period, organization, row.role, notes, source])
    return render_table(["Period", "Organization", "Role", "Notes", "Source"], rows)


def render_looking_for_section(page: EntityPage) -> str:
    if not page.looking_for_rows:
        return "- No active asks recorded."

    rows: list[list[str]] = []
    for row in page.looking_for_rows:
        rows.append(
            [
                row.ask,
                as_text(row.details),
                as_text(row.first_asked_at),
                as_text(row.last_checked_at),
                row.status.value,
                as_text(row.notes),
            ]
        )
    return render_table(
        ["Ask", "Details", "First Asked", "Last Checked", "Status", "Notes"],
        rows,
    )


def render_changelog_section(
    page: EntityPage,
    *,
    path_map: dict[Path, Path],
) -> str:
    if not page.changelog_rows:
        return "- No changelog entries recorded."

    rows: list[list[str]] = []
    for row in page.changelog_rows:
        summary = rewrite_markdown_links(
            row.summary,
            source_path=page.index_path,
            output_path=page.output_path,
            path_map=path_map,
        )
        rows.append([row.changed_at, summary, row.source_path])
    return render_table(["Changed At", "Summary", "Source"], rows)


def render_edges_section(
    page: EntityPage,
    *,
    edge_map: dict[str, list[EdgeRecord]],
    entity_by_ref: dict[str, EntityPage],
) -> str:
    edges = edge_map.get(page.ref, [])
    if not edges:
        return "- No edge relationships recorded."

    rows: list[list[str]] = []
    ordered = sorted(edges, key=lambda edge: (edge.relation.value, edge.id))
    for edge in ordered:
        if edge.from_entity == page.ref:
            direction = "outbound" if edge.directed else "undirected"
            target_ref = edge.to_entity
        else:
            direction = "inbound" if edge.directed else "undirected"
            target_ref = edge.from_entity
        target = rewrite_entity_ref_links(
            target_ref,
            entity_by_ref=entity_by_ref,
            output_path=page.output_path,
        )
        rows.append(
            [
                relation_text(edge.relation.value),
                direction,
                target,
                edge.first_noted_at,
                edge.last_verified_at,
                as_text(edge.notes),
            ]
        )

    return render_table(
        ["Relation", "Direction", "Target", "First Noted", "Last Verified", "Notes"],
        rows,
    )


def render_entity_page(
    page: EntityPage,
    *,
    entity_by_ref: dict[str, EntityPage],
    edge_map: dict[str, list[EdgeRecord]],
    path_map: dict[Path, Path],
) -> str:
    metadata = dict(page.metadata)
    narrative = rewrite_markdown_links(
        strip_leading_h1(page.body),
        source_path=page.index_path,
        output_path=page.output_path,
        path_map=path_map,
    )

    lines: list[str] = [render_frontmatter(metadata).rstrip(), "", f"# {page.title}"]
    if narrative:
        lines.extend(["", narrative])

    if page.kind == "person" and not contains_section(narrative, "Employment History"):
        lines.extend(["", "## Employment History", "", render_employment_section(page, entity_by_ref=entity_by_ref, path_map=path_map)])

    if page.kind == "person" and not contains_section(narrative, "Looking For"):
        lines.extend(["", "## Looking For", "", render_looking_for_section(page)])

    if not contains_section(narrative, "Edge Links"):
        lines.extend(
            [
                "",
                "## Edge Links",
                "",
                render_edges_section(page, edge_map=edge_map, entity_by_ref=entity_by_ref),
            ]
        )

    if not contains_section(narrative, "Changelog"):
        lines.extend(["", "## Changelog", "", render_changelog_section(page, path_map=path_map)])

    return "\n".join(lines).rstrip() + "\n"


def render_note_page(
    page: NotePage,
    *,
    path_map: dict[Path, Path],
) -> str:
    metadata = dict(page.metadata)
    body = rewrite_markdown_links(
        page.body.strip(),
        source_path=page.index_path,
        output_path=page.output_path,
        path_map=path_map,
    )
    chunks = [render_frontmatter(metadata).rstrip()]
    if body:
        chunks.extend(["", body])
    return "\n".join(chunks).rstrip() + "\n"


def prepare_path_map(
    *,
    entities: dict[str, EntityPage],
    notes: list[NotePage],
    source_root: Path,
    output_root: Path,
) -> tuple[dict[Path, Path], list[tuple[Path, Path]]]:
    path_map: dict[Path, Path] = {}
    image_pairs: list[tuple[Path, Path]] = []
    used_targets: dict[Path, Path] = {}

    for entity in entities.values():
        path_map[entity.index_path.resolve()] = entity.output_path

    for note in notes:
        path_map[note.index_path.resolve()] = note.output_path

    for entity in entities.values():
        images_dir = entity.directory / "images"
        if not images_dir.exists():
            continue
        for image_path in sorted(images_dir.iterdir()):
            if not image_path.is_file():
                continue
            target = output_root / entity.kind / "images" / image_path.name
            conflict = used_targets.get(target)
            if conflict and conflict != image_path:
                raise ValueError(
                    f"image name collision for {target.as_posix()}: "
                    f"{conflict.as_posix()} vs {image_path.as_posix()}"
                )
            used_targets[target] = image_path
            image_pairs.append((image_path, target))
            path_map[image_path.resolve()] = target

    # Map direct source paths like data/.../index.md to generated markdown output.
    for source_path, rendered_path in list(path_map.items()):
        try:
            relative = source_path.relative_to(source_root)
        except ValueError:
            continue
        path_map[(source_root / relative).resolve()] = rendered_path

    return path_map, image_pairs


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def copy_templates_if_available(*, project_root: Path, output_root: Path) -> list[str]:
    copied: list[str] = []
    for kind in ("person", "org"):
        source = project_root / "data-old" / kind / "_template.md"
        target = output_root / kind / "_template.md"
        if not source.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(target.relative_to(project_root).as_posix())
    return copied


def generate_legacy_data(
    *,
    project_root: Path,
    source_root: Path,
    output_root: Path,
    clean: bool = True,
) -> dict[str, Any]:
    if not source_root.exists():
        raise FileNotFoundError(f"source root does not exist: {source_root.as_posix()}")

    entities = load_entities(source_root=source_root, output_root=output_root)
    notes = load_notes(source_root=source_root, output_root=output_root)
    edges = load_edges(source_root=source_root)

    if clean and output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    path_map, image_pairs = prepare_path_map(
        entities=entities,
        notes=notes,
        source_root=source_root,
        output_root=output_root,
    )

    edge_map: dict[str, list[EdgeRecord]] = defaultdict(list)
    for edge in edges:
        edge_map[edge.from_entity].append(edge)
        edge_map[edge.to_entity].append(edge)

    for entity in entities.values():
        rendered = render_entity_page(
            entity,
            entity_by_ref=entities,
            edge_map=edge_map,
            path_map=path_map,
        )
        write_text(entity.output_path, rendered)

    for note in notes:
        rendered = render_note_page(note, path_map=path_map)
        write_text(note.output_path, rendered)

    copied_images: list[str] = []
    for source, target in image_pairs:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied_images.append(target.relative_to(project_root).as_posix())

    copied_templates = copy_templates_if_available(project_root=project_root, output_root=output_root)
    return {
        "ok": True,
        "source_root": source_root.relative_to(project_root).as_posix(),
        "output_root": output_root.relative_to(project_root).as_posix(),
        "entities_rendered": len(entities),
        "person_pages": sum(1 for page in entities.values() if page.kind == "person"),
        "org_pages": sum(1 for page in entities.values() if page.kind == "org"),
        "notes_rendered": len(notes),
        "edges_loaded": len(edges),
        "images_copied": len(copied_images),
        "templates_copied": copied_templates,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate consolidated legacy-style markdown files in data/ from canonical records."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    parser.add_argument(
        "--source-root",
        default="data",
        help="Source root containing canonical records (default: data).",
    )
    parser.add_argument(
        "--output-root",
        default="data",
        help="Output root for generated consolidated markdown (default: data).",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not delete output root before writing.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    source_root = Path(args.source_root)
    if not source_root.is_absolute():
        source_root = (project_root / source_root).resolve()
    output_root = Path(args.output_root)
    if not output_root.is_absolute():
        output_root = (project_root / output_root).resolve()

    result = generate_legacy_data(
        project_root=project_root,
        source_root=source_root,
        output_root=output_root,
        clean=not args.no_clean,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
