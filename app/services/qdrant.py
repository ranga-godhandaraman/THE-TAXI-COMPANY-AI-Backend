"""Backward-compatible Qdrant helpers — implementation lives in app.rag."""

from app.rag.client import get_qdrant_client, ping_qdrant, reset_qdrant_client

__all__ = ["get_qdrant_client", "ping_qdrant", "reset_qdrant_client"]
