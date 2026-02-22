from __future__ import annotations

import datetime as _dt
import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PARTIAL_DATE_RE = re.compile(r"^\d{4}(?:-\d{2})?(?:-\d{2})?$")


class KBBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LookingForStatus(str, Enum):
    open = "open"
    paused = "paused"
    closed = "closed"


class EdgeRelation(str, Enum):
    works_at = "works_at"
    worked_at = "worked_at"
    founded = "founded"
    co_founded = "co_founded"
    invested_in = "invested_in"
    advises = "advises"
    introduced = "introduced"
    knows = "knows"
    partnered_with = "partnered_with"
    acquired = "acquired"


EDGE_RELATIONS_V1 = tuple(item.value for item in EdgeRelation)


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


ENTITY_PATH_RE = re.compile(r"^(person|org)/[^/]+/(person|org)@[^/]+$")


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

    @field_validator("id", "period", "organization", "role", "source_path", "source_section")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must be non-empty")
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
        if not ENTITY_PATH_RE.match(text):
            raise ValueError("organization_ref must point to person/*/person@* or org/*/org@*")
        return text


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

    @field_validator("id", "ask", "source_path", "source_section")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must be non-empty")
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

    @field_validator("id", "summary", "source_path")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must be non-empty")
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
        if not text:
            raise ValueError("id must be non-empty")
        return text

    @field_validator("from_entity", "to_entity")
    @classmethod
    def validate_entity_paths(cls, value: str) -> str:
        text = value.strip()
        if not ENTITY_PATH_RE.match(text):
            raise ValueError("must be person/*/person@* or org/*/org@*")
        return text

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
            normalized.append(text)
        if not normalized:
            raise ValueError("sources must contain at least one non-empty item")
        return normalized

    @model_validator(mode="after")
    def validate_edge_consistency(self) -> "EdgeRecord":
        if self.from_entity == self.to_entity:
            raise ValueError("from and to must be different entities")
        return self
