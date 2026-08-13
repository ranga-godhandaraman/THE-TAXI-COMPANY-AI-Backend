"""RAG agent tests — grounding, operational rejection, API shape."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agents.rag import RAGAgent
from app.agents.rag.agent import INSUFFICIENT_MSG, OPERATIONAL_MSG
from app.config import get_settings
from app.main import app


@pytest.fixture(scope="module")
def rag_ready() -> None:
    settings = get_settings()
    if not settings.qdrant_configured:
        pytest.skip("QUAD_ENDPOINT / QUAD_API_KEY not configured")
    if not settings.llm_configured:
        pytest.skip("GROQ_API_KEY not configured")


def test_rejects_operational_question() -> None:
    agent = RAGAgent()
    assert agent.is_operational_question(
        "How many vehicles are available in London?"
    )
    result = agent.run("How many vehicles are available in London?")
    assert result.sources == []
    assert "operational" in result.answer.lower() or result.answer == OPERATIONAL_MSG
    assert result.confidence == 0.0


def test_taxi_vs_phv_grounded(rag_ready: None) -> None:
    agent = RAGAgent()
    result = agent.run("What is the difference between a taxi and PHV?")
    assert result.answer
    assert result.answer != INSUFFICIENT_MSG
    assert result.sources
    assert any("taxi_vs_phv" in s.source for s in result.sources)
    assert result.confidence > 0.0
    # Answer should mention retrieved source or PHV content
    joined = (result.answer + " " + " ".join(s.source for s in result.sources)).lower()
    assert "phv" in joined or "private hire" in joined or "taxi_vs_phv" in joined


def test_cancellation_policy(rag_ready: None) -> None:
    agent = RAGAgent()
    result = agent.run("What is the cancellation policy?")
    assert result.sources
    assert any("cancellation" in s.source for s in result.sources)
    assert result.answer


def test_api_rag_endpoint(rag_ready: None) -> None:
    client = TestClient(app)
    response = client.post(
        "/api/agents/rag",
        json={"question": "What is the difference between a taxi and PHV?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "answer" in body
    assert "sources" in body
    assert "confidence" in body
    assert isinstance(body["sources"], list)
    assert body["sources"]
    assert {"source", "score"} <= set(body["sources"][0].keys())
