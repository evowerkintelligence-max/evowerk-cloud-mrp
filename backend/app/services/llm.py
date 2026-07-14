from __future__ import annotations

import asyncio
import json
from functools import lru_cache

from google import genai

from app.core.config import get_settings

# Stateless Gemini wrapper shared by every LLM-backed feature (data chat,
# computer vision, report narration). Ported from the desktop app's
# backend/llm/gemini_provider.py. The SDK client is sync-only, so calls run in
# a thread via asyncio.to_thread to avoid blocking the event loop.


class LlmUnavailableError(RuntimeError):
    """Raised when no Gemini API key is configured. Callers must catch this
    and fall back to their deterministic path - it is not a request error."""


@lru_cache
def _client() -> genai.Client:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise LlmUnavailableError("GEMINI_API_KEY is not configured")
    return genai.Client(api_key=settings.gemini_api_key)


def is_configured() -> bool:
    return bool(get_settings().gemini_api_key)


async def generate_text(
    prompt: str,
    *,
    system: str | None = None,
    max_output_tokens: int | None = None,
) -> str:
    if not prompt.strip():
        return ""

    client = _client()
    parts = [f"[SYSTEM]\n{system}\n", prompt] if system else [prompt]
    config = {"max_output_tokens": max_output_tokens} if max_output_tokens is not None else {}

    def _call() -> str:
        resp = client.models.generate_content(
            model=get_settings().gemini_model,
            contents="\n\n".join(parts),
            config=config,
        )
        return (resp.text or "").strip()

    return await asyncio.to_thread(_call)


async def generate_json(
    prompt: str,
    *,
    system: str | None = None,
    max_output_tokens: int | None = None,
) -> dict:
    """Best-effort JSON mode: instructs the model to return only JSON, then
    parses it. Raises ValueError (not LlmUnavailableError) on malformed
    output - callers already inside a try/except LlmUnavailableError should
    catch this separately if they want to fall back on bad JSON too."""
    json_system = (f"{system}\n\n" if system else "") + (
        "Return ONLY valid JSON. Do not wrap in markdown fences. Do not include commentary."
    )
    text = await generate_text(prompt, system=json_system, max_output_tokens=max_output_tokens)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Model did not return valid JSON. Raw output: {text[:500]}") from exc


async def generate_from_image(
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
    *,
    system: str | None = None,
) -> str:
    """Vision call for computer_vision.py - image + text prompt in, text out."""
    client = _client()
    parts: list[object] = []
    if system:
        parts.append(f"[SYSTEM]\n{system}\n")
    parts.append(prompt)
    parts.append(genai.types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

    def _call() -> str:
        resp = client.models.generate_content(
            model=get_settings().gemini_model,
            contents=parts,
        )
        return (resp.text or "").strip()

    return await asyncio.to_thread(_call)
