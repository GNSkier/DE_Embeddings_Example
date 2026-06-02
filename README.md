# DE_Embeddings_Example

A local **Redis Streams** cache deployed with Docker. It mimics classic pub/sub (`PUBLISH` / `SUBSCRIBE`) while keeping recent messages on disk—handy for development, smoke tests, and pipelines that need short replay windows without a full message broker.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose v2 (`docker compose`)
- Optional: Python 3.10+ for the helper scripts and `stream_pubsub.py`

## Deploy the stream cache

1. Copy environment defaults (optional; compose works without `.env`):

   ```bash
   cp .env.example .env
   ```

2. Start Redis:

   ```bash
   docker compose up -d
   ```

3. Confirm the service is healthy:

   ```bash
   docker compose ps
   docker compose exec redis-stream-cache redis-cli ping
   ```

   Expect `PONG`. Redis is exposed on **`localhost:6379`** unless you change `REDIS_PORT` in `.env`.

4. Stop when finished:

   ```bash
   docker compose down          # stop container, keep data volume
   docker compose down -v       # stop and delete persisted stream data
   ```

## Configuration

Set these in `.env` (see `.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `REDIS_PORT` | `6379` | Host port mapped to the container |
| `REDIS_URL` | `redis://localhost:6379/0` | Connection string for Python clients |
| `REDIS_STREAM_PREFIX` | `pubsub` | Key prefix; channel `events` → stream `pubsub:events` |

## Stream-backed pub/sub model

| Classic pub/sub | This setup |
|-----------------|------------|
| `PUBLISH channel msg` | `XADD pubsub:<channel> * message <msg>` |
| `SUBSCRIBE channel` | `XREAD BLOCK` on `pubsub:<channel>` |

Streams persist via AOF (`redis/redis.conf`). Each publish appends one field, `message`, to the stream. The Python helper trims streams to roughly **10,000** entries per channel (`MAXLEN ~`).

**Why streams instead of pub/sub?** Fire-and-forget pub/sub drops messages for offline subscribers. Streams keep a bounded history, support blocking reads, and can use consumer groups later if you add workers.

## Try it: redis-cli

Use two terminals.

**Terminal 1 — subscribe** (blocks until new entries; `$` = only new messages):

```bash
docker compose exec redis-stream-cache redis-cli XREAD BLOCK 0 STREAMS pubsub:events $
```

**Terminal 2 — publish:**

```bash
docker compose exec redis-stream-cache redis-cli XADD pubsub:events '*' message '{"type":"embedding","id":1}'
```

**Inspect history:**

```bash
docker compose exec redis-stream-cache redis-cli XRANGE pubsub:events - +
```

To replay from the beginning in `XREAD`, use `0` instead of `$` as the stream ID.

## Try it: Python demos

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**Terminal 1 — subscriber:**

```bash
python scripts/demo_subscribe.py events
```

**Terminal 2 — publisher:**

```bash
python scripts/demo_publish.py events "hello from stream pubsub"
```

Optional third argument on subscribe: start after a specific entry id, or `0` for full replay:

```bash
python scripts/demo_subscribe.py events 0
```

Ensure `REDIS_URL` in `.env` matches your host if Redis is not on `localhost`.

## Use in your own code

Import `stream_pubsub` from the repo root (or add the project to `PYTHONPATH`):

```python
from stream_pubsub import publish, subscribe

publish("events", '{"type":"embedding","id":1}')

for entry_id, message in subscribe("events"):
    print(entry_id, message)
```

- `publish(channel, message, url=..., prefix=..., maxlen=...)` — returns the stream entry id.
- `subscribe(channel, block_ms=5000, last_id="$")` — blocking iterator of `(entry_id, message)`.
- `stream_key(channel)` — resolves `pubsub:<channel>` using `REDIS_STREAM_PREFIX`.

## Operations

```bash
docker compose logs -f redis-stream-cache
docker compose restart redis-stream-cache
```

Data is stored in the Docker volume `redis-stream-data` (see `docker-compose.yml`).

## Project layout

| Path | Description |
|------|-------------|
| `docker-compose.yml` | Redis 7 Alpine service, healthcheck, persistent volume |
| `redis/redis.conf` | AOF persistence, dev memory cap (256MB) |
| `stream_pubsub.py` | `publish` / `subscribe` wrapper over `XADD` / `XREAD` |
| `scripts/demo_publish.py` | CLI publish smoke test |
| `scripts/demo_subscribe.py` | CLI subscribe smoke test |
| `requirements.txt` | Python deps (`redis`, `chromadb`, `datasets`, `sentence-transformers`) |
| `.env.example` | Sample environment variables |
