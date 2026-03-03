from __future__ import annotations

from pathlib import Path

import pytest

from kb import semantic


class FakeEmbeddingBackend(semantic.EmbeddingBackend):
    backend_id = "fake"
    model_name = "fake-mini"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vocabulary = [
            "alice",
            "payments",
            "infra",
            "gaming",
            "founder",
        ]
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            vectors.append([float(lowered.count(token)) for token in vocabulary])
        return vectors


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_chunk_text_strips_frontmatter() -> None:
    markdown = (
        "---\n"
        "id: person@alice\n"
        "title: Alice\n"
        "---\n\n"
        "Alice builds payments infrastructure.\n\n"
        "She enjoys durable systems.\n"
    )
    chunks = semantic.chunk_text(markdown, max_chars=120, min_chars=1, overlap_chars=0)

    assert chunks
    assert "id: person@alice" not in chunks[0]
    assert "Alice builds payments infrastructure." in chunks[0]


def test_build_and_load_semantic_index(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    data_root = project_root / "data"
    index_path = project_root / ".build" / "semantic" / "index.json"

    _write(
        data_root / "person" / "al" / "person@alice" / "index.md",
        (
            "---\n"
            "person: Alice\n"
            "---\n\n"
            "Alice is a founder focused on payments infrastructure.\n"
        ),
    )
    _write(
        data_root / "person" / "bo" / "person@bob" / "index.md",
        (
            "---\n"
            "person: Bob\n"
            "---\n\n"
            "Bob works on gaming systems and tooling.\n"
        ),
    )

    result = semantic.build_semantic_index(
        project_root=project_root,
        data_root=data_root,
        index_path=index_path,
        embedding_backend=FakeEmbeddingBackend(),
        max_chars=140,
        min_chars=1,
        overlap_chars=0,
    )
    assert result["ok"] is True
    assert result["chunk_count"] >= 2
    assert result["embedding_dim"] == 5

    payload = semantic.load_semantic_index(index_path)
    assert payload["model"]["backend"] == "fake"
    assert payload["model"]["name"] == "fake-mini"
    assert payload["chunk_count"] == result["chunk_count"]
    assert len(payload["chunks"]) == result["chunk_count"]


def test_search_semantic_index_ranks_expected_top_result(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    data_root = project_root / "data"
    index_path = project_root / ".build" / "semantic" / "index.json"
    backend = FakeEmbeddingBackend()

    _write(
        data_root / "person" / "al" / "person@alice" / "index.md",
        "Alice is a founder building payments infra products.",
    )
    _write(
        data_root / "person" / "bo" / "person@bob" / "index.md",
        "Bob develops gaming engines and graphics systems.",
    )

    semantic.build_semantic_index(
        project_root=project_root,
        data_root=data_root,
        index_path=index_path,
        embedding_backend=backend,
        max_chars=140,
        min_chars=1,
        overlap_chars=0,
    )
    payload = semantic.load_semantic_index(index_path)

    search = semantic.search_semantic_index(
        index_payload=payload,
        query="founder for payments infra",
        embedding_backend=backend,
        limit=3,
    )
    assert search["ok"] is True
    assert search["results"]
    assert search["results"][0]["data_path"].endswith("person/al/person@alice/index.md")
    assert search["results"][0]["score"] >= search["results"][-1]["score"]


def test_search_rejects_model_mismatch(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    data_root = project_root / "data"
    index_path = project_root / ".build" / "semantic" / "index.json"

    backend = FakeEmbeddingBackend()
    _write(data_root / "person" / "al" / "person@alice" / "index.md", "Alice payments infra")
    semantic.build_semantic_index(
        project_root=project_root,
        data_root=data_root,
        index_path=index_path,
        embedding_backend=backend,
        max_chars=140,
        min_chars=1,
        overlap_chars=0,
    )
    payload = semantic.load_semantic_index(index_path)
    payload["model"]["name"] = "different-model"

    with pytest.raises(ValueError, match="model mismatch"):
        semantic.search_semantic_index(
            index_payload=payload,
            query="alice",
            embedding_backend=backend,
            limit=3,
        )
