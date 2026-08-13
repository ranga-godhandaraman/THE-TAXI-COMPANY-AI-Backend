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


class RagSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    filters: dict[str, Any] | None = None


class RagSearchResponse(BaseModel):
    query: str
    results: list[RetrievalHit]
