from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from kb.schemas import ChangelogRow, EmploymentHistoryRow, LookingForRow

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
H2_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$")
TABLE_SEPARATOR_RE = re.compile(r"^:?-{3,}:?$")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
CHANGELOG_LINE_RE = re.compile(
    r"^\[(?P<date>\d{4}(?:-\d{2}){0,2})\]\s*:?[ \t]*(?P<summary>.+)$"
)

STRUCTURED_PERSON_HEADINGS = {
    "employment history",
    "looking for",
    "changelog",
}
STRUCTURED_ORG_HEADINGS = {
    "changelog",
}


@dataclass(frozen=True)
class EntitySource:
    kind: str
    slug: str
    old_path: Path
    new_dir: Path
    new_index: Path


@dataclass(frozen=True)
class Section:
    heading: str
    content: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate KB data into v2 folder layout")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root containing data/.",
    )
    parser.add_argument(
        "--output-dir",
        default="data",
        help="Output directory to write v2 data into (default: data).",
    )
    return parser.parse_args()


def shard_for_slug(slug: str) -> str:
    letters = re.sub(r"[^a-z0-9]", "", slug.lower())
    if not letters:
        return "zz"
    if len(letters) == 1:
        return f"{letters}z"
    return letters[:2]


def split_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(markdown)
    if not match:
        return {}, markdown
    metadata = yaml.safe_load(match.group(1)) or {}
    body = markdown[match.end() :].lstrip("\n")
    return metadata, body


def render_frontmatter(metadata: dict[str, Any]) -> str:
    dumped = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=False).rstrip()
    return f"---\n{dumped}\n---\n"


def normalize_heading(value: str) -> str:
    return " ".join(value.lower().split())


def split_h2_sections(body: str) -> tuple[str, list[Section]]:
    lines = body.splitlines()
    preamble_lines: list[str] = []
    current_heading: str | None = None
    current_lines: list[str] = []
    sections: list[Section] = []

    for line in lines:
        heading_match = H2_RE.match(line)
        if heading_match:
            if current_heading is None:
                preamble_lines = current_lines
            else:
                sections.append(
                    Section(heading=current_heading, content="\n".join(current_lines).rstrip())
                )
            current_heading = heading_match.group("title").strip()
            current_lines = [line]
            continue
        current_lines.append(line)

    if current_heading is None:
        return body.strip(), []

    sections.append(Section(heading=current_heading, content="\n".join(current_lines).rstrip()))
    return "\n".join(preamble_lines).strip(), sections


def join_h2_sections(preamble: str, sections: list[Section]) -> str:
    blocks: list[str] = []
    if preamble.strip():
        blocks.append(preamble.strip())
    for section in sections:
        if section.content.strip():
            blocks.append(section.content.strip())
    return "\n\n".join(blocks).strip()


def parse_markdown_table(section_content: str) -> list[dict[str, str]]:
    lines = section_content.splitlines()
    table_lines: list[str] = []
    started = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|"):
            table_lines.append(stripped)
            started = True
            continue
        if started:
            break

    if len(table_lines) < 2:
        return []

    headers = parse_markdown_row(table_lines[0])
    if not headers:
        return []

    data_rows: list[list[str]] = []
    for row_line in table_lines[1:]:
        row = parse_markdown_row(row_line)
        if not row:
            continue
        if len(row) == len(headers) and all(TABLE_SEPARATOR_RE.match(cell) for cell in row):
            continue
        data_rows.append(row)

    rows: list[dict[str, str]] = []
    for row in data_rows:
        padded = row + [""] * max(0, len(headers) - len(row))
        values = padded[: len(headers)]
        rows.append({headers[i]: values[i] for i in range(len(headers))})
    return rows


def parse_markdown_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    return cells


def parse_markdown_link(cell_text: str) -> tuple[str, str | None]:
    match = MARKDOWN_LINK_RE.search(cell_text)
    if not match:
        cleaned = re.sub(r"\[\^[^\]]+\]", "", cell_text).strip()
        return cleaned, None
    label = match.group(1).strip()
    destination = match.group(2).strip()
    return label, destination


def is_external_destination(destination: str) -> bool:
    lower = destination.lower()
    return (
        lower.startswith("http://")
        or lower.startswith("https://")
        or lower.startswith("mailto:")
        or lower.startswith("#")
    )


def split_destination(destination: str) -> tuple[str, str]:
    path_part, hash_part = destination, ""
    if "#" in destination:
        path_part, fragment = destination.split("#", 1)
        hash_part = f"#{fragment}"
    return path_part, hash_part


def resolve_old_destination(old_source_path: Path, destination: str) -> Path | None:
    if not destination or is_external_destination(destination):
        return None
    path_part, _ = split_destination(destination)
    if not path_part:
        return None
    candidate = (old_source_path.parent / path_part).resolve()
    return candidate


def rewrite_entity_links_in_text(
    text: str,
    *,
    old_source_path: Path,
    new_source_dir: Path,
    old_to_new_index: dict[Path, Path],
) -> str:
    def replace(match: re.Match[str]) -> str:
        label = match.group(1)
        destination = match.group(2)
        resolved = resolve_old_destination(old_source_path, destination)
        if resolved is None:
            return match.group(0)

        target = old_to_new_index.get(resolved)
        if target is None:
            return match.group(0)

        path_part, fragment = split_destination(destination)
        rel = os.path.relpath(target, start=new_source_dir)
        rel = rel.replace(os.sep, "/")
        _ = path_part  # retained for readability when debugging replacements.
        return f"[{label}]({rel}{fragment})"

    return MARKDOWN_LINK_RE.sub(replace, text)


def rewrite_links_in_value(
    value: Any,
    *,
    old_source_path: Path,
    new_source_dir: Path,
    old_to_new_index: dict[Path, Path],
) -> Any:
    if isinstance(value, str):
        return rewrite_entity_links_in_text(
            value,
            old_source_path=old_source_path,
            new_source_dir=new_source_dir,
            old_to_new_index=old_to_new_index,
        )
    if isinstance(value, list):
        return [
            rewrite_links_in_value(
                item,
                old_source_path=old_source_path,
                new_source_dir=new_source_dir,
                old_to_new_index=old_to_new_index,
            )
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: rewrite_links_in_value(
                item,
                old_source_path=old_source_path,
                new_source_dir=new_source_dir,
                old_to_new_index=old_to_new_index,
            )
            for key, item in value.items()
        }
    return value


def slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def extract_employment_rows(
    *,
    metadata: dict[str, Any],
    section: Section | None,
    provenance_source_path: str,
    old_source_path: Path,
    new_source_dir: Path,
    old_to_new_index: dict[Path, Path],
    org_name_to_entity: dict[str, str],
    data_new_root: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    if section is not None:
        table_rows = parse_markdown_table(section.content)
        for index, row in enumerate(table_rows, start=1):
            period = (row.get("Period") or "").strip()
            organization_cell = (row.get("Organization") or "").strip()
            role = (row.get("Role") or "").strip()
            notes = (row.get("Notes") or "").strip() or None
            source = (row.get("Source") or "").strip() or None

            if not period or not organization_cell or not role:
                continue

            organization, destination = parse_markdown_link(organization_cell)
            organization_ref: str | None = None
            if destination:
                resolved = resolve_old_destination(old_source_path, destination)
                if resolved in old_to_new_index:
                    target = old_to_new_index[resolved]
                    organization_ref = target.parent.relative_to(data_new_root).as_posix()

            if notes:
                notes = rewrite_entity_links_in_text(
                    notes,
                    old_source_path=old_source_path,
                    new_source_dir=new_source_dir,
                    old_to_new_index=old_to_new_index,
                )
            if source:
                source = rewrite_entity_links_in_text(
                    source,
                    old_source_path=old_source_path,
                    new_source_dir=new_source_dir,
                    old_to_new_index=old_to_new_index,
                )

            payload = EmploymentHistoryRow(
                id=f"employment-{index:03d}",
                period=period,
                organization=organization,
                organization_ref=organization_ref,
                role=role,
                notes=notes,
                source=source,
                source_path=provenance_source_path,
                source_section="employment_history_table",
                source_row=index,
            )
            rows.append(payload.model_dump())

    if rows:
        return rows

    firm = str(metadata.get("firm") or "").strip()
    role = str(metadata.get("role") or "").strip()
    if not firm or not role:
        return []

    org_ref = org_name_to_entity.get(firm.lower())
    if org_ref is None:
        guessed = slugify(firm)
        for key, value in org_name_to_entity.items():
            if slugify(key) == guessed:
                org_ref = value
                break

    payload = EmploymentHistoryRow(
        id="employment-001",
        period="Current",
        organization=firm,
        organization_ref=org_ref,
        role=role,
        notes="Extracted from frontmatter firm/role.",
        source="frontmatter",
        source_path=provenance_source_path,
        source_section="frontmatter_firm_role",
        source_row=1,
    )
    return [payload.model_dump()]


def normalize_looking_for_status(value: str | None) -> str:
    if not value:
        return "open"
    lowered = value.strip().lower()
    if lowered in {"open", "paused", "closed"}:
        return lowered
    return "open"


def normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def extract_looking_for_rows(
    *,
    metadata: dict[str, Any],
    section: Section | None,
    provenance_source_path: str,
    old_source_path: Path,
    new_source_dir: Path,
    old_to_new_index: dict[Path, Path],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    from_frontmatter = metadata.get("looking-for")

    if isinstance(from_frontmatter, list) and from_frontmatter:
        for index, item in enumerate(from_frontmatter, start=1):
            if isinstance(item, dict):
                ask = normalize_optional_text(item.get("ask"))
                details = normalize_optional_text(item.get("details"))
                first_asked_at = normalize_optional_text(item.get("first-asked-at"))
                last_checked_at = normalize_optional_text(item.get("last-checked-at"))
                status = normalize_looking_for_status(normalize_optional_text(item.get("status")))
                notes = normalize_optional_text(item.get("notes"))
            else:
                ask = normalize_optional_text(item)
                details = None
                first_asked_at = None
                last_checked_at = None
                status = "open"
                notes = None

            if not ask:
                continue

            ask = rewrite_entity_links_in_text(
                ask,
                old_source_path=old_source_path,
                new_source_dir=new_source_dir,
                old_to_new_index=old_to_new_index,
            )
            if details:
                details = rewrite_entity_links_in_text(
                    details,
                    old_source_path=old_source_path,
                    new_source_dir=new_source_dir,
                    old_to_new_index=old_to_new_index,
                )
            if notes:
                notes = rewrite_entity_links_in_text(
                    notes,
                    old_source_path=old_source_path,
                    new_source_dir=new_source_dir,
                    old_to_new_index=old_to_new_index,
                )

            payload = LookingForRow(
                id=f"ask-{index:03d}",
                ask=ask,
                details=details,
                first_asked_at=first_asked_at,
                last_checked_at=last_checked_at,
                status=status,
                notes=notes,
                source_path=provenance_source_path,
                source_section="frontmatter_looking_for",
                source_row=index,
            )
            rows.append(payload.model_dump())

    if rows:
        return rows

    if section is None:
        return []

    table_rows = parse_markdown_table(section.content)
    for index, row in enumerate(table_rows, start=1):
        ask = normalize_optional_text(row.get("Ask"))
        if not ask:
            continue
        ask = rewrite_entity_links_in_text(
            ask,
            old_source_path=old_source_path,
            new_source_dir=new_source_dir,
            old_to_new_index=old_to_new_index,
        )
        details = normalize_optional_text(row.get("Details"))
        if details:
            details = rewrite_entity_links_in_text(
                details,
                old_source_path=old_source_path,
                new_source_dir=new_source_dir,
                old_to_new_index=old_to_new_index,
            )

        payload = LookingForRow(
            id=f"ask-{index:03d}",
            ask=ask,
            details=details,
            first_asked_at=normalize_optional_text(row.get("First Asked")),
            last_checked_at=normalize_optional_text(row.get("Last Checked")),
            status=normalize_looking_for_status(normalize_optional_text(row.get("Status"))),
            notes=None,
            source_path=provenance_source_path,
            source_section="looking_for_table",
            source_row=index,
        )
        rows.append(payload.model_dump())

    return rows


def extract_changelog_rows(
    *,
    section: Section | None,
    provenance_source_path: str,
    old_source_path: Path,
    new_source_dir: Path,
    old_to_new_index: dict[Path, Path],
) -> list[dict[str, Any]]:
    if section is None:
        return []

    rows: list[dict[str, Any]] = []
    row_index = 0
    for line in section.content.splitlines()[1:]:
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue

        payload_text = stripped[2:].strip()
        match = CHANGELOG_LINE_RE.match(payload_text)
        if not match:
            alt = re.match(r"^(?P<date>\d{4}(?:-\d{2}){0,2})\s*:\s*(?P<summary>.+)$", payload_text)
            if alt is None:
                continue
            date_text = alt.group("date").strip()
            summary = alt.group("summary").strip()
        else:
            date_text = match.group("date").strip()
            summary = match.group("summary").strip()

        row_index += 1
        summary = rewrite_entity_links_in_text(
            summary,
            old_source_path=old_source_path,
            new_source_dir=new_source_dir,
            old_to_new_index=old_to_new_index,
        )
        payload = ChangelogRow(
            id=f"change-{row_index:03d}",
            changed_at=date_text,
            summary=summary,
            source_path=provenance_source_path,
            source_row=row_index,
        )
        rows.append(payload.model_dump())

    return rows


def remove_structured_sections(kind: str, body: str) -> str:
    preamble, sections = split_h2_sections(body)
    if not sections:
        return body.strip()

    blocklist = STRUCTURED_PERSON_HEADINGS if kind == "person" else STRUCTURED_ORG_HEADINGS
    kept = [section for section in sections if normalize_heading(section.heading) not in blocklist]
    return join_h2_sections(preamble, kept)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")


def copy_entity_images(entity: EntitySource, data_dir: Path) -> None:
    source_images_dir = data_dir / entity.kind / "images"
    if not source_images_dir.exists():
        return

    image_candidates = sorted(source_images_dir.glob(f"{entity.slug}.*"))
    if not image_candidates:
        return

    destination_dir = entity.new_dir / "images"
    destination_dir.mkdir(parents=True, exist_ok=True)
    for source_image in image_candidates:
        shutil.copy2(source_image, destination_dir / source_image.name)


def gather_entities(data_dir: Path, output_root: Path) -> list[EntitySource]:
    entities: list[EntitySource] = []
    for kind in ("person", "org"):
        for old_path in sorted((data_dir / kind).glob("*.md")):
            if old_path.stem.startswith("_"):
                continue
            slug = old_path.stem
            shard = shard_for_slug(slug)
            new_dir = output_root / kind / shard / f"{kind}@{slug}"
            entities.append(
                EntitySource(
                    kind=kind,
                    slug=slug,
                    old_path=old_path.resolve(),
                    new_dir=new_dir,
                    new_index=new_dir / "index.md",
                )
            )
    return entities


def build_org_name_index(entities: list[EntitySource], data_new_root: Path) -> dict[str, str]:
    index: dict[str, str] = {}
    for entity in entities:
        if entity.kind != "org":
            continue
        markdown = entity.old_path.read_text(encoding="utf-8")
        metadata, _ = split_frontmatter(markdown)
        name = str(metadata.get("org") or "").strip()
        if not name:
            continue
        index[name.lower()] = entity.new_dir.relative_to(data_new_root).as_posix()
    return index


def migrate_entity(
    *,
    entity: EntitySource,
    data_new_root: Path,
    old_to_new_index: dict[Path, Path],
    org_name_to_entity: dict[str, str],
) -> None:
    markdown = entity.old_path.read_text(encoding="utf-8")
    metadata, body = split_frontmatter(markdown)
    preamble, sections = split_h2_sections(body)
    section_by_heading = {normalize_heading(section.heading): section for section in sections}

    if entity.kind == "person" and "looking-for" in metadata:
        metadata.pop("looking-for", None)

    metadata = rewrite_links_in_value(
        metadata,
        old_source_path=entity.old_path,
        new_source_dir=entity.new_dir,
        old_to_new_index=old_to_new_index,
    )

    rewritten_body = rewrite_entity_links_in_text(
        body,
        old_source_path=entity.old_path,
        new_source_dir=entity.new_dir,
        old_to_new_index=old_to_new_index,
    )

    trimmed_body = remove_structured_sections(entity.kind, rewritten_body)
    entity.new_dir.mkdir(parents=True, exist_ok=True)

    index_parts: list[str] = []
    index_parts.append(render_frontmatter(metadata).rstrip())
    index_parts.append("")
    index_parts.append(trimmed_body.strip())
    entity.new_index.write_text("\n".join(index_parts).rstrip() + "\n", encoding="utf-8")

    edges_dir = entity.new_dir / "edges"
    edges_dir.mkdir(parents=True, exist_ok=True)
    (edges_dir / ".gitkeep").write_text("", encoding="utf-8")

    copy_entity_images(entity, data_dir=data_new_root.parent / "data")

    source_path_relative = entity.old_path.relative_to(data_new_root.parent).as_posix()

    changelog_rows = extract_changelog_rows(
        section=section_by_heading.get("changelog"),
        provenance_source_path=source_path_relative,
        old_source_path=entity.old_path,
        new_source_dir=entity.new_dir,
        old_to_new_index=old_to_new_index,
    )
    write_jsonl(entity.new_dir / "changelog.jsonl", changelog_rows)

    if entity.kind == "person":
        employment_rows = extract_employment_rows(
            metadata=metadata,
            section=section_by_heading.get("employment history"),
            provenance_source_path=source_path_relative,
            old_source_path=entity.old_path,
            new_source_dir=entity.new_dir,
            old_to_new_index=old_to_new_index,
            org_name_to_entity=org_name_to_entity,
            data_new_root=data_new_root,
        )
        write_jsonl(entity.new_dir / "employment-history.jsonl", employment_rows)

        looking_for_rows = extract_looking_for_rows(
            metadata=split_frontmatter(markdown)[0],
            section=section_by_heading.get("looking for"),
            provenance_source_path=source_path_relative,
            old_source_path=entity.old_path,
            new_source_dir=entity.new_dir,
            old_to_new_index=old_to_new_index,
        )
        write_jsonl(entity.new_dir / "looking-for.jsonl", looking_for_rows)



def write_mapping_file(
    *,
    output_root: Path,
    entities: list[EntitySource],
    project_root: Path,
) -> None:
    mapping_path = output_root / "migration-path-map.csv"
    rows = [
        (
            entity.old_path.relative_to(project_root).as_posix(),
            entity.new_index.relative_to(project_root).as_posix(),
        )
        for entity in entities
    ]
    rows.sort(key=lambda item: item[0])

    with mapping_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["old_path", "new_index_path"])
        writer.writerows(rows)


def run_migration(*, project_root: Path, output_dir: str) -> Path:
    data_dir = project_root / "data"
    output_root = project_root / output_dir

    if output_root.exists():
        shutil.rmtree(output_root)

    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "person").mkdir(parents=True, exist_ok=True)
    (output_root / "org").mkdir(parents=True, exist_ok=True)
    edge_root = output_root / "edge"
    edge_root.mkdir(parents=True, exist_ok=True)
    (edge_root / ".gitkeep").write_text("", encoding="utf-8")

    entities = gather_entities(data_dir=data_dir, output_root=output_root)
    old_to_new_index = {entity.old_path.resolve(): entity.new_index for entity in entities}
    org_name_to_entity = build_org_name_index(entities=entities, data_new_root=output_root)

    for entity in entities:
        migrate_entity(
            entity=entity,
            data_new_root=output_root,
            old_to_new_index=old_to_new_index,
            org_name_to_entity=org_name_to_entity,
        )

    write_mapping_file(output_root=output_root, entities=entities, project_root=project_root)
    return output_root


def main() -> int:
    args = parse_args()
    output = run_migration(
        project_root=args.project_root.resolve(),
        output_dir=args.output_dir,
    )
    print(output.relative_to(args.project_root.resolve()).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
