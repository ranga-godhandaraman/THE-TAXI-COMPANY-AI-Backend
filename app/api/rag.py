"""RAG search test endpoint — retrieval only, no answer generation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_user
from app.rag import (
    QdrantConfigError,
    QdrantUnavailableError,
    RagSearchRequest,
    RagSearchResponse,
    retrieve_documents,
)

router = APIRouter(
    prefix="/api/rag",
    tags=["rag"],
    dependencies=[Depends(require_user)],
)


def _search(query: str, top_k: int, filters: dict | None) -> RagSearchResponse:
    try:
        result = retrieve_documents(query=query, top_k=top_k, filters=filters)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except QdrantConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except QdrantUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None

    return RagSearchResponse(query=result.query, results=result.results)


@router.post("/search", response_model=RagSearchResponse)
def rag_search_post(body: RagSearchRequest) -> RagSearchResponse:
    """Semantic search over existing policy documents in Qdrant."""
    return _search(body.query, body.top_k, body.filters)


@router.get("/search", response_model=RagSearchResponse)
def rag_search_get(
    query: str = Query(..., min_length=1),
    top_k: int = Query(5, ge=1, le=50),
    source: str | None = Query(
        default=None,
        description="Optional metadata filter on payload.source (e.g. taxi_vs_phv.md)",
    ),
) -> RagSearchResponse:
    filters = {"source": source} if source else None
    return _search(query, top_k, filters)
