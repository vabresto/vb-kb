from __future__ import annotations

import datetime as _dt
import re
from enum import Enum
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PARTIAL_DATE_RE = re.compile(r"^\d{4}(?:-\d{2})?(?:-\d{2})?$")
ENTITY_REL_PATH_RE = re.compile(
    r"^(?P<kind>person|org|source)/(?P<shard>[a-z0-9]{2})/(?P<prefix>person|org|source)@(?P<slug>[a-z0-9][a-z0-9-]*)$"
)
CANONICAL_INDEX_PATH_RE = re.compile(
    r"^data/(person|org|source)/[a-z0-9]{2}/(?:person|org|source)@[a-z0-9][a-z0-9-]*/index\.md$"
)
SNAKE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]*$")
EDGE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
EMPLOYMENT_ROW_ID_RE = re.compile(r"^employment-\d{3}$")
LOOKING_FOR_ROW_ID_RE = re.compile(r"^ask-\d{3}$")
CHANGELOG_ROW_ID_RE = re.compile(r"^change-\d{3}$")
SOURCE_ID_RE = re.compile(r"^source@[a-z0-9]+(?:-[a-z0-9]+)*$")
CITATION_KEY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
NOTE_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
SOURCE_REF_RE = re.compile(r"^source/[a-z0-9]{2}/source@[a-z0-9][a-z0-9-]*(?:#[a-z0-9_\-]+)?$")


class KBBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LookingForStatus(str, Enum):
    open = "open"
    paused = "paused"
    closed = "closed"


class SourceType(str, Enum):
    website = "website"
    note = "note"
    internal = "internal"
    document = "document"
    image = "image"


class EdgeRelation(str, Enum):
    works_at = "works_at"
    founds = "founds"
    co_founds = "co_founds"
    invests_in = "invests_in"
    advises = "advises"
    introduces = "introduces"
    knows = "knows"
    partners_with = "partners_with"
    acquires = "acquires"
    cites = "cites"


EDGE_RELATIONS_V1 = tuple(item.value for item in EdgeRelation)
LEGACY_EDGE_RELATION_ALIASES = {
    "worked_at": "works_at",
    "founded": "founds",
    "co_founded": "co_founds",
    "invested_in": "invests_in",
    "introduced": "introduces",
    "partnered_with": "partners_with",
    "acquired": "acquires",
}


def parse_partial_date(value: str) -> str:
    text = value.strip()
    if not PARTIAL_DATE_RE.match(text):
        raise ValueError("date must match YYYY or YYYY-MM or YYYY-MM-DD")

    parts = [int(part) for part in text.split("-")]
    year = parts[0]
    if year < 1900 or year > 2200:
        raise ValueError("year out of allowed range [1900, 2200]")
    if len(parts) == 1:
        return text

    month = parts[1]
    if month < 1 or month > 12:
        raise ValueError("month must be 01-12")
    if len(parts) == 2:
        return text

    day = parts[2]
    try:
        _dt.date(year, month, day)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    return text


def partial_date_sort_key(value: str) -> tuple[int, int, int]:
    text = parse_partial_date(value)
    parts = [int(part) for part in text.split("-")]
    year = parts[0]
    month = parts[1] if len(parts) > 1 else 0
    day = parts[2] if len(parts) > 2 else 0
    return (year, month, day)


def shard_for_slug(slug: str) -> str:
    letters = re.sub(r"[^a-z0-9]", "", slug.lower())
    if not letters:
        return "zz"
    if len(letters) == 1:
        return f"{letters}z"
    return letters[:2]


def validate_entity_rel_path(value: str) -> str:
    text = value.strip()
    match = ENTITY_REL_PATH_RE.match(text)
    if not match:
        raise ValueError(
            "must match person/<shard>/person@<slug>, org/<shard>/org@<slug>, "
            "or source/<shard>/source@<slug>"
        )

    kind = match.group("kind")
    prefix = match.group("prefix")
    shard = match.group("shard")
    slug = match.group("slug")

    if kind != prefix:
        raise ValueError("entity kind and folder prefix must match")

    expected_shard = shard_for_slug(slug)
    if shard != expected_shard:
        raise ValueError(f"invalid shard for slug {slug}; expected {expected_shard}")

    return text


def validate_canonical_index_path(value: str, *, field_name: str) -> str:
    text = value.strip()
    if not CANONICAL_INDEX_PATH_RE.match(text):
        raise ValueError(f"{field_name} must point to a canonical index.md file under data/")
    return text


def validate_source_ref(value: str) -> str:
    text = value.strip()
    if not SOURCE_REF_RE.match(text):
        raise ValueError(
            "source reference must match source/<shard>/source@<slug> (optional #fragment)"
        )

    path_part, _, _ = text.partition("#")
    validate_entity_rel_path(path_part)
    return text


def normalize_path_token(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.startswith("/"):
        raise ValueError("path must be relative")
    if ".." in text.split("/"):
        raise ValueError("path cannot contain '..'")
    return text


def validate_source_category(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().strip("/")
    if not text:
        return None
    if ".." in text.split("/"):
        raise ValueError("source-category cannot contain '..'")
    return text


class EmploymentHistoryRow(KBBaseModel):
    id: str
    period: str
    organization: str
    organization_ref: str | None = None
    role: str
    notes: str | None = None
    source: str | None = None
    source_path: str
    source_section: str
    source_row: int | None = None

    @field_validator("period", "organization", "role")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must be non-empty")
        return text

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        text = value.strip()
        if not EMPLOYMENT_ROW_ID_RE.match(text):
            raise ValueError("id must match employment-<3 digits>")
        return text

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, value: str) -> str:
        return validate_canonical_index_path(value, field_name="source_path")

    @field_validator("source_section")
    @classmethod
    def validate_source_section(cls, value: str) -> str:
        text = value.strip()
        if not SNAKE_TOKEN_RE.match(text):
            raise ValueError("source_section must be snake_case")
        return text

    @field_validator("source_row")
    @classmethod
    def validate_source_row(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 1:
            raise ValueError("source_row must be >= 1")
        return value

    @field_validator("organization_ref")
    @classmethod
    def validate_organization_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        return validate_entity_rel_path(text)


class LookingForRow(KBBaseModel):
    id: str
    ask: str
    details: str | None = None
    first_asked_at: str | None = None
    last_checked_at: str | None = None
    status: LookingForStatus
    notes: str | None = None
    source_path: str
    source_section: str
    source_row: int | None = None

    @field_validator("ask")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must be non-empty")
        return text

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        text = value.strip()
        if not LOOKING_FOR_ROW_ID_RE.match(text):
            raise ValueError("id must match ask-<3 digits>")
        return text

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, value: str) -> str:
        return validate_canonical_index_path(value, field_name="source_path")

    @field_validator("source_section")
    @classmethod
    def validate_source_section(cls, value: str) -> str:
        text = value.strip()
        if not SNAKE_TOKEN_RE.match(text):
            raise ValueError("source_section must be snake_case")
        return text

    @field_validator("source_row")
    @classmethod
    def validate_source_row(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 1:
            raise ValueError("source_row must be >= 1")
        return value

    @field_validator("first_asked_at", "last_checked_at")
    @classmethod
    def validate_dates(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        return parse_partial_date(text)


class ChangelogRow(KBBaseModel):
    id: str
    changed_at: str
    summary: str
    source_path: str
    source_row: int | None = None

    @field_validator("summary")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must be non-empty")
        return text

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        text = value.strip()
        if not CHANGELOG_ROW_ID_RE.match(text):
            raise ValueError("id must match change-<3 digits>")
        return text

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, value: str) -> str:
        return validate_canonical_index_path(value, field_name="source_path")

    @field_validator("changed_at")
    @classmethod
    def validate_changed_at(cls, value: str) -> str:
        return parse_partial_date(value)

    @field_validator("source_row")
    @classmethod
    def validate_source_row(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 1:
            raise ValueError("source_row must be >= 1")
        return value


class SourceRecord(KBBaseModel):
    id: str
    title: str
    source_type: SourceType = Field(alias="source-type")
    citation_key: str = Field(alias="citation-key")
    source_path: str = Field(alias="source-path")
    source_category: str | None = Field(alias="source-category", default=None)
    note_type: str | None = Field(alias="note-type", default=None)
    date: str | None = None
    updated_at: str | None = Field(alias="updated-at", default=None)
    url: str | None = None
    retrieved_at: str | None = Field(alias="retrieved-at", default=None)
    published_at: str | None = Field(alias="published-at", default=None)
    html_capture_path: str | None = Field(alias="html-capture-path", default=None)
    screenshot_path: str | None = Field(alias="screenshot-path", default=None)
    citation_text: str | None = Field(alias="citation-text", default=None)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        text = value.strip()
        if not SOURCE_ID_RE.match(text):
            raise ValueError("id must match source@<kebab-case>")
        return text

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("title must be non-empty")
        return text

    @field_validator("citation_key")
    @classmethod
    def validate_citation_key(cls, value: str) -> str:
        text = value.strip()
        if not CITATION_KEY_RE.match(text):
            raise ValueError("citation-key must be lowercase kebab-case")
        return text

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, value: str) -> str:
        return validate_canonical_index_path(value, field_name="source-path")

    @field_validator("note_type")
    @classmethod
    def validate_note_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        if not NOTE_TYPE_RE.match(text):
            raise ValueError("note-type must be snake_case")
        return text

    @field_validator("date", "updated_at", "retrieved_at", "published_at")
    @classmethod
    def validate_dates(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        return parse_partial_date(text)

    @field_validator("source_category")
    @classmethod
    def validate_source_category_value(cls, value: str | None) -> str | None:
        return validate_source_category(value)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        parsed = urlparse(text)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("url must use http or https")
        if not parsed.netloc:
            raise ValueError("url must include a hostname")
        return text

    @field_validator("html_capture_path", "screenshot_path")
    @classmethod
    def validate_capture_paths(cls, value: str | None) -> str | None:
        return normalize_path_token(value)

    @model_validator(mode="after")
    def validate_source_consistency(self) -> "SourceRecord":
        if self.source_type == SourceType.note and self.note_type is None:
            raise ValueError("note-type is required when source-type is 'note'")
        return self


class NoteRecord(SourceRecord):
    @model_validator(mode="after")
    def validate_note_record(self) -> "NoteRecord":
        if self.source_type != SourceType.note:
            raise ValueError("source-type must be 'note' for NoteRecord")
        return self


class EdgeRecord(KBBaseModel):
    id: str
    relation: EdgeRelation
    directed: bool
    from_entity: str = Field(alias="from")
    to_entity: str = Field(alias="to")
    first_noted_at: str
    last_verified_at: str
    valid_from: str | None = None
    valid_to: str | None = None
    sources: list[str]
    notes: str | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        text = value.strip()
        if not EDGE_ID_RE.match(text):
            raise ValueError("id must be lowercase kebab-case")
        return text

    @field_validator("relation", mode="before")
    @classmethod
    def normalize_relation(cls, value: str | EdgeRelation) -> str | EdgeRelation:
        if isinstance(value, EdgeRelation):
            return value
        text = str(value).strip()
        return LEGACY_EDGE_RELATION_ALIASES.get(text, text)

    @field_validator("from_entity", "to_entity")
    @classmethod
    def validate_entity_paths(cls, value: str) -> str:
        return validate_entity_rel_path(value)

    @field_validator("first_noted_at", "last_verified_at", "valid_from", "valid_to")
    @classmethod
    def validate_dates(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return parse_partial_date(value)

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            text = validate_source_ref(str(item))
            if text in normalized:
                continue
            normalized.append(text)
        if not normalized:
            raise ValueError("sources must contain at least one valid source reference")
        return normalized

    @model_validator(mode="after")
    def validate_edge_consistency(self) -> "EdgeRecord":
        if self.from_entity == self.to_entity:
            raise ValueError("from and to must be different entities")

        if partial_date_sort_key(self.last_verified_at) < partial_date_sort_key(self.first_noted_at):
            raise ValueError("last_verified_at must be on/after first_noted_at")

        if self.valid_from and self.valid_to:
            if partial_date_sort_key(self.valid_to) < partial_date_sort_key(self.valid_from):
                raise ValueError("valid_to must be on/after valid_from")

        return self
