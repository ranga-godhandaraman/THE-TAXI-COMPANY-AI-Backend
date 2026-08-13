"""Authentication HTTP endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import clear_session_cookie, set_session_cookie
from app.auth.deps import assert_trusted_origin, get_current_user
from app.auth.rate_limit import check_rate_limit
from app.auth.service import (
    AuthError,
    PublicUser,
    authenticate_user,
    create_user,
    issue_session,
    revoke_session,
)
from app.db.session import get_session
from app.schemas.auth import (
    AuthUserResponse,
    MeResponse,
    SigninRequest,
    SignupRequest,
    UserOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _client_key(request: Request, action: str) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    ip = (forwarded.split(",")[0].strip() if forwarded else None) or (
        request.client.host if request.client else "unknown"
    )
    return f"{action}:{ip}"


def _user_out(user: PublicUser) -> UserOut:
    return UserOut(
        id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
    )


@router.post("/signup", response_model=AuthUserResponse)
async def signup(
    body: SignupRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> AuthUserResponse:
    assert_trusted_origin(request)
    if not check_rate_limit(_client_key(request, "signup"), limit=10, window_seconds=900):
        raise HTTPException(status_code=429, detail="Too many attempts. Please try again later.")
    try:
        user = await create_user(
            session,
            first_name=body.first_name,
            last_name=body.last_name,
            email=body.email,
            password=body.password,
        )
        issued = await issue_session(session, user)
        await session.commit()
    except AuthError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except Exception:  # noqa: BLE001
        await session.rollback()
        logger.exception("signup failed")
        raise HTTPException(status_code=500, detail="Unable to create account.") from None

    set_session_cookie(response, issued.raw_token, expires_at=issued.expires_at)
    return AuthUserResponse(user=_user_out(issued.user))


@router.post("/signin", response_model=AuthUserResponse)
async def signin(
    body: SigninRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> AuthUserResponse:
    assert_trusted_origin(request)
    if not check_rate_limit(_client_key(request, "signin"), limit=20, window_seconds=900):
        raise HTTPException(status_code=429, detail="Too many attempts. Please try again later.")
    try:
        user = await authenticate_user(session, email=body.email, password=body.password)
        issued = await issue_session(session, user)
        await session.commit()
    except AuthError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except Exception:  # noqa: BLE001
        await session.rollback()
        logger.exception("signin failed")
        raise HTTPException(status_code=500, detail="Unable to sign in.") from None

    set_session_cookie(response, issued.raw_token, expires_at=issued.expires_at)
    return AuthUserResponse(user=_user_out(issued.user))


@router.post("/signout")
async def signout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    assert_trusted_origin(request)
    from app.config import get_settings

    token = request.cookies.get(get_settings().auth_cookie_name)
    try:
        await revoke_session(session, token)
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()
        logger.exception("signout failed")
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def me(user: PublicUser | None = Depends(get_current_user)) -> MeResponse:
    if user is None:
        return MeResponse(authenticated=False, user=None)
    return MeResponse(authenticated=True, user=_user_out(user))
