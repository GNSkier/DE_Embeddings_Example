"""Redis Streams as a durable pub/sub stand-in (XADD / XREAD)."""

from __future__ import annotations

import os
from typing import Iterator

import redis


def stream_key(channel: str, prefix: str | None = None) -> str:
    p = prefix if prefix is not None else os.getenv("REDIS_STREAM_PREFIX", "pubsub")
    return f"{p}:{channel}"


def client(url: str | None = None) -> redis.Redis:
    return redis.from_url(url or os.getenv("REDIS_URL", "redis://localhost:6379/0"))


def publish(
    channel: str,
    message: str,
    *,
    url: str | None = None,
    prefix: str | None = None,
    maxlen: int | None = 10_000,
) -> str:
    """Append a message to the channel stream; returns entry id."""
    r = client(url)
    key = stream_key(channel, prefix)
    kwargs: dict = {}
    if maxlen is not None:
        kwargs["maxlen"] = maxlen
        kwargs["approximate"] = True
    entry_id = r.xadd(key, {"message": message}, **kwargs)
    return entry_id.decode() if isinstance(entry_id, bytes) else entry_id


def subscribe(
    channel: str,
    *,
    url: str | None = None,
    prefix: str | None = None,
    block_ms: int = 5000,
    last_id: str = "$",
) -> Iterator[tuple[str, str]]:
    """Yield (entry_id, message) for new stream entries (blocks like SUBSCRIBE)."""
    r = client(url)
    key = stream_key(channel, prefix)
    cursor = last_id
    while True:
        rows = r.xread({key: cursor}, block=block_ms, count=10)
        if not rows:
            continue
        for _stream, entries in rows:
            for entry_id, fields in entries:
                cursor = entry_id
                msg = fields.get(b"message", fields.get("message", b""))
                if isinstance(msg, bytes):
                    msg = msg.decode()
                yield entry_id if isinstance(entry_id, str) else entry_id.decode(), msg
