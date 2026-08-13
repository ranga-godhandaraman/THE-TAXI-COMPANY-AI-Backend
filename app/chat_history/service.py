"""Chat history service — persistence around the existing AI pipeline."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import PublicUser
from app.chat_history.titles import generate_session_title
from app.db import chat_repository as repo
from app.db.models import ChatMessage, ChatSession
from app.orchestration import ChatResponse, run_chat


def session_to_dict(row: ChatSession) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def message_to_dict(row: ChatMessage) -> dict[str, Any]:
    return {
        "id": row.id,
        "role": row.role,
        "content": row.content,
        "metadata": row.metadata_json,
        "created_at": row.created_at.isoformat(),
    }


async def create_chat_session(db: AsyncSession, user: PublicUser) -> dict[str, Any]:
    row = await repo.create_session(db, user_id=user.id)
    await db.commit()
    await db.refresh(row)
    return session_to_dict(row)


async def list_chat_sessions(db: AsyncSession, user: PublicUser) -> list[dict[str, Any]]:
    rows = await repo.list_sessions_for_user(db, user_id=user.id)
    return [session_to_dict(r) for r in rows]


async def get_chat_session(
    db: AsyncSession,
    user: PublicUser,
    session_id: str,
) -> dict[str, Any]:
    row = await repo.get_owned_session(db, session_id=session_id, user_id=user.id)
    if row is None:
        # Do not reveal whether the id exists for another user
        raise HTTPException(status_code=404, detail="Session not found.")
    messages = await repo.list_messages(db, session_id=row.id)
    return {
        **session_to_dict(row),
        "messages": [message_to_dict(m) for m in messages],
    }


async def post_session_message(
    db: AsyncSession,
    user: PublicUser,
    session_id: str,
    content: str,
) -> ChatResponse:
    """
    Persist user message → existing AI pipeline → persist assistant reply.

    Uses session_id as LangGraph conversation_id (in-memory slot state stays
    separate from PostgreSQL transcript storage).
    """
    text = (content or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message content is required.")

    chat_session = await repo.get_owned_session(
        db, session_id=session_id, user_id=user.id
    )
    if chat_session is None:
        raise HTTPException(status_code=404, detail="Session not found.")

    user_count = await repo.count_user_messages(db, session_id=session_id)
    await repo.add_message(
        db,
        session_id=session_id,
        role="user",
        content=text,
        metadata=None,
    )

    # First meaningful user message → deterministic title
    if user_count == 0 and chat_session.title in {"New Booking", "new booking"}:
        title = generate_session_title(text)
        await repo.update_session_title(db, session_id=session_id, title=title)

    await db.commit()

    try:
        response = await run_chat(text, conversation_id=session_id)
    except Exception:
        # User message already persisted — re-raise for API error mapping
        raise

    assistant_meta = {
        "route": response.route,
        "sources": response.sources,
        "data": response.data,
        "response_metadata": response.metadata,
        "conversation_id": response.conversation_id,
    }
    await repo.add_message(
        db,
        session_id=session_id,
        role="assistant",
        content=response.answer,
        metadata=assistant_meta,
    )
    await db.commit()

    # Ensure client sees the durable session id
    response.conversation_id = session_id
    return response


async def save_local_exchange(
    db: AsyncSession,
    user: PublicUser,
    session_id: str,
    user_content: str,
    assistant_content: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist a UI-only exchange (e.g. small-talk) without calling the AI."""
    chat_session = await repo.get_owned_session(
        db, session_id=session_id, user_id=user.id
    )
    if chat_session is None:
        raise HTTPException(status_code=404, detail="Session not found.")

    user_count = await repo.count_user_messages(db, session_id=session_id)
    await repo.add_message(
        db, session_id=session_id, role="user", content=user_content.strip()
    )
    if user_count == 0 and chat_session.title in {"New Booking", "new booking"}:
        await repo.update_session_title(
            db,
            session_id=session_id,
            title=generate_session_title(user_content),
        )
    await repo.add_message(
        db,
        session_id=session_id,
        role="assistant",
        content=assistant_content,
        metadata=metadata or {"local": True, "small_talk": True},
    )
    await db.commit()
