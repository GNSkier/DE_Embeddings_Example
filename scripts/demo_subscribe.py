#!/usr/bin/env python3
"""Subscribe to a stream-backed channel (prints new messages)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stream_pubsub import stream_key, subscribe


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: demo_subscribe.py <channel> [last_id]", file=sys.stderr)
        sys.exit(1)
    channel = sys.argv[1]
    last_id = sys.argv[2] if len(sys.argv) > 2 else "$"
    key = stream_key(channel)
    print(f"listening on {key} (last_id={last_id!r}) …")
    for entry_id, message in subscribe(channel, last_id=last_id):
        print(f"{entry_id}  {message}")


if __name__ == "__main__":
    main()
