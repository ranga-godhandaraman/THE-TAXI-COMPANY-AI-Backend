"""Health check API routes."""

from __future__ import annotations

import time

from fastapi import APIRouter

from app.config import get_settings
from app.db.session import ping_postgres, redact_db_error
from app.rag import QdrantUnavailableError, ping_qdrant
from app.schemas.health import ComponentHealth, HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness: backend process is up."""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        components={
            "api": ComponentHealth(status="ok", detail="Backend is running"),
        },
    )


@router.get("/health/postgres", response_model=HealthResponse)
@router.get("/api/health/db", response_model=HealthResponse)
async def health_postgres() -> HealthResponse:
    """Check Neon PostgreSQL connectivity (structured taxi data)."""
    settings = get_settings()
    if not settings.postgres_configured:
        return HealthResponse(
            status="not_configured",
            service=settings.app_name,
            components={
                "postgres": ComponentHealth(
                    status="not_configured",
                    detail="Set NEON_POSTGRES_STRING in .env",
                ),
            },
        )

    started = time.perf_counter()
    try:
        await ping_postgres(settings)
        latency = (time.perf_counter() - started) * 1000
        return HealthResponse(
            status="ok",
            service=settings.app_name,
            components={
                "postgres": ComponentHealth(
                    status="ok",
                    detail="Neon SELECT 1 succeeded",
                    latency_ms=round(latency, 2),
                ),
            },
        )
    except Exception as exc:  # noqa: BLE001
        latency = (time.perf_counter() - started) * 1000
        return HealthResponse(
            status="error",
            service=settings.app_name,
            components={
                "postgres": ComponentHealth(
                    status="error",
                    detail=redact_db_error(exc),
                    latency_ms=round(latency, 2),
                ),
            },
        )


@router.get("/health/qdrant", response_model=HealthResponse)
def health_qdrant() -> HealthResponse:
    """Read-only Qdrant connectivity check. Does not mutate data."""
    settings = get_settings()
    if not settings.qdrant_configured:
        return HealthResponse(
            status="not_configured",
            service=settings.app_name,
            components={
                "qdrant": ComponentHealth(
                    status="not_configured",
                    detail="Set QUAD_ENDPOINT and QUAD_API_KEY in .env",
                ),
            },
        )

    started = time.perf_counter()
    try:
        info = ping_qdrant(settings)
        latency = (time.perf_counter() - started) * 1000
        return HealthResponse(
            status="ok",
            service=settings.app_name,
            components={
                "qdrant": ComponentHealth(
                    status="ok",
                    detail=(
                        f"Connected to '{info['target_collection']}' "
                        f"({info['points_count']} points). Read-only."
                    ),
                    latency_ms=round(latency, 2),
                ),
            },
        )
    except QdrantUnavailableError as exc:
        latency = (time.perf_counter() - started) * 1000
        return HealthResponse(
            status="error",
            service=settings.app_name,
            components={
                "qdrant": ComponentHealth(
                    status="error",
                    detail=str(exc),
                    latency_ms=round(latency, 2),
                ),
            },
        )
    except Exception as exc:  # noqa: BLE001
        latency = (time.perf_counter() - started) * 1000
        return HealthResponse(
            status="error",
            service=settings.app_name,
            components={
                "qdrant": ComponentHealth(
                    status="error",
                    detail=str(exc),
                    latency_ms=round(latency, 2),
                ),
            },
        )


@router.get("/health/all", response_model=HealthResponse)
async def health_all() -> HealthResponse:
    """Aggregate health for API, PostgreSQL, and Qdrant."""
    settings = get_settings()
    api = health()
    pg = await health_postgres()
    qd = health_qdrant()

    components = {
        **api.components,
        **pg.components,
        **qd.components,
    }

    statuses = {c.status for c in components.values()}
    if "error" in statuses:
        overall: str = "error"
    elif "degraded" in statuses:
        overall = "degraded"
    elif "not_configured" in statuses:
        overall = "degraded"
    else:
        overall = "ok"

    return HealthResponse(
        status=overall,  # type: ignore[arg-type]
        service=settings.app_name,
        components=components,
    )
