#!/usr/bin/env python3
"""Publish entries from a JSONL file into a Redis stream.

Default file: pathfinder_rag_dataset.jsonl
Each line is parsed as JSON and published as a single message payload.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stream_pubsub import publish  # noqa: E402


def main() -> None:
    p = Path.cwd()
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", "-f", default=str(p / "pathfinder_rag_dataset.jsonl"))
    parser.add_argument("--channel", "-c", default="chroma_ingest")
    parser.add_argument("--delay", "-d", type=float, default=1.0,
                        help="Seconds to wait between publishing messages")
    parser.add_argument("--once", action="store_true", help="Publish file once and exit")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}")
        raise SystemExit(1)

    # Preload non-empty lines to compute total for percentage display
    raw_text = path.read_text(encoding="utf-8")
    all_lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    total = len(all_lines)
    if total == 0:
        print(f"No lines to publish in {path}")
        raise SystemExit(0)

    try:
        for idx, line in enumerate(all_lines):
            try:
                parsed = json.loads(line)
            except Exception as e:
                print(f"Skipping invalid JSON line {idx}: {e}")
                continue

            payload = {"source": str(path.name), "idx": idx, "messages": parsed}
            entry_id = publish(args.channel, json.dumps(payload))
            percent = (idx + 1) / total * 100.0
            print(f"published idx={idx} id={entry_id} ({percent:.1f}% )")
            time.sleep(args.delay)

    except KeyboardInterrupt:
        print("Interrupted by user")


if __name__ == "__main__":
    main()
