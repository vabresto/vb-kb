from __future__ import annotations

import datetime as _dt
import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PARTIAL_DATE_RE = re.compile(r"^\d{4}(?:-\d{2})?(?:-\d{2})?$")
ENTITY_REL_PATH_RE = re.compile(
    r"^(?P<kind>person|org)/(?P<shard>[a-z0-9]{2})/(?P<prefix>person|org)@(?P<slug>[a-z0-9][a-z0-9-]*)$"
)
SOURCE_PATH_RE = re.compile(r"^data/(person|org|notes)/.+\.md$")
SNAKE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]*$")
EDGE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
EMPLOYMENT_ROW_ID_RE = re.compile(r"^employment-\d{3}$")
LOOKING_FOR_ROW_ID_RE = re.compile(r"^ask-\d{3}$")
CHANGELOG_ROW_ID_RE = re.compile(r"^change-\d{3}$")


class KBBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LookingForStatus(str, Enum):
    open = "open"
    paused = "paused"
    closed = "closed"


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
        raise ValueError("must match person/<shard>/person@<slug> or org/<shard>/org@<slug>")

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
        text = value.strip()
        if not SOURCE_PATH_RE.match(text):
            raise ValueError("source_path must match data/(person|org|notes)/**/*.md")
        return text

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
        text = value.strip()
        if not SOURCE_PATH_RE.match(text):
            raise ValueError("source_path must match data/(person|org|notes)/**/*.md")
        return text

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
        text = value.strip()
        if not SOURCE_PATH_RE.match(text):
            raise ValueError("source_path must match data/(person|org|notes)/**/*.md")
        return text

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
            text = str(item).strip()
            if not text:
                continue
            if text in normalized:
                continue
            normalized.append(text)
        if not normalized:
            raise ValueError("sources must contain at least one non-empty item")
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
