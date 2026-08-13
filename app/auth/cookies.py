"""HTTP-only session cookie helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Response

from app.config import Settings, get_settings

COOKIE_PATH = "/"


def _cookie_samesite(settings: Settings) -> str:
    """Cross-site frontends (e.g. Render UI → Render API) need SameSite=None + Secure."""
    return "none" if settings.cookie_secure else "lax"


def set_session_cookie(
    response: Response,
    token: str,
    *,
    expires_at: datetime,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    max_age = max(0, int((expires_at - datetime.now(timezone.utc)).total_seconds()))
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=_cookie_samesite(settings),
        max_age=max_age,
        expires=expires_at,
        path=COOKIE_PATH,
    )


def clear_session_cookie(
    response: Response,
    *,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=_cookie_samesite(settings),
    )


def session_expiry(*, settings: Settings | None = None) -> datetime:
    settings = settings or get_settings()
    return datetime.now(timezone.utc) + timedelta(days=settings.auth_session_days)
