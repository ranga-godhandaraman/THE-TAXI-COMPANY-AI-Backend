"""LangGraph orchestration package."""

from __future__ import annotations

from typing import Any

from app.orchestration.models import ChatRequest, ChatResponse
from app.orchestration.routing import RouteDecision, decide_route


async def run_chat(message: str, **kwargs: Any) -> ChatResponse:
    """Delegate to the conversational understanding layer."""
    from app.conversation.graph import run_chat as conversational_run_chat

    return await conversational_run_chat(message, **kwargs)


__all__ = [
    "ChatRequest",
    "ChatResponse",
    "RouteDecision",
    "decide_route",
    "run_chat",
]
