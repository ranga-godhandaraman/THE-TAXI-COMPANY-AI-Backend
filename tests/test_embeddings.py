"""Embedding provider unit tests (no live Qdrant required)."""

from __future__ import annotations

import pytest
from qdrant_client.http import models as qm

from app.config import get_settings
from app.rag.embeddings import (
    EmbeddingError,
    build_search_query,
    embed_query,
    uses_qdrant_cloud_inference,
)


def test_default_provider_is_qdrant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    get_settings.cache_clear()
    assert uses_qdrant_cloud_inference() is True
    get_settings.cache_clear()


def test_build_search_query_returns_document_for_qdrant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "qdrant")
    monkeypatch.setenv("QDRANT_INFERENCE_MODEL", "bge-m3")
    get_settings.cache_clear()

    result = build_search_query("What is a PHV licence?")
    assert isinstance(result, qm.Document)
    assert result.text == "What is a PHV licence?"
    assert result.model == "bge-m3"
    get_settings.cache_clear()


def test_embed_query_rejects_qdrant_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "qdrant")
    get_settings.cache_clear()

    with pytest.raises(EmbeddingError, match="EMBEDDING_PROVIDER=qdrant"):
        embed_query("test query")
    get_settings.cache_clear()


def test_build_search_query_rejects_empty() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        build_search_query("   ")
