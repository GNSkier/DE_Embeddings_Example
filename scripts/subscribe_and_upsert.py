#!/usr/bin/env python3
"""Subscribe to a Redis stream, validate incoming messages, embed and upsert into Chroma.

Listens on a channel (default 'chroma_ingest') and for each message attempts to
convert it into a document, embed it using `rag_common.embed_texts`, and upsert
it into the configured Chroma collection.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stream_pubsub import subscribe  # noqa: E402
from rag_common import embed_texts, get_collection, row_to_document  # noqa: E402


def process_message(payload: Any):
    """Turn the incoming payload into a (text, meta) pair suitable for Chroma."""
    # If the payload is a JSON object with 'messages' key (our publisher format),
    # pass it to row_to_document which understands that structure.
    if isinstance(payload, dict) and "messages" in payload:
        text, meta = row_to_document(payload, subset=payload.get("source", "jsonl"), source_idx=payload.get("idx"))
        return text, meta

    # If raw string, row_to_document will attempt to parse or use it as-is.
    return row_to_document(payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", "-c", default="chroma_ingest")
    parser.add_argument("--url", default=None, help="Redis URL (optional)")
    parser.add_argument("--prefix", default=None, help="Stream key prefix (optional)")
    args = parser.parse_args()

    collection = get_collection()
    print(f"Listening on channel: {args.channel} — writing to collection: {collection.name}")

    for entry_id, msg in subscribe(args.channel, url=args.url, prefix=args.prefix):
        try:
            payload = json.loads(msg)
        except Exception:
            print(f"Received non-JSON message {entry_id}, treating as raw text")
            payload = msg

        text, meta = process_message(payload)
        if not text:
            print(f"Skipping empty message {entry_id}")
            continue

        # Embed and upsert
        try:
            embeddings = embed_texts([text])
            doc_id = None
            if isinstance(payload, dict) and payload.get("idx") is not None:
                doc_id = f"{payload.get('source','jsonl')}-{payload.get('idx')}"
            else:
                doc_id = f"ingest-{entry_id}"

            collection.upsert(ids=[doc_id], embeddings=embeddings, documents=[text], metadatas=[meta])
            print(f"Upserted id={doc_id} entry={entry_id}")
        except Exception as e:
            print(f"Failed to upsert entry {entry_id}: {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()
