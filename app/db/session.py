"""Async Neon PostgreSQL engine, sessions, and lifecycle helpers."""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def redact_db_error(exc: BaseException) -> str:
    """Strip credentials / DSNs from database error messages."""
    msg = str(exc)
    msg = re.sub(
        r"postgres(?:ql)?(?:\+asyncpg)?://\S+",
        "[REDACTED_DSN]",
        msg,
        flags=re.IGNORECASE,
    )
    msg = re.sub(r"password=[^\s]+", "password=[REDACTED]", msg, flags=re.IGNORECASE)
    return msg


def require_neon_settings(settings: Settings | None = None) -> Settings:
    settings = settings or get_settings()
    # Access property to raise a clear error if missing
    _ = settings.neon_dsn
    return settings


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Return a process-wide async engine with connection pooling."""
    global _engine, _session_factory
    settings = require_neon_settings(settings)
    if _engine is None:
        _engine = create_async_engine(
            settings.async_database_url,
            pool_pre_ping=True,
            # Neon idle connections drop; recycle before typical idle timeout.
            pool_recycle=280,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            connect_args={"timeout": 10},
            # Never echo SQL with bound params that could leak env in logs
            echo=False,
        )
        _session_factory = async_sessionmaker(
            bind=_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _engine


def get_session_factory(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        get_engine(settings)
    assert _session_factory is not None
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: acquire a session and close it cleanly."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope(
    settings: Settings | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Transactional session scope for services / CLI."""
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def ping_postgres(settings: Settings | None = None) -> None:
    """Raise on connectivity failure. Never logs the DSN."""
    engine = get_engine(settings)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Neon PostgreSQL unreachable: {redact_db_error(exc)}") from None


async def dispose_engine() -> None:
    """Clean shutdown of the connection pool."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
