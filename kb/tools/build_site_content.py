#!/usr/bin/env python3
"""Generate MkDocs pages from KB source files."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import ValidationError

from kb.schemas import ChangelogRow, EdgeRecord, EmploymentHistoryRow, LookingForRow

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
IMAGE_ONLY_RE = re.compile(
    r"^!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)(?:\[\^[^\]]+\])*\s*$"
)
MARKDOWN_IMAGE_SRC_RE = re.compile(r"(!\[[^\]]*\]\()(\./)?images/")
HTML_IMAGE_SRC_RE = re.compile(r"(<img\b[^>]*\ssrc=[\"'])(\./)?images/")
FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]]+)\](?!:)")
FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]]+)\]:")
SLUG_SEPARATOR_RE = re.compile(r"[-_]+")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")

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
    entity_rel_path: str
    index_path: Path
    source_dir: Path
    output_path: Path
    metadata: dict[str, Any]
    body: str
    employment_rows: list[EmploymentHistoryRow]
    looking_for_rows: list[LookingForRow]
    changelog_rows: list[ChangelogRow]


@dataclass(frozen=True)
class SourcePage:
    title: str
    source_path: Path
    output_path: Path
    relative_path: Path
    citation_key: str
    metadata: dict[str, Any]
    body: str


def split_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(markdown)
    if not match:
        return {}, markdown
    metadata = yaml.safe_load(match.group(1)) or {}
    body = markdown[match.end() :].lstrip("\n")
    return metadata, body


def infer_entity_data_root(project_root: Path) -> Path:
    return project_root / "data"


def infer_sources_root(project_root: Path, entity_data_root: Path) -> Path:
    for candidate in (
        entity_data_root / "source",
        entity_data_root / "note",
        project_root / "data" / "source",
    ):
        if candidate.exists():
            return candidate
    return project_root / "data" / "source"


def load_jsonl_rows(path: Path, model_cls: type) -> list[Any]:
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


def load_entity_page(index_path: Path, entity_type: str, output_path: Path, data_root: Path) -> Page:
    entity_dir = index_path.parent
    prefix = f"{entity_type}@"
    if not entity_dir.name.startswith(prefix):
        raise ValueError(f"Unexpected entity directory name: {entity_dir.as_posix()}")

    slug = entity_dir.name[len(prefix) :]
    markdown = index_path.read_text(encoding="utf-8")
    metadata, body = split_frontmatter(markdown)

    if entity_type == "person":
        title = str(metadata.get("person") or slug.replace("-", " ").title())
        employment_rows = load_jsonl_rows(entity_dir / "employment-history.jsonl", EmploymentHistoryRow)
        looking_for_rows = load_jsonl_rows(entity_dir / "looking-for.jsonl", LookingForRow)
    else:
        title = str(metadata.get("org") or slug.replace("-", " ").title())
        employment_rows = []
        looking_for_rows = []

    changelog_rows = load_jsonl_rows(entity_dir / "changelog.jsonl", ChangelogRow)

    entity_rel_path = entity_dir.relative_to(data_root).as_posix()
    return Page(
        slug=slug,
        title=title,
        entity_type=entity_type,
        entity_rel_path=entity_rel_path,
        index_path=index_path,
        source_dir=entity_dir,
        output_path=output_path,
        metadata=metadata,
        body=body,
        employment_rows=employment_rows,
        looking_for_rows=looking_for_rows,
        changelog_rows=changelog_rows,
    )


def title_from_slug(slug: str) -> str:
    parts = [part for part in SLUG_SEPARATOR_RE.split(slug.strip()) if part]
    if not parts:
        return "Untitled"
    return " ".join(parts).title()


def load_source_page(path: Path, sources_root: Path, output_path: Path) -> SourcePage:
    markdown = path.read_text(encoding="utf-8")
    metadata, body = split_frontmatter(markdown)
    title = str(metadata.get("title") or title_from_slug(path.stem))
    citation_key = str(metadata.get("citation-key") or path.stem).strip()
    return SourcePage(
        title=title,
        source_path=path,
        output_path=output_path,
        relative_path=path.relative_to(sources_root),
        citation_key=citation_key,
        metadata=metadata,
        body=body,
    )


def load_source_page_v2(index_path: Path, output_root: Path) -> SourcePage:
    markdown = index_path.read_text(encoding="utf-8")
    metadata, body = split_frontmatter(markdown)

    raw_id = str(metadata.get("id") or "").strip()
    fallback = index_path.parent.name
    if raw_id.startswith("source@"):
        slug = raw_id[len("source@") :]
    elif fallback.startswith("source@"):
        slug = fallback[len("source@") :]
    elif raw_id.startswith("note@"):
        slug = raw_id[len("note@") :]
    elif fallback.startswith("note@"):
        slug = fallback[len("note@") :]
    else:
        slug = fallback

    source_category = str(metadata.get("source-category") or "").strip().strip("/")
    if source_category:
        relative_path = Path(source_category) / f"{slug}.md"
    else:
        relative_path = Path(f"{slug}.md")

    title = str(metadata.get("title") or title_from_slug(slug))
    citation_key = str(metadata.get("citation-key") or slug).strip()
    return SourcePage(
        title=title,
        source_path=index_path,
        output_path=output_root / relative_path,
        relative_path=relative_path,
        citation_key=citation_key,
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
    normalized = normalize_external_url(url)
    parsed = urlparse(normalized)
    label = parsed.netloc if parsed.netloc else parsed.path
    label = label.removeprefix("www.") if label else normalized
    return f"[{label}]({normalized})"


def normalize_external_url(value: str) -> str:
    text = value.strip()
    if not text:
        return text
    parsed = urlparse(text)
    if parsed.scheme:
        return text
    if text.startswith("//"):
        return f"https:{text}"
    host_candidate = text.split("/", 1)[0]
    if "." in host_candidate:
        return f"https://{text}"
    return text


def render_person_quick_links(metadata: dict[str, Any]) -> list[str]:
    links: list[str] = []

    for email in list_from_metadata(metadata.get("email")):
        links.append(f"[{email}](mailto:{email})")

    linkedin = normalize_external_url(as_inline_text(metadata.get("linkedin")))
    if linkedin and linkedin != "-":
        links.append(f"[LinkedIn]({linkedin})")

    website = normalize_external_url(as_inline_text(metadata.get("website")))
    if website and website != "-":
        links.append(f"[Website]({website})")

    if not links:
        return []
    return [f"**Quick links:** {' | '.join(links)}", ""]


def render_org_website_link(metadata: dict[str, Any]) -> list[str]:
    website = normalize_external_url(as_inline_text(metadata.get("website")))
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


def render_reference_section(page: Page, metadata: dict[str, Any]) -> list[str]:
    fields = PERSON_FIELDS if page.entity_type == "person" else ORG_FIELDS
    items = render_reference_field_items(metadata, fields)
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
    body = MARKDOWN_IMAGE_SRC_RE.sub(r"\1../images/", body)
    body = HTML_IMAGE_SRC_RE.sub(r"\1../images/", body)
    return body


def remove_footnote_definitions(body: str) -> str:
    filtered_lines: list[str] = []
    for line in body.splitlines():
        if FOOTNOTE_DEF_RE.match(line):
            continue
        filtered_lines.append(line)
    return "\n".join(filtered_lines)


def append_source_footnote_definitions(
    markdown: str,
    *,
    output_path: Path,
    sources_by_citation_key: dict[str, "SourcePage"],
) -> str:
    ordered_keys: list[str] = []
    seen: set[str] = set()
    for match in FOOTNOTE_REF_RE.finditer(markdown):
        key = match.group(1).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered_keys.append(key)

    if not ordered_keys:
        return markdown.rstrip() + "\n"

    definitions: list[str] = []
    for key in ordered_keys:
        source_page = sources_by_citation_key.get(key)
        if source_page is None:
            definitions.append(f"[^{key}]: Missing source for citation key `{key}`.")
            continue

        rel = os.path.relpath(source_page.output_path, start=output_path.parent).replace(os.sep, "/")
        definitions.append(f"[^{key}]: [{source_page.title}]({rel})")

    body = markdown.rstrip()
    if definitions:
        body = f"{body}\n\n" + "\n".join(definitions)
    return body.rstrip() + "\n"


def is_external_destination(destination: str) -> bool:
    lowered = destination.lower()
    return lowered.startswith(("http://", "https://", "mailto:", "#"))


def split_destination(destination: str) -> tuple[str, str]:
    if "#" not in destination:
        return destination, ""
    path, fragment = destination.split("#", 1)
    return path, f"#{fragment}"


def resolve_destination(source_path: Path, destination: str) -> Path | None:
    if not destination or is_external_destination(destination):
        return None
    path_part, _ = split_destination(destination)
    if not path_part:
        return None
    return (source_path.parent / path_part).resolve()


def rewrite_entity_links_in_text(
    text: str,
    *,
    source_path: Path,
    output_path: Path,
    source_to_output: dict[Path, Path],
) -> str:
    def replace(match: re.Match[str]) -> str:
        label = match.group(1)
        destination = match.group(2)
        resolved = resolve_destination(source_path, destination)
        if resolved is None:
            return match.group(0)

        target = source_to_output.get(resolved)
        if target is None:
            return match.group(0)

        _, fragment = split_destination(destination)
        rel = os.path.relpath(target, start=output_path.parent).replace(os.sep, "/")
        return f"[{label}]({rel}{fragment})"

    return MARKDOWN_LINK_RE.sub(replace, text)


def rewrite_links_in_value(
    value: Any,
    *,
    source_path: Path,
    output_path: Path,
    source_to_output: dict[Path, Path],
) -> Any:
    if isinstance(value, str):
        return rewrite_entity_links_in_text(
            value,
            source_path=source_path,
            output_path=output_path,
            source_to_output=source_to_output,
        )
    if isinstance(value, list):
        return [
            rewrite_links_in_value(
                item,
                source_path=source_path,
                output_path=output_path,
                source_to_output=source_to_output,
            )
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: rewrite_links_in_value(
                item,
                source_path=source_path,
                output_path=output_path,
                source_to_output=source_to_output,
            )
            for key, item in value.items()
        }
    return value


def render_employment_history_section(
    page: Page,
    pages_by_entity_rel_path: dict[str, Page],
    source_to_output: dict[Path, Path],
) -> list[str]:
    if page.entity_type != "person" or not page.employment_rows:
        return []

    lines = [
        "## Employment History",
        "",
        "| Period | Organization | Role | Notes | Source |",
        "| --- | --- | --- | --- | --- |",
    ]

    for row in page.employment_rows:
        org_text = row.organization
        if row.organization_ref:
            linked_page = pages_by_entity_rel_path.get(row.organization_ref)
            if linked_page is not None:
                rel = os.path.relpath(linked_page.output_path, start=page.output_path.parent).replace(
                    os.sep, "/"
                )
                org_text = f"[{org_text}]({rel})"

        notes_value = row.notes or "-"
        if notes_value != "-":
            notes_value = rewrite_entity_links_in_text(
                notes_value,
                source_path=page.index_path,
                output_path=page.output_path,
                source_to_output=source_to_output,
            )

        source_value = row.source or "-"
        if source_value != "-":
            source_value = rewrite_entity_links_in_text(
                source_value,
                source_path=page.index_path,
                output_path=page.output_path,
                source_to_output=source_to_output,
            )

        lines.append(
            "| {period} | {org} | {role} | {notes} | {source} |".format(
                period=clean_cell(row.period),
                org=clean_cell(org_text),
                role=clean_cell(row.role),
                notes=clean_cell(notes_value),
                source=clean_cell(source_value),
            )
        )

    lines.append("")
    return lines


def render_looking_for_section(
    page: Page,
    source_to_output: dict[Path, Path],
) -> list[str]:
    if page.entity_type != "person" or not page.looking_for_rows:
        return []

    lines = [
        "## Looking For",
        "",
        "| Ask | Details | Status | First Asked | Last Checked | Notes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    for row in page.looking_for_rows:
        ask = rewrite_entity_links_in_text(
            row.ask,
            source_path=page.index_path,
            output_path=page.output_path,
            source_to_output=source_to_output,
        )

        details = row.details or "-"
        if details != "-":
            details = rewrite_entity_links_in_text(
                details,
                source_path=page.index_path,
                output_path=page.output_path,
                source_to_output=source_to_output,
            )

        notes = row.notes or "-"
        if notes != "-":
            notes = rewrite_entity_links_in_text(
                notes,
                source_path=page.index_path,
                output_path=page.output_path,
                source_to_output=source_to_output,
            )

        lines.append(
            "| {ask} | {details} | {status} | {first_asked_at} | {last_checked_at} | {notes} |".format(
                ask=clean_cell(ask),
                details=clean_cell(details),
                status=clean_cell(row.status.value),
                first_asked_at=clean_cell(row.first_asked_at or "-"),
                last_checked_at=clean_cell(row.last_checked_at or "-"),
                notes=clean_cell(notes),
            )
        )

    lines.append("")
    return lines


def render_changelog_section(page: Page, source_to_output: dict[Path, Path]) -> list[str]:
    if not page.changelog_rows:
        return []

    lines = ["## Changelog", ""]
    for row in page.changelog_rows:
        summary = rewrite_entity_links_in_text(
            row.summary,
            source_path=page.index_path,
            output_path=page.output_path,
            source_to_output=source_to_output,
        )
        lines.append(f"- [{row.changed_at}] {summary}")
    lines.append("")
    return lines


def relation_display(value: str) -> str:
    return value.replace("_", " ")


def load_entity_edges(page: Page) -> list[EdgeRecord]:
    edges_dir = page.source_dir / "edges"
    if not edges_dir.exists() or not edges_dir.is_dir():
        return []

    by_id: dict[str, EdgeRecord] = {}
    for link_path in sorted(edges_dir.glob("edge@*.json"), key=lambda path: path.as_posix()):
        target = link_path.resolve() if link_path.is_symlink() else link_path
        if not target.exists():
            continue
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            continue
        record = EdgeRecord.model_validate(payload)
        by_id[record.id] = record

    return [by_id[key] for key in sorted(by_id)]


def render_relations_section(
    page: Page,
    relation_targets: dict[str, tuple[str, Path]],
) -> list[str]:
    records = load_entity_edges(page)
    if not records:
        return []

    lines = ["## Relations", ""]
    for record in records:
        if record.from_entity == page.entity_rel_path:
            other = record.to_entity
            arrow = "->"
        elif record.to_entity == page.entity_rel_path:
            other = record.from_entity
            arrow = "<-"
        else:
            continue

        other_target = relation_targets.get(other)
        if other_target is not None:
            other_title, other_output_path = other_target
            rel = os.path.relpath(other_output_path, start=page.output_path.parent).replace(os.sep, "/")
            other_label = f"[{other_title}]({rel})"
        else:
            other_label = f"`{other}`"

        lines.append(
            f"- {relation_display(record.relation.value)} {arrow} {other_label} "
            f"(first noted: {record.first_noted_at}, last verified: {record.last_verified_at}, edge: `{record.id}`)"
        )

    if len(lines) == 2:
        return []

    lines.append("")
    return lines


def render_page(
    page: Page,
    *,
    source_to_output: dict[Path, Path],
    pages_by_entity_rel_path: dict[str, Page],
    relation_targets: dict[str, tuple[str, Path]],
    sources_by_citation_key: dict[str, "SourcePage"],
) -> str:
    rewritten_metadata = rewrite_links_in_value(
        page.metadata,
        source_path=page.index_path,
        output_path=page.output_path,
        source_to_output=source_to_output,
    )

    sections: list[str] = [f"# {page.title}", ""]

    if page.entity_type == "person":
        sections.extend(render_person_quick_links(rewritten_metadata))
    else:
        sections.extend(render_org_website_link(rewritten_metadata))

    body = strip_leading_h1(page.body)
    body = rewrite_entity_links_in_text(
        body,
        source_path=page.index_path,
        output_path=page.output_path,
        source_to_output=source_to_output,
    )
    body = inject_bio_photo_wrap(body)
    body = normalize_image_paths(body)
    if page.entity_type == "person":
        body = move_looking_for_after_snapshot(body)
    else:
        body = insert_affiliated_people_above_bio(body, rewritten_metadata)
    body = remove_footnote_definitions(body)
    if body:
        sections.append(body.rstrip())
        sections.append("")

    sections.extend(
        render_employment_history_section(
            page,
            pages_by_entity_rel_path=pages_by_entity_rel_path,
            source_to_output=source_to_output,
        )
    )
    sections.extend(render_looking_for_section(page, source_to_output=source_to_output))
    sections.extend(render_changelog_section(page, source_to_output=source_to_output))
    sections.extend(render_relations_section(page, relation_targets=relation_targets))
    sections.extend(render_reference_section(page, metadata=rewritten_metadata))

    rendered = "\n".join(sections).strip() + "\n"
    return append_source_footnote_definitions(
        rendered,
        output_path=page.output_path,
        sources_by_citation_key=sources_by_citation_key,
    )


def render_source_page(
    source_page: SourcePage,
    source_to_output: dict[Path, Path],
    *,
    sources_by_citation_key: dict[str, "SourcePage"],
) -> str:
    sections: list[str] = [f"# {source_page.title}", ""]
    body = strip_leading_h1(source_page.body)
    body = rewrite_entity_links_in_text(
        body,
        source_path=source_page.source_path,
        output_path=source_page.output_path,
        source_to_output=source_to_output,
    )
    body = normalize_image_paths(body)
    body = remove_footnote_definitions(body)
    if body:
        sections.append(body.rstrip())
        sections.append("")
    rendered = "\n".join(sections).strip() + "\n"
    return append_source_footnote_definitions(
        rendered,
        output_path=source_page.output_path,
        sources_by_citation_key=sources_by_citation_key,
    )


def collect_entity_pages(data_root: Path, docs_dir: Path, entity_type: str) -> list[Page]:
    source_dir = data_root / entity_type
    output_dir = docs_dir / entity_type
    output_dir.mkdir(parents=True, exist_ok=True)

    pages: list[Page] = []
    seen_slugs: set[str] = set()

    for index_path in sorted(source_dir.rglob("index.md")):
        entity_dir = index_path.parent
        prefix = f"{entity_type}@"
        if not entity_dir.name.startswith(prefix):
            continue

        slug = entity_dir.name[len(prefix) :]
        if slug in seen_slugs:
            raise ValueError(f"Duplicate {entity_type} slug in data root: {slug}")
        seen_slugs.add(slug)

        page = load_entity_page(
            index_path=index_path,
            entity_type=entity_type,
            output_path=output_dir / f"{slug}.md",
            data_root=data_root,
        )
        pages.append(page)

    return pages


def collect_source_pages(sources_root: Path, docs_dir: Path) -> list[SourcePage]:
    output_dir = docs_dir / "sources"
    if not sources_root.exists():
        return []

    if sources_root.name in {"source", "note"}:
        pages: list[SourcePage] = []
        for index_path in sorted(sources_root.rglob("index.md")):
            parent_name = index_path.parent.name
            if not (parent_name.startswith("source@") or parent_name.startswith("note@")):
                continue
            page = load_source_page_v2(
                index_path=index_path,
                output_root=output_dir,
            )
            page.output_path.parent.mkdir(parents=True, exist_ok=True)
            pages.append(page)
        return pages

    pages: list[SourcePage] = []
    for source_path in sorted(sources_root.rglob("*.md")):
        relative_path = source_path.relative_to(sources_root)
        if any(part.startswith("_") for part in relative_path.parts):
            continue
        page = load_source_page(
            path=source_path,
            sources_root=sources_root,
            output_path=output_dir / relative_path,
        )
        page.output_path.parent.mkdir(parents=True, exist_ok=True)
        pages.append(page)
    return pages


def copy_entity_images(pages: list[Page], docs_dir: Path) -> None:
    for page in pages:
        images_dir = page.source_dir / "images"
        if not images_dir.exists() or not images_dir.is_dir():
            continue

        destination_dir = docs_dir / page.entity_type / "images"
        destination_dir.mkdir(parents=True, exist_ok=True)

        for source_path in sorted(images_dir.iterdir(), key=lambda path: path.as_posix()):
            if not source_path.is_file() or source_path.name.startswith("."):
                continue

            destination = destination_dir / source_path.name
            if destination.exists():
                if destination.read_bytes() == source_path.read_bytes():
                    continue
                raise ValueError(f"Image filename collision with different content: {destination.as_posix()}")
            shutil.copy2(source_path, destination)


def copy_source_assets(sources_root: Path, docs_dir: Path) -> None:
    if not sources_root.exists():
        return

    destination_root = docs_dir / "sources"
    for path in sorted(sources_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() == ".md" or path.name.startswith("."):
            continue
        rel_parts = path.relative_to(sources_root).parts
        if "edges" in rel_parts:
            continue
        destination = destination_root / path.relative_to(sources_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def copy_site_assets(project_root: Path, docs_dir: Path) -> None:
    assets_dir = project_root / "kb" / "tools" / "site_assets"
    if not assets_dir.exists():
        return
    for path in sorted(assets_dir.rglob("*")):
        if not path.is_file():
            continue
        destination = docs_dir / path.relative_to(assets_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def render_people_index(people: list[Page], source_label: str) -> str:
    lines = [
        "# People",
        "",
        f"Generated from `{len(people)}` entity pages in `{source_label}/person/`.",
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


def render_orgs_index(orgs: list[Page], source_label: str) -> str:
    lines = [
        "# Organizations",
        "",
        f"Generated from `{len(orgs)}` entity pages in `{source_label}/org/`.",
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


def render_sources_index(sources: list[SourcePage], source_label: str) -> str:
    lines = [
        "# Sources",
        "",
        f"Generated from `{len(sources)}` source files in `{source_label}`.",
        "",
    ]
    if not sources:
        lines.extend(["No sources found.", ""])
        return "\n".join(lines)

    grouped: dict[tuple[str, ...], list[SourcePage]] = defaultdict(list)
    for page in sources:
        category = tuple(page.relative_path.parts[:-1])
        grouped[category].append(page)

    for category in sorted(grouped):
        pages = sorted(grouped[category], key=lambda page: page.relative_path.as_posix())
        lines.extend([f"## {format_note_category(category)}", ""])
        for page in pages:
            relative_path = page.relative_path.as_posix()
            lines.append(f"- [{page.title}](sources/{relative_path}) (`{source_label}/{relative_path}`)")
        lines.append("")

    return "\n".join(lines)


def render_home(
    people: list[Page],
    orgs: list[Page],
    sources: list[SourcePage],
    *,
    entity_source_label: str,
    sources_source_label: str,
) -> str:
    return "\n".join(
        [
            "# Victor's Knowledge Base",
            "",
            f"This site is generated from `{entity_source_label}/person/`, `{entity_source_label}/org/`, and `{sources_source_label}`.",
            "",
            f"- People profiles: **{len(people)}**",
            f"- Organization profiles: **{len(orgs)}**",
            f"- Sources: **{len(sources)}**",
            "",
            "## Collections",
            "",
            "- [People](people.md)",
            "- [Organizations](orgs.md)",
            "- [Sources](sources.md)",
            "",
            "## Rendering Notes",
            "",
            "- Pages are generated as view-only output from source files.",
            "- Structured sections are rendered from JSONL for entity pages.",
            "- Frontmatter is rendered in a compact, collapsible reference section per page.",
            "- Edit `kb/tools/build_site_content.py` to change how source data is rendered.",
            "",
        ]
    )


def build_source_to_output_map(
    project_root: Path,
    pages: list[Page],
    sources: list[SourcePage],
) -> dict[Path, Path]:
    mapping: dict[Path, Path] = {}
    for page in pages:
        mapping[page.index_path.resolve()] = page.output_path

        legacy_flat = project_root / "data" / page.entity_type / f"{page.slug}.md"
        if legacy_flat.exists():
            mapping[legacy_flat.resolve()] = page.output_path

    for source_page in sources:
        mapping[source_page.source_path.resolve()] = source_page.output_path
        raw_source_path = str(source_page.metadata.get("source-path") or "").strip()
        if raw_source_path:
            legacy_note = (project_root / raw_source_path).resolve()
            mapping[legacy_note] = source_page.output_path

    return mapping


def build_site_content(project_root: Path) -> None:
    entity_data_root = infer_entity_data_root(project_root)
    sources_root = infer_sources_root(project_root, entity_data_root)

    docs_dir = project_root / ".build" / "docs"
    if docs_dir.exists():
        shutil.rmtree(docs_dir)
    docs_dir.mkdir(parents=True, exist_ok=True)

    people = collect_entity_pages(data_root=entity_data_root, docs_dir=docs_dir, entity_type="person")
    orgs = collect_entity_pages(data_root=entity_data_root, docs_dir=docs_dir, entity_type="org")
    sources = collect_source_pages(sources_root=sources_root, docs_dir=docs_dir)

    pages = [*people, *orgs]
    pages_by_entity_rel_path = {page.entity_rel_path: page for page in pages}
    sources_by_citation_key = {source_page.citation_key: source_page for source_page in sources}
    relation_targets: dict[str, tuple[str, Path]] = {
        page.entity_rel_path: (page.title, page.output_path) for page in pages
    }
    for source_page in sources:
        source_rel_path = source_page.source_path.parent.relative_to(entity_data_root).as_posix()
        relation_targets[source_rel_path] = (source_page.title, source_page.output_path)
    source_to_output = build_source_to_output_map(
        project_root=project_root,
        pages=pages,
        sources=sources,
    )

    for page in pages:
        page.output_path.write_text(
            render_page(
                page,
                source_to_output=source_to_output,
                pages_by_entity_rel_path=pages_by_entity_rel_path,
                relation_targets=relation_targets,
                sources_by_citation_key=sources_by_citation_key,
            ),
            encoding="utf-8",
        )
    for source_page in sources:
        source_page.output_path.write_text(
            render_source_page(
                source_page,
                source_to_output=source_to_output,
                sources_by_citation_key=sources_by_citation_key,
            ),
            encoding="utf-8",
        )

    copy_entity_images(pages=pages, docs_dir=docs_dir)
    copy_source_assets(sources_root=sources_root, docs_dir=docs_dir)
    copy_site_assets(project_root=project_root, docs_dir=docs_dir)

    entity_source_label = entity_data_root.relative_to(project_root).as_posix()
    sources_source_label = sources_root.relative_to(project_root).as_posix()

    (docs_dir / "index.md").write_text(
        render_home(
            people=people,
            orgs=orgs,
            sources=sources,
            entity_source_label=entity_source_label,
            sources_source_label=sources_source_label,
        ),
        encoding="utf-8",
    )
    (docs_dir / "people.md").write_text(
        render_people_index(people=people, source_label=entity_source_label), encoding="utf-8"
    )
    (docs_dir / "orgs.md").write_text(
        render_orgs_index(orgs=orgs, source_label=entity_source_label), encoding="utf-8"
    )
    (docs_dir / "sources.md").write_text(
        render_sources_index(sources=sources, source_label=sources_source_label), encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        default=Path(__file__).resolve().parents[2],
        type=Path,
        help="Repository root containing data roots and mkdocs.yml.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_site_content(project_root=args.project_root.resolve())


if __name__ == "__main__":
    main()
