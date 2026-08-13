"""Orchestrator routing + chat acceptance tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.orchestration.routing import decide_route

ACCEPTANCE_ROUTES = [
    ("How many vehicles are available in London?", "sql"),
    ("What is the difference between a taxi and PHV?", "rag"),
    ("Which London zones have unusually low availability?", "analytics"),
    ("Why is Heathrow availability lower than normal?", "hybrid"),
    ("What is the average trip distance from Heathrow?", "sql"),
    ("What are the accessibility requirements?", "rag"),
    ("Compare demand between London and Manchester.", "analytics"),
]


@pytest.mark.parametrize("question,expected", ACCEPTANCE_ROUTES)
def test_deterministic_routes(question: str, expected: str) -> None:
    decision = decide_route(question)
    assert decision.route == expected
    assert decision.reason
    assert decision.agents
    if expected == "hybrid":
        assert "sql" in decision.agents and "analytics" in decision.agents
    else:
        assert decision.agents == (expected,)


@pytest.mark.asyncio
async def test_chat_sql_london_availability() -> None:
    from app.orchestration import run_chat

    result = await run_chat("How many vehicles are available in London?")
    assert result.route == "sql"
    assert result.answer
    assert result.metadata.get("route_selected") == "sql"
    assert "execution_time_ms" in result.metadata
    assert "223" in result.answer or result.data


@pytest.mark.asyncio
async def test_chat_rag_taxi_phv() -> None:
    from app.orchestration import run_chat

    result = await run_chat("What is the difference between a taxi and PHV?")
    assert result.route == "rag"
    assert result.sources
    assert any("taxi_vs_phv" in str(s.get("source", "")) for s in result.sources)


@pytest.mark.asyncio
async def test_chat_analytics_unusual_availability() -> None:
    from app.orchestration import run_chat

    result = await run_chat("Which London zones have unusually low availability?")
    assert result.route == "analytics"
    assert result.answer
    assert result.metadata.get("analysis_type") or result.data is not None


@pytest.mark.asyncio
async def test_chat_hybrid_heathrow() -> None:
    from app.orchestration import run_chat

    result = await run_chat("Why is Heathrow availability lower than normal?")
    assert result.route == "hybrid"
    assert "sql" in (result.metadata.get("agents_selected") or [])
    assert "analytics" in (result.metadata.get("agents_selected") or [])
    assert "SQL" in result.answer or "snapshot" in result.answer.lower()
    assert "analysis" in result.answer.lower() or "Historical" in result.answer


def test_api_chat_endpoint() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"message": "How many vehicles are available in London?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "sql"
    assert body["question"]
    assert body["answer"]
    assert "execution_time_ms" in body["metadata"]
    assert body["metadata"].get("route_reason")
