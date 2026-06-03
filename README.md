# DE_Embeddings_Example

A local **Redis Streams** cache deployed with Docker. It mimics classic pub/sub (`PUBLISH` / `SUBSCRIBE`) while keeping recent messages on disk—handy for development, smoke tests, and pipelines that need short replay windows without a full message broker.

On top of that, this repo is an end-to-end **RAG demo**: seed a containerized **ChromaDB** with embeddings from a dataset, stream new messages through Redis into Chroma, and query it all from a **Streamlit** app backed by a local **Qwen** model (via Ollama).

```
dataset ──(bulk seed)──┐
                       ▼
Redis stream ─(worker)─► embed ─► ChromaDB ◄─ query ◄─ Streamlit ─► Ollama (Qwen)
```

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

## Showcase runbook (live student demo, no HuggingFace token needed)

A live demo must not stall on a network download. So split the work in two:

**Prep — before the session (one-time, off the clock).** Seed ChromaDB once. The vectors
persist in the `chroma_data` Docker volume, so this only happens once per machine.

```bash
docker compose up -d                                   # redis + chromadb
# Download is the slow part (rate-limited without a token, but FREE either way).
# Cache the slice so it never re-downloads, then the demo is fully offline:
DATASET_STREAMING=false MAX_DOCS=30000 python scripts/load_dataset_to_chroma.py
```

A free token (`HF_TOKEN`, https://huggingface.co/settings/tokens) just makes this download
faster — it is **not required** and there is **nothing to pay**. Tune `MAX_DOCS` to how
much you want seeded; the embeddings persist regardless.

**Showcase — during the session (fast, no HuggingFace).** Chroma is already seeded, so you
only run the instant parts:

1. **Pub/sub → live embed:** publish a message and watch the worker embed it into Chroma.
2. **RAG app:** ask the Streamlit app a question; it retrieves from Chroma and answers via
   the students' local Qwen model.

Nothing in the showcase touches HuggingFace, so the throttle can't bite mid-demo.

> Measured on Apple Silicon: embedding sustains ~140 docs/s, retrieval + a Qwen answer is
> a couple of seconds. The only slow step (dataset download) is done in prep.

## RAG pipeline: dataset → ChromaDB → query

The pieces below share `rag_common.py` (one embedding model — `all-MiniLM-L6-v2` — for
both ingest and query, so all vectors live in the same space). Configure everything via
`.env` (see `.env.example`). Make sure the stack is up first: `docker compose up -d`.

### 1. Seed ChromaDB from the dataset (bulk load)

Streams `ianncity/KIMI-K2.5-1000000x`, embeds rows, and upserts them into Chroma. It
**streams** the dataset, so only the rows you embed are downloaded — not the full ~20 GB.

```bash
# smoke test the path with a tiny slice first
MAX_DOCS=200 python scripts/load_dataset_to_chroma.py

# tuned default (~45-min demo budget); set MAX_DOCS≈199000 for a literal 50% slice
python scripts/load_dataset_to_chroma.py
```

`MAX_DOCS` controls scope (default `30000`). Tune it to your machine — long reasoning
traces embed slower than short text.

### Timing (measured on Apple Silicon / MPS)

- **Compute is fast.** Embedding sustains **~140 docs/s** after a one-time ~15s model
  load (5,000 docs in ~58s). At that rate a literal 50% slice (~199k docs) is ~24 min.
- **The bottleneck is the HF download, not compute.** With **no `HF_TOKEN`**, streaming
  the dataset is rate-limited (~2–3 MB/s) and buffers a large parquet shard before
  yielding the first rows — in testing the first batch hadn't landed after 4+ min.
  **Set `HF_TOKEN`** (see `.env`) to stay download-unbound; then wall-clock tracks the
  ~140 docs/s compute rate and the half-subset comfortably fits a 45-minute budget.
- If you can't use a token, keep `MAX_DOCS` modest (e.g. 30k) or pre-cache the dataset
  once with `huggingface-cli download`.

### 2. Stream new messages into ChromaDB (the live worker)

This closes the loop Grant's demos start: a published message is **consumed, transformed,
embedded, and stored** in Chroma. Run the worker, then publish to the same channel.

```bash
# terminal 1 — worker (subscribes to the `embeddings` channel, replays from start)
python scripts/embed_worker.py embeddings

# terminal 2 — publish a chat-shaped row (or any text)
python scripts/demo_publish.py embeddings '{"messages":[{"role":"user","content":"What is a binary search tree?"}]}'
python scripts/demo_publish.py embeddings "plain text also works"
```

The worker logs each upsert and the running collection count.

### 3. Query it: Streamlit RAG app

Embeds your question, retrieves top-k context from Chroma, and answers with a local Qwen
model served by **Ollama**.

```bash
ollama run qwen2.5                      # pull/serve the model (any Qwen tag; set OLLAMA_MODEL)
streamlit run app/streamlit_app.py
```

The sidebar shows the collection size and model; retrieved sources are shown per answer.

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
| `rag_common.py` | Shared embedding + Chroma helpers and the row→document transform |
| `scripts/demo_publish.py` | CLI publish smoke test |
| `scripts/demo_subscribe.py` | CLI subscribe smoke test |
| `scripts/load_dataset_to_chroma.py` | Bulk-seed ChromaDB from the dataset (streaming) |
| `scripts/embed_worker.py` | Consume stream → transform → embed → store in ChromaDB |
| `app/streamlit_app.py` | RAG chat app (ChromaDB retrieval + Qwen via Ollama) |
| `requirements.txt` | Python deps (`redis`, `chromadb`, `datasets`, `sentence-transformers`, `streamlit`, `requests`) |
| `.env.example` | Sample environment variables |
