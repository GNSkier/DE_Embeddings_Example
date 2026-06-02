#!/usr/bin/env python3
"""Publish one message to a stream-backed channel."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stream_pubsub import publish


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: demo_publish.py <channel> <message>", file=sys.stderr)
        sys.exit(1)
    channel, message = sys.argv[1], sys.argv[2]
    entry_id = publish(channel, message)
    print(f"published to {channel!r} id={entry_id}")


if __name__ == "__main__":
    main()
