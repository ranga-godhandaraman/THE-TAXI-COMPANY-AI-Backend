"""Agent HTTP endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analytics import (
    AnalyticsAgent,
    AnalyticsAgentRequest,
    AnalyticsAgentResponse,
)
from app.agents.llm import LLMConfigError, LLMError
from app.agents.rag import RAGAgent, RagAgentRequest, RagAgentResponse
from app.agents.sql import SQLAgent, SQLAgentRequest, SQLAgentResponse, SQLValidationError
from app.auth import require_user
from app.config import get_settings
from app.db.session import get_session
from app.rag import QdrantConfigError, QdrantUnavailableError

router = APIRouter(
    prefix="/api/agents",
    tags=["agents"],
    dependencies=[Depends(require_user)],
)


@router.post("/sql", response_model=SQLAgentResponse)
async def run_sql_agent(
    body: SQLAgentRequest,
    session: AsyncSession = Depends(get_session),
) -> SQLAgentResponse:
    """
    Natural-language → validated read-only SQL → Neon results + summary.

    Does not use RAG. Never returns database credentials.
    """
    settings = get_settings()
    agent = SQLAgent(settings=settings)
    try:
        result = await agent.run(body.question, session=session)
    except LLMConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except SQLValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid SQL: {exc}") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None

    return SQLAgentResponse(
        question=body.question,
        intent=result.intent,
        sql=result.sql,
        result={"columns": result.columns, "rows": result.rows},
        summary=result.summary,
        confidence=result.confidence,
        execution=result.execution,
    )


@router.post("/rag", response_model=RagAgentResponse)
def run_rag_agent(body: RagAgentRequest) -> RagAgentResponse:
    """
    Policy/reference Q&A grounded in the existing Qdrant knowledge base.

    Uses retrieve_documents() only — no ingest, no new collections.
    """
    settings = get_settings()
    agent = RAGAgent(settings=settings)
    try:
        result = agent.run(body.question, top_k=body.top_k)
    except LLMConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except (QdrantConfigError, QdrantUnavailableError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    return RagAgentResponse(
        question=body.question,
        answer=result.answer,
        sources=result.sources,
        confidence=result.confidence,
    )


@router.post("/analytics", response_model=AnalyticsAgentResponse)
async def run_analytics_agent(
    body: AnalyticsAgentRequest,
    session: AsyncSession = Depends(get_session),
) -> AnalyticsAgentResponse:
    """
    Operational analytics: trends, comparisons, anomalies.

    Metrics come only from PostgreSQL + transparent Python statistics.
    """
    settings = get_settings()
    agent = AnalyticsAgent(settings=settings)
    try:
        result = await agent.run(body.question, session=session)
    except LLMConfigError as exc:
        # Classification can fall back to heuristics; still surface if execute fails oddly
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from None

    return AnalyticsAgentResponse(
        question=body.question,
        analysis_type=result.analysis_type,
        summary=result.summary,
        metrics=result.metrics,
        observations=result.observations,
        recommendations=result.recommendations,
        data=result.data,
    )
