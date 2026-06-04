"""LLM backends for RAG generation: Ollama or Gemini.

Provider is chosen from .env (no LLM_PROVIDER flag):
  - GEMINI_API_KEY set → Gemini
  - else OLLAMA_API_KEY set, or no Gemini key → Ollama (local or authenticated)
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer the user's question using ONLY the provided "
    "context when it is relevant. If the context does not contain the answer, say so "
    "and answer from general knowledge, noting that it is not grounded in the context."
)


def _env_set(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def provider_name() -> str:
    """Infer backend from which API keys are present in the environment."""
    if _env_set("GEMINI_API_KEY"):
        return "gemini"
    return "ollama"


def provider_display() -> str:
    """Short label for UI (model + backend)."""
    if provider_name() == "gemini":
        return f"Gemini ({GEMINI_MODEL})"
    auth = " + API key" if _env_set("OLLAMA_API_KEY") else ""
    return f"Ollama ({OLLAMA_MODEL}){auth} @ {OLLAMA_HOST}"


def _ollama_headers() -> dict[str, str]:
    key = os.getenv("OLLAMA_API_KEY", "").strip()
    if key:
        return {"Authorization": f"Bearer {key}"}
    return {}


def _stream_ollama(system: str, user: str) -> Iterator[str]:
    import requests

    with requests.post(
        f"{OLLAMA_HOST}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": True,
        },
        headers=_ollama_headers(),
        stream=True,
        timeout=300,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            token = chunk.get("message", {}).get("content", "")
            if token:
                yield token


def _stream_gemini(system: str, user: str) -> Iterator[str]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError(
            "google-genai is required when GEMINI_API_KEY is set. "
            "Install with: pip install google-genai"
        ) from exc

    client = genai.Client(api_key=api_key)
    for chunk in client.models.generate_content_stream(
        model=GEMINI_MODEL,
        contents=user,
        config=types.GenerateContentConfig(system_instruction=system),
    ):
        if chunk.text:
            yield chunk.text


def stream_answer(
    user: str,
    *,
    system: str = DEFAULT_SYSTEM_PROMPT,
) -> Iterator[str]:
    """Stream response tokens from the configured LLM provider."""
    if provider_name() == "gemini":
        yield from _stream_gemini(system, user)
    else:
        yield from _stream_ollama(system, user)


def complete_answer(
    user: str,
    *,
    system: str = DEFAULT_SYSTEM_PROMPT,
) -> str:
    """Non-streaming full response (e.g. smoke tests)."""
    return "".join(stream_answer(user, system=system))


def provider_error_hint(exc: Exception) -> str:
    if provider_name() == "gemini":
        return (
            f"Gemini request failed: {exc}\n\n"
            "Check GEMINI_API_KEY and GEMINI_MODEL in .env. "
            "Get a key at https://aistudio.google.com/apikey"
        )
    hint = f"Confirm {OLLAMA_HOST} is reachable"
    if _env_set("OLLAMA_API_KEY"):
        hint += " and OLLAMA_API_KEY is valid"
    else:
        hint += f" and run `ollama run {OLLAMA_MODEL}` for a local model"
    return f"Ollama request failed: {exc}\n\n{hint}."
