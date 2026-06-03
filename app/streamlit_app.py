#!/usr/bin/env python3
"""Task 3 — RAG chat app over ChromaDB + a local Qwen model served by Ollama.

Flow: user query -> embed (same model as ingest) -> retrieve top-k from Chroma ->
build a grounded prompt -> stream the answer from Ollama's Qwen model.

Run:
  ollama run qwen2.5            # ensure the model is pulled / served
  streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag_common import CHROMA_COLLECTION, embed_texts, get_collection  # noqa: E402

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5")

SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer the user's question using ONLY the provided "
    "context when it is relevant. If the context does not contain the answer, say so "
    "and answer from general knowledge, noting that it is not grounded in the context."
)


def retrieve(query: str, k: int):
    """Embed the query and return Chroma's top-k (documents, metadatas, distances)."""
    collection = get_collection()
    embedding = embed_texts([query])
    res = collection.query(
        query_embeddings=embedding,
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]
    return list(zip(docs, metas, dists))


def build_prompt(query: str, hits) -> str:
    context = "\n\n---\n\n".join(doc for doc, _meta, _dist in hits) or "(no context found)"
    return f"Context:\n{context}\n\nQuestion: {query}"


def stream_ollama(prompt: str):
    """Yield response tokens from Ollama's /api/chat (streaming)."""
    with requests.post(
        f"{OLLAMA_HOST}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "stream": True,
        },
        stream=True,
        timeout=300,
    ) as resp:
        resp.raise_for_status()
        import json

        for line in resp.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            token = chunk.get("message", {}).get("content", "")
            if token:
                yield token


# --- UI ---------------------------------------------------------------------

st.set_page_config(page_title="KIMI RAG", page_icon="🔎")
st.title("🔎 RAG over ChromaDB → Qwen (Ollama)")

with st.sidebar:
    st.subheader("Settings")
    k = st.slider("Top-k retrieved", 1, 10, 4)
    st.caption(f"Collection: `{CHROMA_COLLECTION}`")
    st.caption(f"Model: `{OLLAMA_MODEL}` @ {OLLAMA_HOST}")
    try:
        count = get_collection().count()
        st.caption(f"Docs in collection: **{count}**")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Cannot reach ChromaDB: {exc}\n\nIs `docker compose up -d` running?")

query = st.text_input("Ask a question", placeholder="e.g. How do I reverse a linked list?")

if query:
    try:
        hits = retrieve(query, k)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Retrieval failed: {exc}")
        st.stop()

    if not hits:
        st.warning("No documents retrieved — has the collection been seeded?")

    with st.expander(f"Retrieved {len(hits)} source(s)", expanded=False):
        for i, (doc, meta, dist) in enumerate(hits, 1):
            st.markdown(f"**{i}.** _(distance {dist:.3f}, {meta})_")
            st.text(doc[:800] + ("…" if len(doc) > 800 else ""))

    st.subheader("Answer")
    prompt = build_prompt(query, hits)
    try:
        st.write_stream(stream_ollama(prompt))
    except requests.RequestException as exc:
        st.error(
            f"Ollama request failed: {exc}\n\n"
            f"Start it with `ollama run {OLLAMA_MODEL}` and confirm {OLLAMA_HOST} is reachable."
        )
