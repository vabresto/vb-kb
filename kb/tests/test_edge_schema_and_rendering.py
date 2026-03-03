from __future__ import annotations

import pytest

from kb.schemas import EdgeRecord
from kb.tools.build_site_content import relation_display


BASE_EDGE = {
    "id": "edge-sample",
    "directed": True,
    "from": "person/al/person@alice",
    "to": "org/ac/org@acme",
    "first_noted_at": "2026-01-01",
    "last_verified_at": "2026-01-02",
    "valid_from": None,
    "valid_to": None,
    "sources": ["source/so/source@some-source"],
    "notes": "sample",
}


def test_works_at_requires_person_to_org() -> None:
    payload = {**BASE_EDGE, "relation": "works_at"}
    record = EdgeRecord.model_validate(payload)
    assert record.relation.value == "works_at"

    bad_payload = {**payload, "to": "person/bo/person@bob"}
    with pytest.raises(ValueError, match="works_at edges must connect person -> org"):
        EdgeRecord.model_validate(bad_payload)


def test_knows_requires_undirected_and_strength() -> None:
    payload = {
        **BASE_EDGE,
        "relation": "knows",
        "directed": False,
        "to": "person/bo/person@bob",
        "strength": 8,
    }
    record = EdgeRecord.model_validate(payload)
    assert record.strength == 8

    with pytest.raises(ValueError, match="knows edges require strength"):
        EdgeRecord.model_validate({**payload, "strength": None})

    with pytest.raises(ValueError, match="knows edges must be undirected"):
        EdgeRecord.model_validate({**payload, "directed": True})


def test_cites_requires_source_target() -> None:
    payload = {
        **BASE_EDGE,
        "relation": "cites",
        "from": "org/ac/org@acme",
        "to": "source/so/source@some-source",
    }
    record = EdgeRecord.model_validate(payload)
    assert record.relation.value == "cites"

    with pytest.raises(ValueError, match="cites edges must connect"):
        EdgeRecord.model_validate({**payload, "to": "org/be/org@beta"})


def test_strength_only_allowed_for_knows() -> None:
    payload = {**BASE_EDGE, "relation": "works_at", "strength": 1}
    with pytest.raises(ValueError, match="strength is only allowed for knows"):
        EdgeRecord.model_validate(payload)


def test_relation_display_uses_worked_at_for_past_valid_to() -> None:
    record = EdgeRecord.model_validate(
        {
            **BASE_EDGE,
            "relation": "works_at",
            "valid_to": "2025-12-31",
        }
    )
    assert relation_display(record, as_of="2026-02-24") == "worked at"

    current_record = EdgeRecord.model_validate(
        {
            **BASE_EDGE,
            "relation": "works_at",
            "valid_to": "2026-12-31",
        }
    )
    assert relation_display(current_record, as_of="2026-02-24") == "works at"
