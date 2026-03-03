from __future__ import annotations

import json
from pathlib import Path

import yaml

from kb.edges import derive_citation_edges, derive_employment_edges


def _write_markdown_with_frontmatter(path: Path, frontmatter: dict[str, object], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter_block = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    path.write_text(f"---\n{frontmatter_block}\n---\n\n{body}", encoding="utf-8")


def _write_source_record(project_root: Path) -> None:
    _write_markdown_with_frontmatter(
        project_root / "data/source/te/source@test-source/index.md",
        {
            "id": "source@test-source",
            "title": "Test Source",
            "source-type": "website",
            "citation-key": "test-source",
            "source-path": "data/source/te/source@test-source/index.md",
            "url": "https://example.com/source",
            "retrieved-at": "2026-02-10",
        },
        "# Test Source\n",
    )


def _read_edge(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_derive_citation_edges_preserves_existing_dates_for_unchanged_edges(tmp_path: Path) -> None:
    project_root = tmp_path
    data_root = project_root / "data"
    _write_source_record(project_root)
    _write_markdown_with_frontmatter(
        project_root / "data/org/ac/org@acme/index.md",
        {"org": "Acme"},
        "# Acme\n\nAcme cites this source.[^test-source]\n",
    )

    edge_path = project_root / "data/edge/ci/edge@citation-org-acme-test-source.json"
    edge_path.parent.mkdir(parents=True, exist_ok=True)
    edge_path.write_text(
        json.dumps(
            {
                "id": "citation-org-acme-test-source",
                "relation": "cites",
                "directed": True,
                "from": "org/ac/org@acme",
                "to": "source/te/source@test-source",
                "first_noted_at": "2026-02-10",
                "last_verified_at": "2026-02-10",
                "valid_from": None,
                "valid_to": None,
                "sources": ["source/te/source@test-source"],
                "notes": "Derived citation edge from footnote [^test-source] in org/ac/org@acme",
                "strength": None,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    result = derive_citation_edges(
        project_root=project_root,
        data_root=data_root,
        as_of="2026-03-02",
    )

    assert result["ok"] is True
    assert result["updated_edge_files"] == 0
    assert result["created_edge_files"] == 0
    assert result["unchanged_existing"] == 1

    edge_payload = _read_edge(edge_path)
    assert edge_payload["first_noted_at"] == "2026-02-10"
    assert edge_payload["last_verified_at"] == "2026-02-10"


def test_derive_employment_edges_preserves_existing_dates_for_unchanged_edges(tmp_path: Path) -> None:
    project_root = tmp_path
    data_root = project_root / "data"
    _write_source_record(project_root)
    _write_markdown_with_frontmatter(
        project_root / "data/org/ac/org@acme/index.md",
        {"org": "Acme"},
        "# Acme\n",
    )
    _write_markdown_with_frontmatter(
        project_root / "data/person/al/person@alice/index.md",
        {"person": "Alice"},
        "# Alice\n",
    )
    employment_path = project_root / "data/person/al/person@alice/employment-history.jsonl"
    employment_path.write_text(
        json.dumps(
            {
                "id": "employment-001",
                "period": "2024 - Present",
                "organization": "Acme",
                "organization_ref": "org/ac/org@acme",
                "role": "Engineer",
                "notes": None,
                "source": "[^test-source]",
                "source_path": "data/person/al/person@alice/index.md",
                "source_section": "employment_history_table",
                "source_row": 1,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    edge_path = project_root / "data/edge/em/edge@employment-alice-employment-001.json"
    edge_path.parent.mkdir(parents=True, exist_ok=True)
    edge_path.write_text(
        json.dumps(
            {
                "id": "employment-alice-employment-001",
                "relation": "works_at",
                "directed": True,
                "from": "person/al/person@alice",
                "to": "org/ac/org@acme",
                "first_noted_at": "2026-02-10",
                "last_verified_at": "2026-02-10",
                "valid_from": None,
                "valid_to": None,
                "sources": ["source/te/source@test-source"],
                "notes": "Role: Engineer | Period: 2024 - Present",
                "strength": None,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    result = derive_employment_edges(
        project_root=project_root,
        data_root=data_root,
        as_of="2026-03-02",
    )

    assert result["ok"] is True
    assert result["updated_edge_files"] == 0
    assert result["created_edge_files"] == 0
    assert result["unchanged_existing"] == 1

    edge_payload = _read_edge(edge_path)
    assert edge_payload["first_noted_at"] == "2026-02-10"
    assert edge_payload["last_verified_at"] == "2026-02-10"
