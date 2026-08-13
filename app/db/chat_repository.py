"""Chat session / message persistence (user-visible history only)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatMessage, ChatSession

DEFAULT_SESSION_TITLE = "New Booking"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def create_session(
    session: AsyncSession,
    *,
    user_id: str,
    title: str = DEFAULT_SESSION_TITLE,
) -> ChatSession:
    now = _utcnow()
    row = ChatSession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        title=title[:60] if title else DEFAULT_SESSION_TITLE,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def list_sessions_for_user(
    session: AsyncSession,
    *,
    user_id: str,
) -> list[ChatSession]:
    result = await session.scalars(
        select(ChatSession)
        .where(ChatSession.user_id == user_id)
        .order_by(ChatSession.updated_at.desc())
    )
    return list(result.all())


async def get_owned_session(
    session: AsyncSession,
    *,
    session_id: str,
    user_id: str,
) -> ChatSession | None:
    """Return the session only when it belongs to user_id (ownership enforced)."""
    return await session.scalar(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id,
        )
    )


async def list_messages(
    session: AsyncSession,
    *,
    session_id: str,
) -> list[ChatMessage]:
    result = await session.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
    )
    return list(result.all())


async def add_message(
    session: AsyncSession,
    *,
    session_id: str,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> ChatMessage:
    now = _utcnow()
    row = ChatMessage(
        id=str(uuid.uuid4()),
        session_id=session_id,
        role=role,
        content=content,
        metadata_json=metadata,
        created_at=now,
    )
    session.add(row)
    await session.execute(
        update(ChatSession)
        .where(ChatSession.id == session_id)
        .values(updated_at=now)
    )
    await session.flush()
    return row


async def update_session_title(
    session: AsyncSession,
    *,
    session_id: str,
    title: str,
) -> None:
    await session.execute(
        update(ChatSession)
        .where(ChatSession.id == session_id)
        .values(title=title[:60], updated_at=_utcnow())
    )
    await session.flush()


async def count_user_messages(
    session: AsyncSession,
    *,
    session_id: str,
) -> int:
    result = await session.scalars(
        select(ChatMessage.id).where(
            ChatMessage.session_id == session_id,
            ChatMessage.role == "user",
        )
    )
    return len(list(result.all()))
