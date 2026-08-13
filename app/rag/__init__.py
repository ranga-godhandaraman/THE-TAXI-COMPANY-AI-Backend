"""RAG module — read-only Qdrant retrieval for taxi policy documents."""

from app.rag.client import (
    QdrantConfigError,
    QdrantUnavailableError,
    get_qdrant_client,
    ping_qdrant,
    reset_qdrant_client,
)
from app.rag.retriever import is_qdrant_configured, retrieve_documents
from app.rag.schemas import RagSearchRequest, RagSearchResponse, RetrievalHit, RetrievalResponse

__all__ = [
    "QdrantConfigError",
    "QdrantUnavailableError",
    "RagSearchRequest",
    "RagSearchResponse",
    "RetrievalHit",
    "RetrievalResponse",
    "get_qdrant_client",
    "is_qdrant_configured",
    "ping_qdrant",
    "reset_qdrant_client",
    "retrieve_documents",
]
