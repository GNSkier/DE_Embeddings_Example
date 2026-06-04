#!/usr/bin/env python3
"""RAG chat app over ChromaDB + Ollama (Qwen) or Gemini.

Flow: user query -> embed (same model as ingest) -> retrieve top-k from Chroma ->
build a grounded prompt -> stream the answer from the LLM inferred from .env API keys.

Run:
  # Ollama (default): leave GEMINI_API_KEY empty, run `ollama run qwen2.5`
  streamlit run app/streamlit_app.py

  # Gemini: set GEMINI_API_KEY in .env
  streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm_client import (  # noqa: E402
    provider_display,
    provider_error_hint,
    provider_name,
    stream_answer,
)
from rag_common import CHROMA_COLLECTION, embed_texts, get_collection  # noqa: E402


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


# --- UI ---------------------------------------------------------------------

st.set_page_config(page_title="KIMI RAG", page_icon="🔎")
st.title("🔎 RAG over ChromaDB")

with st.sidebar:
    st.subheader("Settings")
    k = st.slider("Top-k retrieved", 1, 10, 4)
    st.caption(f"Collection: `{CHROMA_COLLECTION}`")
    st.caption(f"LLM: **{provider_display()}** (backend: `{provider_name()}`)")
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
        st.write_stream(stream_answer(prompt))
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        st.error(provider_error_hint(exc))
