"""Shared helpers for the embedding pipeline: embeddings, ChromaDB, and row transforms.

Reused by the bulk loader (`scripts/load_dataset_to_chroma.py`), the streaming embed
worker (`scripts/embed_worker.py`), and the Streamlit RAG app (`app/streamlit_app.py`).
Using one embedding model on both ingest and query keeps all vectors in the same space.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Iterable

# --- Configuration (env with sane local defaults) ---------------------------

EMBED_MODEL = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")
CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "kimi_embeddings")


# --- Embeddings -------------------------------------------------------------


@lru_cache(maxsize=1)
def get_embedder():
    """Lazy singleton SentenceTransformer. Imported lazily so modules that only
    need config (or Chroma) don't pay the import cost."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBED_MODEL)


def embed_texts(texts: list[str], *, batch_size: int = 256) -> list[list[float]]:
    """Batched encode -> list of float vectors (JSON/Chroma friendly)."""
    embedder = get_embedder()
    vectors = embedder.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    return vectors.tolist()


# --- ChromaDB ---------------------------------------------------------------


@lru_cache(maxsize=1)
def get_collection():
    """Get-or-create the Chroma collection over HTTP. We supply our own
    embeddings on add/query, so no server-side embedding function is configured."""
    import chromadb

    client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    return client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


# --- Row / message transform ------------------------------------------------


def _flatten_messages(messages: Iterable[dict[str, Any]]) -> str:
    """Join a chat `messages` list into one embeddable string."""
    parts = []
    for m in messages:
        role = str(m.get("role", "")).strip() or "unknown"
        content = str(m.get("content", "")).strip()
        if content:
            parts.append(f"{role}: {content}")
    return "\n".join(parts)


def row_to_document(row: Any, *, subset: str = "", source_idx: int | None = None):
    """Transform a dataset row OR a streamed message into (text, metadata).

    Accepts:
      - a dict with a `messages` list (the KIMI dataset shape),
      - a JSON string encoding such a dict,
      - a plain text string (used as the document directly).
    Returns (text, metadata). Returns (None, _) when there's no usable text.
    """
    if isinstance(row, (bytes, bytearray)):
        row = row.decode("utf-8", errors="replace")

    if isinstance(row, str):
        stripped = row.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                # Not JSON — treat the raw string as the document.
                return stripped or None, _meta(subset, source_idx, n_messages=0)
        else:
            return stripped or None, _meta(subset, source_idx, n_messages=0)

    if isinstance(row, dict) and isinstance(row.get("messages"), list):
        messages = row["messages"]
        text = _flatten_messages(messages)
        meta = _meta(
            subset,
            source_idx,
            n_messages=len(messages),
            listlengths=row.get("listlengths"),
        )
        return (text or None), meta

    # Fallback: stringify whatever we got.
    text = str(row).strip()
    return (text or None), _meta(subset, source_idx, n_messages=0)


def _meta(subset: str, source_idx: int | None, **extra: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {"subset": subset}
    if source_idx is not None:
        meta["source_idx"] = source_idx
    for k, v in extra.items():
        if v is not None:
            meta[k] = v
    return meta
