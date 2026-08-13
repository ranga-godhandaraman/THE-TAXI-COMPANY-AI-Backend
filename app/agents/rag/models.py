"""RAG agent request/response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RagAgentRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)


class RagSource(BaseModel):
    source: str
    score: float


class RagAgentResult(BaseModel):
    answer: str
    sources: list[RagSource] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class RagAgentResponse(BaseModel):
    question: str
    answer: str
    sources: list[RagSource] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
