from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INDEX_VERSION = 1
DEFAULT_INDEX_PATH = ".build/semantic/index.json"
DEFAULT_MODEL_NAME = "BAAI/bge-small-en-v1.5"
DEFAULT_MODEL_CACHE_PATH = ".build/semantic/model-cache"
DEFAULT_MAX_CHARS = 1200
DEFAULT_MIN_CHARS = 200
DEFAULT_OVERLAP_CHARS = 120


class EmbeddingBackend:
    backend_id = "unknown"
    model_name = "unknown"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class FastEmbedBackend(EmbeddingBackend):
    backend_id = "fastembed"

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_MODEL_NAME,
        cache_dir: Path | None = None,
    ) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            from fastembed import TextEmbedding
        except Exception as exc:
            raise RuntimeError(
                "fastembed is required for semantic indexing/search. "
                "Install optional dependencies with `uv sync --extra semantic`."
            ) from exc

        kwargs: dict[str, Any] = {"model_name": self.model_name}
        if self.cache_dir is not None:
            kwargs["cache_dir"] = str(self.cache_dir)
        self._model = TextEmbedding(**kwargs)
        return self._model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load_model()
        vectors: list[list[float]] = []
        for embedding in model.embed(texts):
            vectors.append([float(value) for value in embedding])
        return vectors


def resolve_runtime_path(project_root: Path, raw_path: str | Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (project_root / path).resolve()


def relpath(path: Path, project_root: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()


def strip_frontmatter(markdown: str) -> str:
    text = markdown
    if text.startswith("\ufeff"):
        text = text[1:]
    if not text.startswith("---\n"):
        return text

    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    return text[end + 5 :]


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def split_paragraphs(text: str) -> list[str]:
    paragraphs = re.split(r"\n\s*\n", text.strip())
    return [normalize_text(paragraph) for paragraph in paragraphs if paragraph.strip()]


def split_long_paragraph(paragraph: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    if len(paragraph) <= max_chars:
        return [paragraph]

    step = max(max_chars - max(overlap_chars, 0), 1)
    chunks: list[str] = []
    start = 0
    while start < len(paragraph):
        part = paragraph[start : start + max_chars].strip()
        if part:
            chunks.append(part)
        if start + max_chars >= len(paragraph):
            break
        start += step
    return chunks


def chunk_text(
    markdown: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    min_chars: int = DEFAULT_MIN_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    if max_chars < 80:
        raise ValueError("max_chars must be at least 80")
    if min_chars < 1:
        raise ValueError("min_chars must be positive")

    body = strip_frontmatter(markdown).strip()
    if not body:
        return []

    expanded: list[str] = []
    for paragraph in split_paragraphs(body):
        expanded.extend(split_long_paragraph(paragraph, max_chars=max_chars, overlap_chars=overlap_chars))

    chunks: list[str] = []
    current_parts: list[str] = []
    for paragraph in expanded:
        if not current_parts:
            current_parts = [paragraph]
            continue

        candidate = "\n\n".join([*current_parts, paragraph])
        if len(candidate) <= max_chars:
            current_parts.append(paragraph)
            continue

        flushed = "\n\n".join(current_parts).strip()
        if flushed:
            chunks.append(flushed)
        current_parts = [paragraph]

    if current_parts:
        flushed = "\n\n".join(current_parts).strip()
        if flushed:
            chunks.append(flushed)

    if len(chunks) >= 2 and len(chunks[-1]) < min_chars:
        chunks[-2] = f"{chunks[-2]}\n\n{chunks[-1]}".strip()
        chunks = chunks[:-1]

    return chunks


def collect_markdown_chunks(
    *,
    project_root: Path,
    data_root: Path,
    max_chars: int = DEFAULT_MAX_CHARS,
    min_chars: int = DEFAULT_MIN_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for path in sorted(data_root.rglob("*.md"), key=lambda value: value.as_posix()):
        if not path.is_file():
            continue

        try:
            data_rel = path.relative_to(data_root).as_posix()
        except ValueError:
            continue

        content = path.read_text(encoding="utf-8")
        file_chunks = chunk_text(
            content,
            max_chars=max_chars,
            min_chars=min_chars,
            overlap_chars=overlap_chars,
        )

        for index, text in enumerate(file_chunks):
            chunks.append(
                {
                    "id": f"{data_rel}#chunk-{index:04d}",
                    "path": relpath(path, project_root),
                    "data_path": data_rel,
                    "char_count": len(text),
                    "text": text,
                }
            )
    return chunks


def _validate_vectors(vectors: list[list[float]], expected_count: int) -> int:
    if len(vectors) != expected_count:
        raise RuntimeError(f"embedding backend returned {len(vectors)} vectors for {expected_count} chunks")
    if not vectors:
        return 0

    embedding_dim = len(vectors[0])
    if embedding_dim == 0:
        raise RuntimeError("embedding backend returned zero-length vectors")
    for index, vector in enumerate(vectors):
        if len(vector) != embedding_dim:
            raise RuntimeError(
                f"inconsistent embedding dimensions at index {index}: {len(vector)} vs {embedding_dim}"
            )
    return embedding_dim


def build_semantic_index(
    *,
    project_root: Path,
    data_root: Path,
    index_path: Path,
    embedding_backend: EmbeddingBackend,
    max_chars: int = DEFAULT_MAX_CHARS,
    min_chars: int = DEFAULT_MIN_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> dict[str, Any]:
    chunks = collect_markdown_chunks(
        project_root=project_root,
        data_root=data_root,
        max_chars=max_chars,
        min_chars=min_chars,
        overlap_chars=overlap_chars,
    )
    texts = [chunk["text"] for chunk in chunks]
    vectors = embedding_backend.embed_texts(texts)
    embedding_dim = _validate_vectors(vectors, len(chunks))

    chunk_records: list[dict[str, Any]] = []
    for chunk, vector in zip(chunks, vectors):
        chunk_records.append(
            {
                "id": chunk["id"],
                "path": chunk["path"],
                "data_path": chunk["data_path"],
                "char_count": chunk["char_count"],
                "text": chunk["text"],
                "vector": vector,
            }
        )

    payload = {
        "version": INDEX_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project_root),
        "data_root": str(data_root),
        "model": {
            "backend": embedding_backend.backend_id,
            "name": embedding_backend.model_name,
        },
        "chunking": {
            "max_chars": max_chars,
            "min_chars": min_chars,
            "overlap_chars": overlap_chars,
        },
        "embedding_dim": embedding_dim,
        "chunk_count": len(chunk_records),
        "chunks": chunk_records,
    }

    index_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = index_path.with_suffix(f"{index_path.suffix}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temp_path.replace(index_path)

    return {
        "ok": True,
        "index_path": relpath(index_path, project_root),
        "chunk_count": len(chunk_records),
        "embedding_dim": embedding_dim,
        "model": payload["model"],
        "chunking": payload["chunking"],
    }


def load_semantic_index(index_path: Path) -> dict[str, Any]:
    if not index_path.exists():
        raise FileNotFoundError(f"semantic index file not found: {index_path.as_posix()}")
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    if int(payload.get("version") or -1) != INDEX_VERSION:
        raise ValueError(
            f"unsupported semantic index version: {payload.get('version')} (expected {INDEX_VERSION})"
        )
    if not isinstance(payload.get("chunks"), list):
        raise ValueError("invalid semantic index payload: chunks missing")
    return payload


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vector dimensions do not match")
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for left_value, right_value in zip(left, right):
        dot += left_value * right_value
        left_norm += left_value * left_value
        right_norm += right_value * right_value

    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / math.sqrt(left_norm * right_norm)


def _excerpt(text: str, *, max_chars: int = 240) -> str:
    normalized = normalize_text(text)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def search_semantic_index(
    *,
    index_payload: dict[str, Any],
    query: str,
    embedding_backend: EmbeddingBackend,
    limit: int = 8,
    min_score: float | None = None,
    allow_model_mismatch: bool = False,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be positive")

    model_payload = index_payload.get("model") or {}
    index_model_name = str(model_payload.get("name") or "")
    if index_model_name and embedding_backend.model_name != index_model_name and not allow_model_mismatch:
        raise ValueError(
            "semantic index model mismatch; rebuild index or search with matching model "
            f"(index={index_model_name}, query={embedding_backend.model_name})"
        )

    query_text = query.strip()
    if not query_text:
        raise ValueError("query must be non-empty")

    query_vectors = embedding_backend.embed_texts([query_text])
    embedding_dim = int(index_payload.get("embedding_dim") or 0)
    query_vector = query_vectors[0] if query_vectors else []
    if embedding_dim > 0 and len(query_vector) != embedding_dim:
        raise ValueError(
            f"query embedding dimension mismatch: expected {embedding_dim}, got {len(query_vector)}"
        )

    candidates: list[dict[str, Any]] = []
    for chunk in index_payload.get("chunks", []):
        vector = chunk.get("vector")
        if not isinstance(vector, list):
            continue
        chunk_vector = [float(value) for value in vector]
        score = _cosine_similarity(query_vector, chunk_vector)
        if min_score is not None and score < min_score:
            continue
        candidates.append(
            {
                "id": str(chunk.get("id") or ""),
                "path": str(chunk.get("path") or ""),
                "data_path": str(chunk.get("data_path") or ""),
                "score": score,
                "char_count": int(chunk.get("char_count") or 0),
                "excerpt": _excerpt(str(chunk.get("text") or "")),
            }
        )

    candidates.sort(key=lambda value: value["score"], reverse=True)
    top = candidates[:limit]
    for rank, item in enumerate(top, start=1):
        item["rank"] = rank

    return {
        "ok": True,
        "query": query_text,
        "limit": limit,
        "chunk_count": int(index_payload.get("chunk_count") or len(index_payload.get("chunks", []))),
        "model": model_payload,
        "results": top,
    }
