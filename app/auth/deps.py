"""FastAPI auth dependencies."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import PublicUser, resolve_session, to_public_user
from app.config import get_settings
from app.db.session import get_session


def _read_session_cookie(request: Request) -> str | None:
    settings = get_settings()
    return request.cookies.get(settings.auth_cookie_name)


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> PublicUser | None:
    """Return the authenticated user, or None if unauthenticated."""
    token = _read_session_cookie(request)
    resolved = await resolve_session(session, token)
    if resolved is None:
        return None
    user, _ = resolved
    await session.commit()
    return to_public_user(user)


async def require_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> PublicUser:
    """Require an authenticated active user."""
    token = _read_session_cookie(request)
    resolved = await resolve_session(session, token)
    if resolved is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    user, _ = resolved
    await session.commit()
    return to_public_user(user)


def assert_trusted_origin(request: Request) -> None:
    """Basic CSRF mitigation for cookie-authenticated state-changing requests."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    settings = get_settings()
    origin = request.headers.get("origin")
    if not origin:
        # Same-origin navigations / non-browser clients may omit Origin
        return
    allowed = set(settings.cors_origin_list)
    if origin not in allowed:
        raise HTTPException(status_code=403, detail="Origin not allowed.")
