# DE_Embeddings_Example

A local **Redis Streams** cache deployed with Docker. It mimics classic pub/sub (`PUBLISH` / `SUBSCRIBE`) while keeping recent messages on disk—handy for development, smoke tests, and pipelines that need short replay windows without a full message broker.

On top of that, this repo is an end-to-end **RAG demo**: seed a containerized **ChromaDB** with embeddings from a dataset, stream new messages through Redis into Chroma, and query it all from a **Streamlit** app backed by an LLM—**Qwen** via local **Ollama** (default) or **Gemini** when `GEMINI_API_KEY` is set in `.env`.

```
dataset ──(bulk seed)──┐
                       ▼
Redis stream ─(worker)─► embed ─► ChromaDB ◄─ query ◄─ Streamlit ─► Ollama (Qwen) or Gemini
```

A chromaDB instance will be created that is initially partially filled. As time goes on, messages will be streamed through redis, updating the vector store. At the same time, users can prompt an LLM that references this vector data store. They should see that asking the same question before a particular message is ingested, versus after that message is ingested, generally leads to different outcomes.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose v2 (`docker compose`)
- **LLM (pick one):**
  - **Ollama (default):** [Ollama](https://ollama.com/download), sign in with `ollama signin`, and pull **Qwen** (`ollama run qwen2.5`)
  - **Gemini (alternative):** a [Google AI Studio API key](https://aistudio.google.com/apikey) set as `GEMINI_API_KEY` in `.env` (no local Ollama required)
- Optional: Python 3.10+ for the helper scripts and `stream_pubsub.py`

0. Set up environment:
   - a. In your conda terminal, run `conda create -n stream_rag`. This will create a conda environment for you to utilize for the purposes of our demonstration.
   - b. In the same terminal, run `conda activate stream_rag`, which will make sure your current python instance is using the environment we just created.
   - c. Still in the same terminal, navigate to the directory that this repository has been loaded into. The current directory should look something like `.../DE_Embeddings_Example`. Once there, run `pip install -r requirements.txt`, which will install the required packages for this workshop.

## Deploy the stream cache

1. Start Docker Services (Redis + ChromaDB):
   
   [Docker](https://www.geeksforgeeks.org/devops/how-is-docker-different-from-a-virtual-machine/) is a tool that lets you run containers, that mimic the overall effect of virtual machines. In the `docker-compose.yml` file, the instructions for the docker engine to build/deploy these containers exist. You should see that we have two services that will be containerized: **`redis-stream-cache`** and **`chromadb`**.

   `redis-stream-cache` is the service that runs redis. This'll run messages back and forth from our local silo into the deployed chromadb instance. `chromadb` is the service that runs a local container for chromadb vector database. This will ingest the messages that are being served by redis.

   **While in the `stream_rag` environment, and in the local directory to this project, please run**:

      ```
      docker compose up -d
      ```
   This will launch the services we've been describing.
   Confirm docker has built correctly by running the following commands in the same terminal

   ```bash
   docker compose ps
   docker compose exec redis-stream-cache redis-cli ping
   ```

   Expect `PONG`. Redis is exposed on **`localhost:6379`** unless you change `REDIS_PORT` in `.env`.

## Semi-fill ChromaDB

2. Preload Portion of the Dataset Into ChromaDB:
   
   The dataset we're using is the `ianncity/KIMI-K2.5-1000000x` one available on HuggingFace. It's accessible through packages you've ideally already installed for the environment you should be working in. We're hoping to load in **15k rows** of the data initially to the chromaDB.

   You'll notice a file `scripts/load_dataset_to_chroma.py` that's for taking the data from the dataset then loading it into the containerized chromaDB. It does so through the following steps:
   - Loads the data in cache
   - Splits the data accordingly
   - Connects to the ChromaDB Docker instance
   - Uses a predefined chunking function
   - Loads the chunks, and encodes them into ChromaDB

   **Please run**:
   ```
   python scripts/load_dataset_to_chroma.py
   ```

## Test the 'Dumb' LLM

3. Query the Semi-Filled LLM in Streamlit App:
   
   Right now, the chromaDB has been filled with the chunks we've already seeded earlier in [step 2](#Semi-fill_ChromaDB). We're going to launch the streamlit app, and through the interface there, we can run a query that prompts our LLM.

   **LLM setup (Ollama or Gemini):**
   - **Ollama + Qwen (default):** ensure Ollama is running and the model is pulled:
     ```bash
     ollama list
     ```
     Expect something like:
     ```bash
     NAME              ID              SIZE      MODIFIED      
     qwen2.5:latest    845dbda0ea48    4.7 GB    3 seconds ago  
     ```
   - **Gemini (alternative):** copy `.env.example` to `.env`, set `GEMINI_API_KEY` (and optionally `GEMINI_MODEL`). Leave `GEMINI_API_KEY` empty to keep using Ollama/Qwen.

   With the LLM configured, we should be able to run the streamlit app. **Please run**:
   ```bash
   cd app # Change directory to the app
   streamlit run streamlit_app.py
   ```
   The `streamlit_app.py` should look somewhat familiar to y'all. But it also references a few other scripts. Specifically, `rag_common.py` which creates a Chroma HTTP client. The docker compose we ran earlier then publishes it at the port we designated earlier. The `get_collection` function inside of `streamlit_appy.py` should connect to the Chroma container.

   When you run any query in the streamlit app, `retrieve()` will embed the query, then checks which results inside of the Chroma instance are closest, then base its answer off of those retrieved results.

   For testing purposes, let's demonstrate that the LLM can't answer this hyper-specific question, copy and paste it into the streamlit app and check its results:
   ```
   How is the Kholo species described in terms of appearance?
   ```
   The model should return something along the lines of not having enough context in order to answer appropriately. This question is related to material that is outside of its trained data. Y'all being experts on RAG now, know we can augment its data.

   Run the following:
   ```bash
   cd .. # get back in the root directory (hopefully)
   ```

   You can close the app once you've confirmed this. In the terminal that you launched the app from, hit `CMD+C` or `CTRL+C` to stop the app from being hosted.

## Stream New Data

4. Start the Streaming Service:

   Inside of `/scripts` is `publish_jsonl.py` and `subscribe_and_upsert.py`. The `publish_jsonl.py` script will take entries listed in the `pathfinder_rag_dataset.jsonl` and send them to any scripts that are currently listening to that publisher's topic. `subscriber_and_upsert.py` is a script that listens to the same topic that `publish_json.py` is publishing to. It then takes the message, and embeds it in Chroma.

   For this next part, you'll need to have three terminals open:
   1. Launch the subscriber:
   ```bash
   python scripts/subscribe_and_upsert.py
   ```
   You should see in the terminal, it prints out
   ```
   Listening on channel: ...
   ```
   Move onto the next step once you confirm it is running correctly.

   2. Launch the publisher:
   ```bash
   python scripts/publish_jsonl.py
   ```
   Once this starts running, it should print out that it has published a message:
   ```bash
   published idx=0 id=1780528448212-0 (0.0% )
   ```

   3. Navigate back to the `app` folder and start the streamlit app:
   ```bash
   streamtlit run streamlit_app.py --no-reload # activate developer mode
   ```

   This whole process simulates what it is like to have a message be live streamed to a database, and ingesting it as time goes. Your LLM should now be able to give an answer for the strange question listed above (re-listed here for your convenience):
   ```
   How is the Kholo species described in terms of appearance?
   ```

   You can try this question **before** and **after** your publisher has reached 50% completion:
   ```
   What does Blessed Swiftness provide to the character?
   ```

   Finally, try this question **before** and **after** your publisher has reached 90% completion:
   ```
   What are the prices for WITCHWARG elixir?
   ```

   This should demonstrate that the Chroma is being updated over time. We are streaming updates to the Chroma database. We've achieved real-time knowledge.
   
   This could be useful for a startup like HealthWrite. The goal there was to allow patients to get summaries of their healthcare information, and give doctors ways to summarize their patient meetings. Once meeting has been transcribed, the message can be streamed to the database, allowing for the patient to almost instantly start running queries on the meeting they'd just had.

   An alternative to this streamed RAG would be to batch the data (maybe something like nightly) and then retrain the LLM.

   If we glowed this whole set up a bit more, we could set up topic specific scripts that could be ingested slightly differently. Maybe we need to consider information coming from the pharmacist, anasthesiologist, cardiologist, and oncologist differently, as well as any other healthcare professional. They could then point to different topics, which different scripts could manipulate accordingly.

## Close the project

5. Stop when finished:

   Press `CMD+C` or `CTRL+C` on your terminals to stop the services.

   In any, run the following commands to shut down the docker containers.
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
| `GEMINI_API_KEY` | _(empty)_ | If set → Gemini; if empty → Ollama |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model name |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama API base URL |
| `OLLAMA_MODEL` | `qwen2.5` | Ollama model tag |
| `OLLAMA_API_KEY` | _(empty)_ | Optional Bearer token for Ollama Cloud / auth |

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
   the configured LLM (local **Qwen** through Ollama, or **Gemini** if `GEMINI_API_KEY` is set).

Nothing in the showcase touches HuggingFace, so the throttle can't bite mid-demo.

> Measured on Apple Silicon: embedding sustains ~140 docs/s, retrieval + an LLM answer (Qwen
> or Gemini) is a couple of seconds. The only slow step (dataset download) is done in prep.

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

Embeds your question, retrieves top-k context from Chroma, and answers with the LLM
inferred from API keys in `.env`:

| Condition | Backend | What you need |
|-----------|---------|----------------|
| `GEMINI_API_KEY` is set | Gemini | Key from [AI Studio](https://aistudio.google.com/apikey) |
| `GEMINI_API_KEY` empty (default) | Ollama | `ollama run qwen2.5` (optional `OLLAMA_API_KEY` for cloud) |

```bash
cp .env.example .env
# Ollama (default) — leave GEMINI_API_KEY empty
ollama run qwen2.5

# Or Gemini — set in .env:
# GEMINI_API_KEY=your-key
# GEMINI_MODEL=gemini-2.0-flash

streamlit run app/streamlit_app.py
```

The sidebar shows the collection size, active LLM provider, and retrieved sources per answer.

## Use in your own code

**Redis streams** (`stream_pubsub.py`):

```python
from stream_pubsub import publish, subscribe

publish("events", '{"type":"embedding","id":1}')
for entry_id, message in subscribe("events"):
    print(entry_id, message)
```

**LLM answers** (`llm_client.py` — Gemini if `GEMINI_API_KEY` is set, else Ollama):

```python
from llm_client import stream_answer, complete_answer

for token in stream_answer("Context: …\n\nQuestion: …?"):
    print(token, end="")
```

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
| `llm_client.py` | Ollama or Gemini generation (auto from API keys in `.env`) |
| `rag_common.py` | Shared embedding + Chroma helpers and the row→document transform |
| `scripts/demo_publish.py` | CLI publish smoke test |
| `scripts/demo_subscribe.py` | CLI subscribe smoke test |
| `scripts/load_dataset_to_chroma.py` | Bulk-seed ChromaDB from the dataset (streaming) |
| `scripts/embed_worker.py` | Consume stream → transform → embed → store in ChromaDB |
| `app/streamlit_app.py` | RAG chat app (ChromaDB retrieval + Qwen via Ollama or Gemini) |
| `requirements.txt` | Python deps (`redis`, `chromadb`, `datasets`, `sentence-transformers`, `streamlit`, `google-genai`, …) |
| `.env.example` | Sample environment variables |