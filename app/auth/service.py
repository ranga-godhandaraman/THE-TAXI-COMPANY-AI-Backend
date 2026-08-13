"""Auth domain service — users + hashed sessions in Neon PostgreSQL."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import session_expiry
from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.auth.tokens import generate_session_token, hash_session_token
from app.db.models import AuthSession, User

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class PublicUser:
    id: str
    email: str
    first_name: str
    last_name: str


@dataclass(frozen=True)
class SessionIssued:
    user: PublicUser
    raw_token: str
    expires_at: datetime


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_email(email: str) -> str:
    normalized = normalize_email(email)
    if not normalized or len(normalized) > 320 or not _EMAIL_RE.match(normalized):
        raise AuthError("Please enter a valid email address.")
    return normalized


def validate_password(password: str) -> str:
    if password is None or len(password) < 8:
        raise AuthError("Password must be at least 8 characters.")
    if len(password) > 200:
        raise AuthError("Password is too long.")
    if password.strip() == "":
        raise AuthError("Password must be at least 8 characters.")
    # Reject trivially weak / blank-padded only
    if password.lower() in {"password", "password1", "12345678", "qwertyui"}:
        raise AuthError("Please choose a stronger password.")
    return password


def validate_name(value: str, field: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned or len(cleaned) > 100:
        raise AuthError(f"Please enter a valid {field}.")
    return cleaned


def to_public_user(user: User) -> PublicUser:
    return PublicUser(
        id=user.id,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
    )


async def create_user(
    session: AsyncSession,
    *,
    first_name: str,
    last_name: str,
    email: str,
    password: str,
) -> User:
    email_n = validate_email(email)
    password_v = validate_password(password)
    first = validate_name(first_name, "first name")
    last = validate_name(last_name, "last name")

    existing = await session.scalar(select(User).where(User.email == email_n))
    if existing is not None:
        raise AuthError("An account with this email already exists.", status_code=409)

    now = datetime.now(timezone.utc)
    user = User(
        id=str(uuid.uuid4()),
        email=email_n,
        password_hash=hash_password(password_v),
        first_name=first,
        last_name=last,
        is_active=True,
        email_verified=False,
        created_at=now,
        updated_at=now,
        last_login_at=None,
    )
    session.add(user)
    await session.flush()
    return user


async def authenticate_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
) -> User:
    email_n = validate_email(email)
    # Always validate password shape lightly without leaking
    if not password or len(password) > 200:
        raise AuthError("Invalid email or password.", status_code=401)

    user = await session.scalar(select(User).where(User.email == email_n))
    if user is None or not user.is_active:
        # Constant-ish work: hash a dummy if missing
        if user is None:
            hash_password("dummy-password-for-timing")
        raise AuthError("Invalid email or password.", status_code=401)

    if not verify_password(user.password_hash, password):
        raise AuthError("Invalid email or password.", status_code=401)

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        user.updated_at = datetime.now(timezone.utc)

    return user


async def issue_session(session: AsyncSession, user: User) -> SessionIssued:
    now = datetime.now(timezone.utc)
    expires = session_expiry()
    raw = generate_session_token()
    row = AuthSession(
        id=str(uuid.uuid4()),
        user_id=user.id,
        token_hash=hash_session_token(raw),
        expires_at=expires,
        created_at=now,
        revoked_at=None,
        last_used_at=now,
    )
    user.last_login_at = now
    user.updated_at = now
    session.add(row)
    await session.flush()
    return SessionIssued(user=to_public_user(user), raw_token=raw, expires_at=expires)


async def resolve_session(
    session: AsyncSession,
    raw_token: str | None,
) -> tuple[User, AuthSession] | None:
    if not raw_token:
        return None
    token_hash = hash_session_token(raw_token)
    row = await session.scalar(
        select(AuthSession).where(AuthSession.token_hash == token_hash)
    )
    if row is None:
        return None
    now = datetime.now(timezone.utc)
    if row.revoked_at is not None:
        return None
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= now:
        return None

    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None

    row.last_used_at = now
    await session.flush()
    return user, row


async def revoke_session(session: AsyncSession, raw_token: str | None) -> bool:
    if not raw_token:
        return False
    token_hash = hash_session_token(raw_token)
    row = await session.scalar(
        select(AuthSession).where(AuthSession.token_hash == token_hash)
    )
    if row is None:
        return False
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        await session.flush()
    return True
