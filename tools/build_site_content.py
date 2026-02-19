#!/usr/bin/env python3
"""Generate MkDocs pages from KB Markdown source files."""

from __future__ import annotations

import argparse
import html
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
IMAGE_ONLY_RE = re.compile(
    r"^!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)(?:\[\^[^\]]+\])*\s*$"
)
MARKDOWN_IMAGE_SRC_RE = re.compile(r"(!\[[^\]]*\]\()(\./)?images/")
HTML_IMAGE_SRC_RE = re.compile(r"(<img\b[^>]*\ssrc=[\"'])(\./)?images/")
FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]]+)\](?!:)")
FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]]+)\]:")
SLUG_SEPARATOR_RE = re.compile(r"[-_]+")
PERSON_FIELDS: list[tuple[str, str]] = [
    ("firm", "Current Organization"),
    ("role", "Current Role"),
    ("location", "Location"),
    ("relationship-status", "Relationship Status"),
    ("focus-areas", "Focus Areas"),
    ("website", "Website"),
    ("linkedin", "LinkedIn"),
    ("email", "Email"),
    ("updated-at", "Updated At"),
]
ORG_FIELDS: list[tuple[str, str]] = [
    ("website", "Website"),
    ("hq-location", "HQ Location"),
    ("stages", "Stages"),
    ("check-size", "Check Size"),
    ("thesis", "Thesis"),
    ("focus-sectors", "Focus Sectors"),
    ("relationship-status", "Relationship Status"),
    ("updated-at", "Updated At"),
    ("last-updated-from-source", "Last Updated From Source"),
]


@dataclass(frozen=True)
class Page:
    slug: str
    title: str
    entity_type: str
    source_path: Path
    output_path: Path
    metadata: dict[str, Any]
    body: str


@dataclass(frozen=True)
class NotePage:
    title: str
    source_path: Path
    output_path: Path
    relative_path: Path
    metadata: dict[str, Any]
    body: str


def split_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(markdown)
    if not match:
        return {}, markdown
    metadata = yaml.safe_load(match.group(1)) or {}
    body = markdown[match.end() :].lstrip("\n")
    return metadata, body


def load_page(path: Path, entity_type: str, output_path: Path) -> Page:
    markdown = path.read_text(encoding="utf-8")
    metadata, body = split_frontmatter(markdown)
    if entity_type == "person":
        title = str(metadata.get("person") or path.stem.replace("-", " ").title())
    else:
        title = str(metadata.get("org") or path.stem.replace("-", " ").title())
    return Page(
        slug=path.stem,
        title=title,
        entity_type=entity_type,
        source_path=path,
        output_path=output_path,
        metadata=metadata,
        body=body,
    )


def title_from_slug(slug: str) -> str:
    parts = [part for part in SLUG_SEPARATOR_RE.split(slug.strip()) if part]
    if not parts:
        return "Untitled"
    return " ".join(parts).title()


def load_note_page(path: Path, notes_root: Path, output_path: Path) -> NotePage:
    markdown = path.read_text(encoding="utf-8")
    metadata, body = split_frontmatter(markdown)
    title = str(metadata.get("title") or title_from_slug(path.stem))
    return NotePage(
        title=title,
        source_path=path,
        output_path=output_path,
        relative_path=path.relative_to(notes_root),
        metadata=metadata,
        body=body,
    )


def has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def clean_cell(text: str) -> str:
    return text.replace("|", r"\|").replace("\n", "<br>")


def as_text(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, list):
        simple_items = [str(item) for item in value if has_value(item)]
        return ", ".join(simple_items) if simple_items else "-"
    return str(value)


def as_inline_text(value: Any) -> str:
    return as_text(value).replace("\n", " ").strip()


def strip_leading_h1(body: str) -> str:
    lines = body.splitlines()
    if not lines:
        return ""
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index < len(lines) and lines[index].startswith("# "):
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
    remaining = "\n".join(lines[index:]).strip()
    return f"{remaining}\n" if remaining else ""


def render_reference_field_items(
    metadata: dict[str, Any], fields: list[tuple[str, str]]
) -> list[str]:
    rows: list[str] = []
    for key, label in fields:
        value = metadata.get(key)
        if not has_value(value):
            continue
        rows.append(f"    - **{label}:** {as_inline_text(value)}")
    return rows


def format_date_span(start_date: Any, end_date: Any) -> str:
    if not has_value(start_date) and not has_value(end_date):
        return "-"
    if not has_value(start_date):
        return f"until {end_date}"
    if not has_value(end_date):
        return f"{start_date} onward"
    return f"{start_date} to {end_date}"


def normalize_heading(heading: str) -> str:
    return " ".join(heading.lower().split())


def split_h2_sections(body: str) -> tuple[str, list[tuple[str, str]]]:
    lines = body.splitlines()
    preamble_lines: list[str] = []
    sections: list[tuple[str, str]] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in lines:
        if line.startswith("## "):
            if current_heading is None:
                preamble_lines = current_lines
            else:
                sections.append((current_heading, "\n".join(current_lines).rstrip()))
            current_heading = line[3:].strip()
            current_lines = [line]
            continue
        current_lines.append(line)

    if current_heading is None:
        return body.strip(), []

    sections.append((current_heading, "\n".join(current_lines).rstrip()))
    return "\n".join(preamble_lines).strip(), sections


def join_h2_sections(preamble: str, sections: list[tuple[str, str]]) -> str:
    blocks: list[str] = []
    if preamble.strip():
        blocks.append(preamble.strip())
    for _, block in sections:
        if block.strip():
            blocks.append(block.strip())
    return "\n\n".join(blocks).strip()


def find_section_index(sections: list[tuple[str, str]], heading: str) -> int | None:
    target = normalize_heading(heading)
    for index, (title, _) in enumerate(sections):
        if normalize_heading(title) == target:
            return index
    return None


def move_looking_for_after_snapshot(body: str) -> str:
    preamble, sections = split_h2_sections(body)
    if not sections:
        return body

    snapshot_index = find_section_index(sections, "snapshot")
    looking_for_index = find_section_index(sections, "looking for")
    if snapshot_index is None or looking_for_index is None:
        return body
    if looking_for_index == snapshot_index + 1:
        return body

    looking_for_section = sections.pop(looking_for_index)
    snapshot_index = find_section_index(sections, "snapshot")
    if snapshot_index is None:
        sections.insert(0, looking_for_section)
    else:
        sections.insert(snapshot_index + 1, looking_for_section)

    return join_h2_sections(preamble, sections)


def list_from_metadata(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if has_value(item)]
    if has_value(value):
        return [str(value).strip()]
    return []


def format_website_link(url: str) -> str:
    parsed = urlparse(url)
    label = parsed.netloc if parsed.netloc else parsed.path
    label = label.removeprefix("www.") if label else url
    return f"[{label}]({url})"


def render_person_quick_links(metadata: dict[str, Any]) -> list[str]:
    links: list[str] = []

    for email in list_from_metadata(metadata.get("email")):
        links.append(f"[{email}](mailto:{email})")

    linkedin = as_inline_text(metadata.get("linkedin"))
    if linkedin and linkedin != "-":
        links.append(f"[LinkedIn]({linkedin})")

    website = as_inline_text(metadata.get("website"))
    if website and website != "-":
        links.append(f"[Website]({website})")

    if not links:
        return []
    return [f"**Quick links:** {' | '.join(links)}", ""]


def render_org_website_link(metadata: dict[str, Any]) -> list[str]:
    website = as_inline_text(metadata.get("website"))
    if not website or website == "-":
        return []
    return [f"**Website:** {format_website_link(website)}", ""]


def render_affiliated_people_section(metadata: dict[str, Any]) -> str:
    entries = metadata.get("known-people")
    if not isinstance(entries, list) or not entries:
        return ""

    lines = ["## Affiliated People", ""]
    for item in entries:
        if not isinstance(item, dict):
            continue
        person = as_inline_text(item.get("person"))
        if not person or person == "-":
            continue
        relationship = as_inline_text(item.get("relationship"))
        details = as_inline_text(item.get("relationship-details"))
        dates = format_date_span(
            item.get("relationship-start-date"),
            item.get("relationship-end-date"),
        )
        facets = [value for value in [relationship, dates] if value and value != "-"]
        suffix = f" ({', '.join(facets)})" if facets else ""
        if details and details != "-":
            lines.append(f"- {person}{suffix}: {details}")
        else:
            lines.append(f"- {person}{suffix}")

    if len(lines) == 2:
        return ""
    lines.append("")
    return "\n".join(lines)


def insert_affiliated_people_above_bio(body: str, metadata: dict[str, Any]) -> str:
    affiliated_section = render_affiliated_people_section(metadata)
    if not affiliated_section:
        return body

    preamble, sections = split_h2_sections(body)
    if not sections:
        return f"{affiliated_section.strip()}\n\n{body.strip()}".strip()

    if find_section_index(sections, "affiliated people") is not None:
        return body

    insert_index = find_section_index(sections, "bio")
    if insert_index is None:
        snapshot_index = find_section_index(sections, "snapshot")
        insert_index = snapshot_index + 1 if snapshot_index is not None else 0

    sections.insert(insert_index, ("Affiliated People", affiliated_section.strip()))
    return join_h2_sections(preamble, sections)


def render_reference_section(page: Page) -> list[str]:
    fields = PERSON_FIELDS if page.entity_type == "person" else ORG_FIELDS
    items = render_reference_field_items(page.metadata, fields)
    if not items:
        return []
    return [
        "## Reference Data",
        "",
        '??? info "Show metadata"',
        *items,
        "",
    ]


def inject_bio_photo_wrap(body: str) -> str:
    lines = body.splitlines()
    bio_line_index: int | None = None
    for index, line in enumerate(lines):
        if line.strip().lower() == "## bio":
            bio_line_index = index
            break
    if bio_line_index is None:
        return body

    candidate_index = bio_line_index + 1
    while candidate_index < len(lines) and not lines[candidate_index].strip():
        candidate_index += 1
    if candidate_index >= len(lines):
        return body

    match = IMAGE_ONLY_RE.match(lines[candidate_index].strip())
    if not match:
        return body

    alt = html.escape(match.group("alt"), quote=True)
    src = html.escape(match.group("src"), quote=True)
    lines[candidate_index] = f'<img src="{src}" alt="{alt}" class="kb-bio-photo" />'
    return "\n".join(lines)


def normalize_image_paths(body: str) -> str:
    """Adjust image paths for MkDocs directory-style URLs."""
    body = MARKDOWN_IMAGE_SRC_RE.sub(r"\1../images/", body)
    body = HTML_IMAGE_SRC_RE.sub(r"\1../images/", body)
    return body


def remove_unreferenced_footnote_definitions(body: str) -> str:
    referenced = {match.group(1) for match in FOOTNOTE_REF_RE.finditer(body)}
    filtered_lines: list[str] = []
    for line in body.splitlines():
        match = FOOTNOTE_DEF_RE.match(line)
        if match and match.group(1) not in referenced:
            continue
        filtered_lines.append(line)
    return "\n".join(filtered_lines)


def render_page(page: Page) -> str:
    sections: list[str] = [f"# {page.title}", ""]

    if page.entity_type == "person":
        sections.extend(render_person_quick_links(page.metadata))
    else:
        sections.extend(render_org_website_link(page.metadata))

    body = strip_leading_h1(page.body)
    body = inject_bio_photo_wrap(body)
    body = normalize_image_paths(body)
    if page.entity_type == "person":
        body = move_looking_for_after_snapshot(body)
    else:
        body = insert_affiliated_people_above_bio(body, page.metadata)
    body = remove_unreferenced_footnote_definitions(body)
    if body:
        sections.append(body.rstrip())
        sections.append("")
    sections.extend(render_reference_section(page))
    return "\n".join(sections).strip() + "\n"


def render_note_page(page: NotePage) -> str:
    sections: list[str] = [f"# {page.title}", ""]
    body = strip_leading_h1(page.body)
    body = normalize_image_paths(body)
    body = remove_unreferenced_footnote_definitions(body)
    if body:
        sections.append(body.rstrip())
        sections.append("")
    return "\n".join(sections).strip() + "\n"


def collect_pages(data_dir: Path, docs_dir: Path, entity_type: str) -> list[Page]:
    source_dir = data_dir / entity_type
    output_dir = docs_dir / entity_type
    output_dir.mkdir(parents=True, exist_ok=True)
    pages: list[Page] = []
    for source_path in sorted(source_dir.glob("*.md")):
        if source_path.stem.startswith("_"):
            continue
        page = load_page(
            path=source_path,
            entity_type=entity_type,
            output_path=output_dir / source_path.name,
        )
        pages.append(page)
    return pages


def collect_note_pages(data_dir: Path, docs_dir: Path) -> list[NotePage]:
    notes_dir = data_dir / "notes"
    output_dir = docs_dir / "notes"
    if not notes_dir.exists():
        return []

    pages: list[NotePage] = []
    for source_path in sorted(notes_dir.rglob("*.md")):
        relative_path = source_path.relative_to(notes_dir)
        if any(part.startswith("_") for part in relative_path.parts):
            continue
        page = load_note_page(
            path=source_path,
            notes_root=notes_dir,
            output_path=output_dir / relative_path,
        )
        page.output_path.parent.mkdir(parents=True, exist_ok=True)
        pages.append(page)
    return pages


def copy_non_markdown_assets(data_dir: Path, docs_dir: Path) -> None:
    for path in sorted(data_dir.rglob("*")):
        if (
            not path.is_file()
            or path.suffix.lower() == ".md"
            or path.name.startswith(".")
        ):
            continue
        destination = docs_dir / path.relative_to(data_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def copy_site_assets(project_root: Path, docs_dir: Path) -> None:
    assets_dir = project_root / "site_assets"
    if not assets_dir.exists():
        return
    for path in sorted(assets_dir.rglob("*")):
        if not path.is_file():
            continue
        destination = docs_dir / path.relative_to(assets_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def render_people_index(people: list[Page]) -> str:
    lines = [
        "# People",
        "",
        f"Generated from `{len(people)}` source files in `data/person/`.",
        "",
        "| Person | Current Role | Organization | Relationship | Updated |",
        "| --- | --- | --- | --- | --- |",
    ]
    for page in people:
        metadata = page.metadata
        lines.append(
            "| {person} | {role} | {org} | {status} | {updated} |".format(
                person=f"[{clean_cell(page.title)}](person/{page.slug}.md)",
                role=clean_cell(as_text(metadata.get("role"))),
                org=clean_cell(as_text(metadata.get("firm"))),
                status=clean_cell(as_text(metadata.get("relationship-status"))),
                updated=clean_cell(as_text(metadata.get("updated-at"))),
            )
        )
    lines.extend(["", "Use page search to find details across the full profile text.", ""])
    return "\n".join(lines)


def render_orgs_index(orgs: list[Page]) -> str:
    lines = [
        "# Organizations",
        "",
        f"Generated from `{len(orgs)}` source files in `data/org/`.",
        "",
        "| Organization | Thesis | Relationship | Updated |",
        "| --- | --- | --- | --- |",
    ]
    for page in orgs:
        metadata = page.metadata
        lines.append(
            "| {org} | {thesis} | {status} | {updated} |".format(
                org=f"[{clean_cell(page.title)}](org/{page.slug}.md)",
                thesis=clean_cell(as_text(metadata.get("thesis"))),
                status=clean_cell(as_text(metadata.get("relationship-status"))),
                updated=clean_cell(as_text(metadata.get("updated-at"))),
            )
        )
    lines.append("")
    return "\n".join(lines)


def format_note_category(parts: tuple[str, ...]) -> str:
    if not parts:
        return "Uncategorized"
    return " / ".join(title_from_slug(part) for part in parts)


def render_notes_index(notes: list[NotePage]) -> str:
    lines = [
        "# Notes",
        "",
        f"Generated from `{len(notes)}` source files in `data/notes/`.",
        "",
    ]
    if not notes:
        lines.extend(["No notes found.", ""])
        return "\n".join(lines)

    grouped: dict[tuple[str, ...], list[NotePage]] = defaultdict(list)
    for page in notes:
        category = tuple(page.relative_path.parts[:-1])
        grouped[category].append(page)

    for category in sorted(grouped):
        pages = sorted(grouped[category], key=lambda page: page.relative_path.as_posix())
        lines.extend([f"## {format_note_category(category)}", ""])
        for page in pages:
            relative_path = page.relative_path.as_posix()
            lines.append(f"- [{page.title}](notes/{relative_path}) (`data/notes/{relative_path}`)")
        lines.append("")

    return "\n".join(lines)


def render_home(people: list[Page], orgs: list[Page], notes: list[NotePage]) -> str:
    return "\n".join(
        [
            "# Victor's Knowledge Base",
            "",
            "This site is generated from raw Markdown files in `data/person/`, `data/org/`, and `data/notes/`.",
            "",
            f"- People profiles: **{len(people)}**",
            f"- Organization profiles: **{len(orgs)}**",
            f"- Notes: **{len(notes)}**",
            "",
            "## Collections",
            "",
            "- [People](people.md)",
            "- [Organizations](orgs.md)",
            "- [Notes](notes.md)",
            "",
            "## Notes",
            "",
            "- Pages are generated as view-only output from source files.",
            "- Frontmatter is rendered in a compact, collapsible reference section per page.",
            "- Edit `tools/build_site_content.py` to change how source data is rendered.",
            "",
        ]
    )


def build_site_content(project_root: Path) -> None:
    data_dir = project_root / "data"
    docs_dir = project_root / "site_docs"
    if docs_dir.exists():
        shutil.rmtree(docs_dir)
    docs_dir.mkdir(parents=True, exist_ok=True)

    people = collect_pages(data_dir=data_dir, docs_dir=docs_dir, entity_type="person")
    orgs = collect_pages(data_dir=data_dir, docs_dir=docs_dir, entity_type="org")
    notes = collect_note_pages(data_dir=data_dir, docs_dir=docs_dir)

    for page in [*people, *orgs]:
        page.output_path.write_text(render_page(page), encoding="utf-8")
    for page in notes:
        page.output_path.write_text(render_note_page(page), encoding="utf-8")

    copy_non_markdown_assets(data_dir=data_dir, docs_dir=docs_dir)
    copy_site_assets(project_root=project_root, docs_dir=docs_dir)

    (docs_dir / "index.md").write_text(
        render_home(people=people, orgs=orgs, notes=notes), encoding="utf-8"
    )
    (docs_dir / "people.md").write_text(render_people_index(people), encoding="utf-8")
    (docs_dir / "orgs.md").write_text(render_orgs_index(orgs), encoding="utf-8")
    (docs_dir / "notes.md").write_text(render_notes_index(notes), encoding="utf-8")
    (docs_dir / ".gitkeep").write_text("", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        default=Path(__file__).resolve().parents[1],
        type=Path,
        help="Repository root containing data/ and mkdocs.yml.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_site_content(project_root=args.project_root.resolve())


if __name__ == "__main__":
    main()
