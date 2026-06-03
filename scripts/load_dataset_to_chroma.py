#!/usr/bin/env python3
"""Task 1 — bulk-seed ChromaDB from the KIMI-K2.5 dataset.

Streams the dataset (so we never download the full ~19.7 GB), embeds each row with
sentence-transformers, and upserts the vectors into the Chroma container.

Scope is tuned for a ~45-minute local demo via MAX_DOCS. Set MAX_DOCS to ~199000 for
a literal 50% slice of the General-Distillation subset.

Env (see .env.example):
  DATASET_NAME    default ianncity/KIMI-K2.5-1000000x
  DATASET_SUBSET  default General-Distillation
  DATASET_SPLIT   default train
  MAX_DOCS        default 30000   (number of rows to embed; tune to your machine)
  BATCH_SIZE      default 256
  CHROMA_* / EMBED_MODEL / CHROMA_COLLECTION  (see rag_common.py)
"""

from __future__ import annotations

import itertools
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag_common import embed_texts, get_collection, row_to_document  # noqa: E402

DATASET_NAME = os.getenv("DATASET_NAME", "ianncity/KIMI-K2.5-1000000x")
DATASET_SUBSET = os.getenv("DATASET_SUBSET", "General-Distillation")
DATASET_SPLIT = os.getenv("DATASET_SPLIT", "train")
MAX_DOCS = int(os.getenv("MAX_DOCS", "30000"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "256"))
# Streaming (default) downloads only what we consume — good for a one-time prep run.
# Set DATASET_STREAMING=false to download+cache the slice to local HF disk cache so
# re-runs are offline/fast (pair with HF_HUB_OFFLINE=1). No HF token required either way.
DATASET_STREAMING = os.getenv("DATASET_STREAMING", "true").lower() not in ("false", "0", "no")


def _flush(collection, ids, texts, metas) -> int:
    """Embed and upsert one batch. Returns number stored."""
    if not ids:
        return 0
    embeddings = embed_texts(texts, batch_size=BATCH_SIZE)
    collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=texts,
        metadatas=metas,
    )
    return len(ids)


def main() -> None:
    from datasets import load_dataset

    mode = "streaming" if DATASET_STREAMING else "cached/offline"
    print(
        f"Loading {DATASET_NAME} [{DATASET_SUBSET}/{DATASET_SPLIT}] "
        f"({mode}, MAX_DOCS={MAX_DOCS}, BATCH_SIZE={BATCH_SIZE})"
    )
    if DATASET_STREAMING:
        stream = load_dataset(
            DATASET_NAME, DATASET_SUBSET, split=DATASET_SPLIT, streaming=True
        )
    else:
        # Non-streaming: downloads (or reuses) the slice in the local HF cache, so a
        # later run with HF_HUB_OFFLINE=1 needs no network. Slice keeps the download bounded.
        stream = load_dataset(
            DATASET_NAME, DATASET_SUBSET, split=f"{DATASET_SPLIT}[:{MAX_DOCS}]"
        )

    collection = get_collection()
    start = time.time()
    stored = 0
    ids: list[str] = []
    texts: list[str] = []
    metas: list[dict] = []

    for idx, row in enumerate(itertools.islice(stream, MAX_DOCS)):
        text, meta = row_to_document(row, subset=DATASET_SUBSET, source_idx=idx)
        if not text:
            continue
        ids.append(f"{DATASET_SUBSET}-{idx}")
        texts.append(text)
        metas.append(meta)

        if len(ids) >= BATCH_SIZE:
            stored += _flush(collection, ids, texts, metas)
            ids, texts, metas = [], [], []
            elapsed = time.time() - start
            rate = stored / elapsed if elapsed else 0
            print(f"  stored {stored} docs  ({elapsed:.0f}s, {rate:.1f} docs/s)")

    stored += _flush(collection, ids, texts, metas)
    elapsed = time.time() - start
    print(
        f"Done. Stored {stored} docs in {elapsed:.0f}s. "
        f"Collection now holds {collection.count()} docs."
    )


if __name__ == "__main__":
    main()
