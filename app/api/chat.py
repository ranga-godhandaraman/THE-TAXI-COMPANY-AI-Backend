"""Unified chat endpoint — conversational assistant over domain services."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.agents.llm import LLMConfigError, LLMError
from app.auth import require_user
from app.auth.service import PublicUser
from app.orchestration import ChatRequest, ChatResponse, run_chat
from app.rag.client import QdrantConfigError, QdrantUnavailableError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    _user: PublicUser = Depends(require_user),
) -> ChatResponse:
    """
    Natural-language taxi/PHV assistant.

    Maintains conversation_id state for follow-ups. Reuses SQL / RAG /
    Analytics / Journey services under a conversational understanding layer.
    """
    try:
        return await run_chat(body.message, conversation_id=body.conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except (LLMConfigError, QdrantConfigError) as exc:
        logger.warning("chat config error: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="A required service is not configured. Please try again later.",
        ) from None
    except (LLMError, QdrantUnavailableError) as exc:
        logger.warning("chat upstream error: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="An upstream service failed. Please retry your question.",
        ) from None
    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="The request timed out. Please try a simpler question or retry.",
        ) from None
    except Exception:  # noqa: BLE001
        logger.exception("chat failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to process that request. Please try again.",
        ) from None
