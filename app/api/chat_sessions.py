"""Authenticated chat session history APIs."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import LLMConfigError, LLMError
from app.auth import require_user
from app.auth.service import PublicUser
from app.chat_history import service as history
from app.db.session import get_session as get_db_session
from app.orchestration import ChatResponse
from app.rag.client import QdrantConfigError, QdrantUnavailableError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat-history"])


class CreateSessionResponse(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class SessionSummary(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class SessionMessageOut(BaseModel):
    id: str
    role: str
    content: str
    metadata: dict | None = None
    created_at: str


class SessionDetailResponse(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[SessionMessageOut]


class PostMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_chat_session_endpoint(
    user: PublicUser = Depends(require_user),
    db: AsyncSession = Depends(get_db_session),
) -> CreateSessionResponse:
    data = await history.create_chat_session(db, user)
    return CreateSessionResponse(**data)


@router.get("/sessions", response_model=list[SessionSummary])
async def list_chat_sessions_endpoint(
    user: PublicUser = Depends(require_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[SessionSummary]:
    rows = await history.list_chat_sessions(db, user)
    return [SessionSummary(**r) for r in rows]


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_chat_session_endpoint(
    session_id: str,
    user: PublicUser = Depends(require_user),
    db: AsyncSession = Depends(get_db_session),
) -> SessionDetailResponse:
    data = await history.get_chat_session(db, user, session_id)
    return SessionDetailResponse(
        id=data["id"],
        title=data["title"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        messages=[SessionMessageOut(**m) for m in data["messages"]],
    )


@router.post(
    "/sessions/{session_id}/messages",
    response_model=ChatResponse,
)
async def post_chat_session_message(
    session_id: str,
    body: PostMessageRequest,
    user: PublicUser = Depends(require_user),
    db: AsyncSession = Depends(get_db_session),
) -> ChatResponse:
    try:
        return await history.post_session_message(
            db, user, session_id, body.content
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except (LLMConfigError, QdrantConfigError):
        logger.warning("chat-history config error")
        raise HTTPException(
            status_code=503,
            detail="A required service is not configured. Please try again later.",
        ) from None
    except (LLMError, QdrantUnavailableError):
        logger.warning("chat-history upstream error")
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
        logger.exception("chat-history message failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to process that request. Please try again.",
        ) from None
