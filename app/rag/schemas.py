"""Normalized RAG retrieval schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RetrievalHit(BaseModel):
    text: str
    score: float
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalResponse(BaseModel):
    query: str
    results: list[RetrievalHit]
    collection: str | None = None
    top_k: int | None = None




