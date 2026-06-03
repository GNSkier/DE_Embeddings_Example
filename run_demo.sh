#!/usr/bin/env bash
# run_demo.sh — guided, step-by-step runner & benchmark for the DE_Embeddings RAG demo.
#
#   dataset → embed → ChromaDB ← pub/sub worker ;  query ← Streamlit → Ollama (Qwen)
#
# Usage:
#   ./run_demo.sh                       # interactive, pauses between steps
#   ./run_demo.sh --max-docs 30000      # seed 30k docs (benchmark scope)
#   ./run_demo.sh --model llama3.2:latest   # use a different Ollama model
#   ./run_demo.sh --yes                 # no pauses, run straight through
#   ./run_demo.sh --seed-only           # just steps 1-3 (the timed ingestion)
#   ./run_demo.sh --fresh --seed-only --max-docs 30000   # clean ingestion benchmark
#
# Tip: re-seeding the same docs is idempotent (count won't grow). Use --fresh for an
# accurate docs/s number, or the loader's own "Stored N docs in Ms" line.
# Env overrides (or put them in .env): MAX_DOCS, OLLAMA_MODEL, CHROMA_COLLECTION,
# EMBED_CHANNEL, DATASET_STREAMING, HF_TOKEN.

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
STREAMLIT="$ROOT/.venv/bin/streamlit"
CHROMA="$ROOT/.venv/bin/chroma"

# --- defaults (overridable by env or flags) ---
MAX_DOCS="${MAX_DOCS:-2000}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5}"
CHANNEL="${EMBED_CHANNEL:-embeddings}"
COLLECTION="${CHROMA_COLLECTION:-kimi_embeddings}"
ASSUME_YES=0
SEED_ONLY=0
FRESH=0
CHROMA_LOCAL_PID=""

while [ $# -gt 0 ]; do
  case "$1" in
    --max-docs)  MAX_DOCS="$2"; shift 2;;
    --model)     OLLAMA_MODEL="$2"; shift 2;;
    --channel)   CHANNEL="$2"; shift 2;;
    --yes|-y)    ASSUME_YES=1; shift;;
    --seed-only) SEED_ONLY=1; shift;;
    --fresh)     FRESH=1; shift;;
    -h|--help)   sed -n '2,16p' "$0"; exit 0;;
    *) echo "unknown arg: $1 (try --help)"; exit 1;;
  esac
done

# --- helpers ---
blue(){ printf "\033[1;34m%s\033[0m\n" "$*"; }
green(){ printf "\033[1;32m%s\033[0m\n" "$*"; }
yellow(){ printf "\033[1;33m%s\033[0m\n" "$*"; }
red(){ printf "\033[1;31m%s\033[0m\n" "$*"; }
step(){ echo; blue "════════ $* ════════"; }
pause(){ [ "$ASSUME_YES" = 1 ] && return 0; read -r -p $'\n↵ Enter to run this step (Ctrl-C to stop)… ' _ || exit 0; }
count(){ "$PY" -c "from rag_common import get_collection as g; print(g().count())" 2>/dev/null || echo 0; }
wait_http(){ # url, tries
  local url="$1" tries="${2:-15}" i
  for i in $(seq 1 "$tries"); do curl -s "$url" >/dev/null 2>&1 && return 0; sleep 1; done
  return 1
}
cleanup(){ [ -n "$CHROMA_LOCAL_PID" ] && kill "$CHROMA_LOCAL_PID" 2>/dev/null || true; }
trap cleanup EXIT

# ─────────────────────────────────────────────────────────────────────────────
step "0 · Preflight"
if [ ! -x "$PY" ]; then
  red "No venv found at .venv"
  echo "Create it first:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
echo "python      : $($PY --version 2>&1)"
echo "max_docs    : $MAX_DOCS"
echo "collection  : $COLLECTION"
echo "ollama model: $OLLAMA_MODEL"
"$PY" -c "import chromadb, datasets, sentence_transformers, redis, streamlit, requests" \
  && green "deps OK" || { red "missing deps — run: .venv/bin/pip install -r requirements.txt"; exit 1; }

# ─────────────────────────────────────────────────────────────────────────────
step "1 · Start services (Redis + ChromaDB)"
pause
echo "• starting Redis (docker)…"
docker compose up -d redis-stream-cache >/dev/null 2>&1 || true
redis_ok=0
for i in $(seq 1 15); do
  docker compose exec -T redis-stream-cache redis-cli ping 2>/dev/null | grep -q PONG && { redis_ok=1; break; }
  sleep 1
done
[ "$redis_ok" = 1 ] && green "redis: PONG" || yellow "redis not confirmed — check 'docker compose ps'"

echo "• starting ChromaDB…"
if curl -s localhost:8000/api/v2/heartbeat >/dev/null 2>&1; then
  green "chroma: already running on :8000"
elif docker compose up -d chromadb >/dev/null 2>&1 && wait_http "http://localhost:8000/api/v2/heartbeat" 15; then
  green "chroma: docker container healthy on :8000"
else
  yellow "docker chroma unavailable (disk space?) — falling back to a LOCAL chroma server"
  "$CHROMA" run --host localhost --port 8000 --path "$ROOT/chroma_data" >/tmp/chroma_local.log 2>&1 &
  CHROMA_LOCAL_PID=$!
  if wait_http "http://localhost:8000/api/v2/heartbeat" 30; then
    green "chroma: local server healthy on :8000 (pid $CHROMA_LOCAL_PID)"
  else
    red "chroma not reachable — see /tmp/chroma_local.log"; exit 1
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
step "2 · Health check"
curl -s localhost:8000/api/v2/heartbeat && echo
echo "collection '$COLLECTION' currently holds: $(count) docs"

# ─────────────────────────────────────────────────────────────────────────────
step "3 · Seed ChromaDB from the dataset  (MAX_DOCS=$MAX_DOCS) — TIMED"
echo "This is the ingestion benchmark. The script prints docs/s; we also time total wall-clock."
echo "Note: tokenless HF streaming download is the variable part (set HF_TOKEN in .env to speed it)."
if [ "$FRESH" = 1 ]; then
  yellow "--fresh: clearing collection '$COLLECTION' first (clean benchmark)…"
  "$PY" -c "import chromadb; chromadb.HttpClient(host='localhost',port=8000).delete_collection('$COLLECTION')" 2>/dev/null || true
fi
pause
before="$(count)"
t0=$(date +%s)
MAX_DOCS="$MAX_DOCS" CHROMA_COLLECTION="$COLLECTION" "$PY" scripts/load_dataset_to_chroma.py
rc=$?
t1=$(date +%s)
after="$(count)"
if [ "$rc" -ne 0 ]; then
  red "── SEED FAILED (exit $rc) after $((t1 - t0))s ──"
  echo "Most often a transient HuggingFace rate-limit / config-resolution error. Options:"
  echo "  • just retry (throttling is intermittent)"
  echo "  • set HF_TOKEN in .env (free; raises limits)"
  echo "  • DATASET_STREAMING=false ./run_demo.sh …  (load from local cache once downloaded)"
  exit "$rc"
fi
green "── SEED BENCHMARK ──"
echo "wall-clock : $((t1 - t0)) s"
echo "docs       : $before → $after  (+$((after - before)))"
if [ "$((after - before))" -le 0 ]; then
  yellow "(+0: those doc IDs already existed — idempotent re-seed. Use --fresh for a clean rate.)"
elif [ $((t1 - t0)) -gt 0 ]; then
  echo "avg rate   : $(( (after - before) / (t1 - t0) )) docs/s (incl. download warmup)"
fi

if [ "$SEED_ONLY" = 1 ]; then green "seed-only mode done."; exit 0; fi

# ─────────────────────────────────────────────────────────────────────────────
step "4 · Pub/sub → worker → Chroma  (Grant's demo_publish.py feeds the worker)"
pause
"$PY" scripts/embed_worker.py "$CHANNEL" >/tmp/worker.log 2>&1 &
wpid=$!
echo "• worker starting (importing model)…"
for i in $(seq 1 40); do grep -q "listening" /tmp/worker.log 2>/dev/null && break; sleep 1; done
b="$(count)"
echo "• publishing a chat message via demo_publish.py…"
"$PY" scripts/demo_publish.py "$CHANNEL" '{"messages":[{"role":"user","content":"What is a hash table and when should I use one?"}]}'
echo "• waiting for the worker to embed + store it…"
a="$b"
for i in $(seq 1 60); do a="$(count)"; [ "$a" -gt "$b" ] && break; sleep 1; done
kill "$wpid" 2>/dev/null
if [ "$a" -gt "$b" ]; then green "worker stored the message: $b → $a"; else red "no new doc — see /tmp/worker.log"; fi

# ─────────────────────────────────────────────────────────────────────────────
step "5 · RAG query smoke test  (retrieve from Chroma → answer via Ollama '$OLLAMA_MODEL')"
if ! curl -s localhost:11434/api/tags >/dev/null 2>&1; then
  yellow "Ollama not reachable on :11434 — start it with 'ollama serve' / 'ollama run $OLLAMA_MODEL'. Skipping."
else
  if ! curl -s localhost:11434/api/tags | grep -q "\"${OLLAMA_MODEL%%:*}"; then
    yellow "Model '$OLLAMA_MODEL' not found in Ollama. Pull it ('ollama pull $OLLAMA_MODEL') or pass --model <installed>. Trying anyway…"
  fi
  pause
  OLLAMA_MODEL="$OLLAMA_MODEL" CHROMA_COLLECTION="$COLLECTION" "$PY" - <<'PY'
import os, requests
from rag_common import embed_texts, get_collection
q = "How do you reverse a linked list?"
col = get_collection()
res = col.query(query_embeddings=embed_texts([q]), n_results=3, include=["documents","distances"])
ctx = "\n\n---\n\n".join(res["documents"][0])
print(f"query: {q}\nretrieved {len(res['documents'][0])} docs, distances={[round(d,3) for d in res['distances'][0]]}\n")
model = os.environ["OLLAMA_MODEL"]
r = requests.post("http://localhost:11434/api/chat", json={
    "model": model,
    "messages":[{"role":"system","content":"Answer using the context when relevant."},
                {"role":"user","content":f"Context:\n{ctx[:4000]}\n\nQuestion: {q}"}],
    "stream": False}, timeout=300)
r.raise_for_status()
print(f"=== {model} answer (first 500 chars) ===")
print(r.json()["message"]["content"][:500])
PY
  green "RAG path verified."
fi

# ─────────────────────────────────────────────────────────────────────────────
step "6 · Launch the Streamlit RAG app"
echo "Opens at http://localhost:8501 — type a question, see retrieval + the streamed answer."
echo "Using model: $OLLAMA_MODEL   (override: OLLAMA_MODEL=<name> ./run_demo.sh)"
pause
OLLAMA_MODEL="$OLLAMA_MODEL" CHROMA_COLLECTION="$COLLECTION" exec "$STREAMLIT" run app/streamlit_app.py
