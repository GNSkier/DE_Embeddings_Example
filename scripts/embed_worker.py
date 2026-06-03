#!/usr/bin/env python3
"""Task 2 — the consume -> transform -> embed -> store loop.

Subscribes to a Redis stream channel (reusing stream_pubsub.subscribe), turns each
message into a document, embeds it, and upserts it into ChromaDB. This is the worker
that Grant's demo_publish.py feeds: publish a message and watch it land in Chroma.

Usage:
  python scripts/embed_worker.py [channel]      # default channel: EMBED_CHANNEL or "embeddings"

Message payloads accepted (see rag_common.row_to_document):
  - JSON: {"messages": [{"role": "user", "content": "..."}]}
  - JSON: {"text": "..."}  (or any JSON dict)
  - plain text
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag_common import embed_texts, get_collection, row_to_document  # noqa: E402
from stream_pubsub import stream_key, subscribe  # noqa: E402

EMBED_CHANNEL = os.getenv("EMBED_CHANNEL", "embeddings")


def _coerce(message: str):
    """Unwrap a {"text": ...} envelope to its text; otherwise pass through."""
    stripped = message.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict) and "messages" not in obj and "text" in obj:
                return str(obj["text"])
        except json.JSONDecodeError:
            pass
    return message


def main() -> None:
    channel = sys.argv[1] if len(sys.argv) > 1 else EMBED_CHANNEL
    collection = get_collection()
    key = stream_key(channel)
    print(f"embed_worker listening on {key} -> Chroma collection (replay from start)")

    # last_id="0" so messages published before the worker started are also embedded.
    for entry_id, message in subscribe(channel, last_id="0"):
        text, meta = row_to_document(_coerce(message), subset="stream", source_idx=None)
        if not text:
            print(f"  {entry_id}  (skipped: empty)")
            continue
        meta["entry_id"] = entry_id
        embeddings = embed_texts([text])
        collection.upsert(
            ids=[entry_id],
            embeddings=embeddings,
            documents=[text],
            metadatas=[meta],
        )
        preview = text.replace("\n", " ")[:60]
        print(f"  {entry_id}  stored ({collection.count()} total)  {preview!r}")


if __name__ == "__main__":
    main()
