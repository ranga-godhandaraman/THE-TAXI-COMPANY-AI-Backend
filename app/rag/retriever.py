"""Read-only document retrieval against the existing Qdrant collection."""

from __future__ import annotations

from typing import Any

from qdrant_client.http import models as qm

from app.config import get_settings
from app.rag.client import (
    QdrantConfigError,
    QdrantUnavailableError,
    get_qdrant_client,
    require_qdrant_settings,
)
from app.rag.embeddings import embed_query
from app.rag.schemas import RetrievalHit, RetrievalResponse

# Payload keys treated as primary fields, not free-form metadata
_PRIMARY_PAYLOAD_KEYS = {"text", "source", "score"}


def _build_filter(filters: dict[str, Any] | None) -> qm.Filter | None:
    if not filters:
        return None

    must: list[qm.FieldCondition] = []
    for key, value in filters.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            must.append(
                qm.FieldCondition(
                    key=key,
                    match=qm.MatchAny(any=list(value)),
                )
            )
        else:
            must.append(
                qm.FieldCondition(
                    key=key,
                    match=qm.MatchValue(value=value),
                )
            )
    if not must:
        return None
    return qm.Filter(must=must)


def _normalize_hit(point: qm.ScoredPoint) -> RetrievalHit:
    payload = dict(point.payload or {})
    text = str(payload.pop("text", "") or "")
    source = str(payload.pop("source", "") or "unknown")
    # Keep remaining payload as metadata (header_path, h1, chunk_index, …)
    metadata = {k: v for k, v in payload.items() if k not in _PRIMARY_PAYLOAD_KEYS}
    score = float(point.score) if point.score is not None else 0.0
    return RetrievalHit(text=text, score=score, source=source, metadata=metadata)


def _matches_filters(payload: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    if not filters:
        return True
    for key, expected in filters.items():
        if expected is None:
            continue
        actual = payload.get(key)
        if isinstance(expected, (list, tuple, set)):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def retrieve_documents(
    query: str,
    top_k: int = 5,
    filters: dict[str, Any] | None = None,
) -> RetrievalResponse:
    """
    Search the existing taxi policy collection.

    Returns normalized hits. Never writes to Qdrant.
    Raises QdrantConfigError / QdrantUnavailableError on infrastructure issues.
    Empty `results` means no relevant documents matched (not an error).
    """
    settings = require_qdrant_settings()
    if top_k < 1:
        raise ValueError("top_k must be >= 1")

    query_text = (query or "").strip()
    if not query_text:
        raise ValueError("query must be a non-empty string")

    collection = settings.qdrant_collection
    vector = embed_query(query_text)
    client = get_qdrant_client(settings)
    qdrant_filter = _build_filter(filters)

    try:
        # Confirm collection exists (read-only) before searching
        names = {c.name for c in client.get_collections().collections}
        if collection not in names:
            raise QdrantUnavailableError(
                f"Collection '{collection}' not found. Available: {sorted(names) or 'none'}"
            )

        try:
            response = client.query_points(
                collection_name=collection,
                query=vector,
                query_filter=qdrant_filter,
                limit=top_k,
                with_payload=True,
                with_vectors=False,
            )
            raw = list(response.points)
        except Exception as filter_exc:  # noqa: BLE001
            # Cloud collections may lack payload indexes; fall back to
            # over-fetch + local filter (still read-only).
            message = str(filter_exc)
            if qdrant_filter is None or "Index required" not in message:
                raise
            response = client.query_points(
                collection_name=collection,
                query=vector,
                query_filter=None,
                limit=max(top_k * 20, 50),
                with_payload=True,
                with_vectors=False,
            )
            raw = [
                point
                for point in response.points
                if _matches_filters(dict(point.payload or {}), filters)
            ][:top_k]
    except (QdrantConfigError, QdrantUnavailableError, ValueError):
        raise
    except Exception as exc:  # noqa: BLE001
        raise QdrantUnavailableError(f"Qdrant search failed: {exc}") from None

    results = [_normalize_hit(point) for point in raw]
    return RetrievalResponse(
        query=query_text,
        results=results,
        collection=collection,
        top_k=top_k,
    )


# Re-export settings helper for callers that only need config awareness
def is_qdrant_configured() -> bool:
    return get_settings().qdrant_configured
