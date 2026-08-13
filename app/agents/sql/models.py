"""SQL Agent result models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SQLAgentRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)


class SQLExecutionMeta(BaseModel):
    row_count: int
    truncated: bool = False
    elapsed_ms: float | None = None
    statement_timeout_ms: int | None = None


class SQLAgentResult(BaseModel):
    intent: str
    sql: str
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    execution: SQLExecutionMeta | None = None


class SQLAgentResponse(BaseModel):
    question: str
    intent: str
    sql: str
    result: dict[str, Any]
    summary: str
    confidence: float
    execution: SQLExecutionMeta | None = None
