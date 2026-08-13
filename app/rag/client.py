"""Qdrant client — read-only connectivity for the RAG module."""

from __future__ import annotations

from qdrant_client import QdrantClient

from app.config import Settings, get_settings

_client: QdrantClient | None = None


class QdrantConfigError(RuntimeError):
    """Raised when QUAD_ENDPOINT / QUAD_API_KEY are missing."""


class QdrantUnavailableError(RuntimeError):
    """Raised when Qdrant cannot be reached or the collection is missing."""


def require_qdrant_settings(settings: Settings | None = None) -> Settings:
    settings = settings or get_settings()
    missing: list[str] = []
    if not settings.quad_endpoint:
        missing.append("QUAD_ENDPOINT")
    if not settings.quad_api_key:
        missing.append("QUAD_API_KEY")
    if missing:
        raise QdrantConfigError(
            f"Missing required Qdrant environment variable(s): {', '.join(missing)}"
        )
    return settings


def get_qdrant_client(settings: Settings | None = None) -> QdrantClient:
    """
    Shared read-only Qdrant client.

    Must never create collections, upsert points, embed documents into the
    store, or delete data.
    """
    global _client
    settings = require_qdrant_settings(settings)
    if _client is None:
        _client = QdrantClient(
            url=settings.quad_endpoint,
            api_key=settings.quad_api_key,
            timeout=20,
        )
    return _client


def ping_qdrant(settings: Settings | None = None) -> dict:
    """
    Read-only connectivity check.

    Lists collections and confirms the configured collection exists.
    Does not mutate any data.
    """
    settings = require_qdrant_settings(settings)
    try:
        client = get_qdrant_client(settings)
        collections = client.get_collections().collections
    except QdrantConfigError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise QdrantUnavailableError(f"Qdrant unreachable: {exc}") from None

    names = sorted(c.name for c in collections)
    collection = settings.qdrant_collection
    points_count: int | None = None
    if collection in names:
        info = client.get_collection(collection)
        points_count = info.points_count
    else:
        raise QdrantUnavailableError(
            f"Collection '{collection}' not found. Available: {names or 'none'}"
        )

    return {
        "collections": names,
        "target_collection": collection,
        "target_present": True,
        "points_count": points_count,
    }


def reset_qdrant_client() -> None:
    """Drop the cached client (tests / shutdown)."""
    global _client
    _client = None
