"""RAG retrieval integration tests against the existing Qdrant collection."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.rag import retrieve_documents


@pytest.fixture(scope="module")
def qdrant_ready() -> None:
    settings = get_settings()
    if not settings.qdrant_configured:
        pytest.skip("QUAD_ENDPOINT / QUAD_API_KEY not configured")


def test_retrieve_documents_taxi_vs_phv(qdrant_ready: None) -> None:
    result = retrieve_documents(
        query="What is the difference between a taxi and a private hire vehicle?",
        top_k=3,
    )
    assert result.query
    assert len(result.results) >= 1
    top = result.results[0]
    assert top.text
    assert top.source
    assert isinstance(top.score, float)
    assert "taxi_vs_phv" in top.source or "PHV" in top.text or "taxi" in top.text.lower()


def test_retrieve_with_source_filter(qdrant_ready: None) -> None:
    result = retrieve_documents(
        query="cancellation charges",
        top_k=5,
        filters={"source": "cancellation_policy.md"},
    )
    assert all(r.source == "cancellation_policy.md" for r in result.results)


def test_rag_search_endpoint(qdrant_ready: None) -> None:
    client = TestClient(app)
    response = client.post(
        "/api/rag/search",
        json={
            "query": "What is the difference between a taxi and a private hire vehicle?",
            "top_k": 5,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["query"]
    assert isinstance(body["results"], list)
    assert len(body["results"]) >= 1
    hit = body["results"][0]
    assert {"text", "score", "source", "metadata"} <= set(hit.keys())
