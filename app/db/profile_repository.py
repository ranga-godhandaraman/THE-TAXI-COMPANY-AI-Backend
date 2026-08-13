"""User profile persistence (separate from auth identity)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserProfile


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_profile_row(
    session: AsyncSession, *, user_id: str
) -> UserProfile | None:
    return await session.scalar(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )


async def get_or_create_profile(
    session: AsyncSession, *, user_id: str
) -> UserProfile:
    row = await get_profile_row(session, user_id=user_id)
    if row is not None:
        return row
    now = _utcnow()
    row = UserProfile(
        id=str(uuid.uuid4()),
        user_id=user_id,
        phone_number=None,
        date_of_birth=None,
        address_line_1=None,
        address_line_2=None,
        city=None,
        postcode=None,
        country="United Kingdom",
        profile_image_url=None,
        preferred_vehicle_type=None,
        special_requirements=None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def get_user(session: AsyncSession, *, user_id: str) -> User | None:
    return await session.scalar(select(User).where(User.id == user_id))


async def update_user_names(
    session: AsyncSession,
    *,
    user_id: str,
    first_name: str,
    last_name: str,
) -> User | None:
    user = await get_user(session, user_id=user_id)
    if user is None:
        return None
    user.first_name = first_name
    user.last_name = last_name
    user.updated_at = _utcnow()
    await session.flush()
    return user


async def apply_profile_fields(
    row: UserProfile,
    fields: dict[str, Any],
) -> UserProfile:
    for key, value in fields.items():
        if hasattr(row, key):
            setattr(row, key, value)
    row.updated_at = _utcnow()
    return row
