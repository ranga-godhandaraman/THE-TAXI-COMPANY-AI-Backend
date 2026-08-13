"""Groq chat client for SQL / RAG / Analytics agents."""

from __future__ import annotations

import json
import re
from typing import Any

from app.config import Settings, get_settings

_client: Any = None


class LLMConfigError(RuntimeError):
    """Raised when Groq credentials are missing."""


class LLMError(RuntimeError):
    """Raised when the Groq chat call fails."""


def require_llm_settings(settings: Settings | None = None) -> Settings:
    settings = settings or get_settings()
    if not settings.llm_configured:
        raise LLMConfigError(
            "GROQ_API_KEY is not configured. "
            "Set GROQ_API_KEY (and optionally GROQ_MODEL) in the project-root .env."
        )
    return settings


def _get_groq_client(settings: Settings) -> Any:
    """Process-wide Groq client (timeout-bound)."""
    global _client
    try:
        from groq import Groq
    except ImportError as exc:  # pragma: no cover
        raise LLMError("groq package is not installed") from exc

    if _client is None:
        _client = Groq(api_key=settings.groq_api_key, timeout=45.0)
    return _client


def reset_groq_client() -> None:
    """Drop the cached client (tests / shutdown)."""
    global _client
    _client = None


def chat_json(
    *,
    system: str,
    user: str,
    settings: Settings | None = None,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """
    Call Groq chat completions and parse a JSON object from the response.

    Never logs or transmits database credentials.
    """
    settings = require_llm_settings(settings)
    client = _get_groq_client(settings)
    model = settings.groq_model

    try:
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except LLMConfigError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LLMError(f"Groq request failed ({model}): {exc}") from None

    content = (response.choices[0].message.content or "").strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise LLMError("Groq did not return valid JSON") from None
        return json.loads(match.group(0))
