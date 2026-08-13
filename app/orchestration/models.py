"""Chat / orchestration response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=100)


class ChatResponse(BaseModel):
    question: str
    conversation_id: str | None = None
    route: str
    answer: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    data: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
