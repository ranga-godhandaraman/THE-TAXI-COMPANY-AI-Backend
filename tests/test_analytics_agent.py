"""Analytics agent tests — deterministic metrics from Neon."""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analytics import AnalyticsAgent
from app.config import get_settings
from app.db.session import dispose_engine, get_session_factory, require_neon_settings
from app.main import app


@pytest.fixture(scope="module")
def neon_ready() -> None:
    require_neon_settings()


@pytest_asyncio.fixture
async def session(neon_ready: None):
    factory = get_session_factory()
    async with factory() as sess:
        yield sess
    await dispose_engine()


@pytest.fixture
def agent() -> AnalyticsAgent:
    return AnalyticsAgent()


@pytest.mark.asyncio
async def test_peak_hours_london(agent: AnalyticsAgent, session: AsyncSession) -> None:
    plan = {
        "analysis_type": "peak_hours",
        "city": "London",
        "cities": None,
        "zone_name": None,
        "hour": None,
    }
    result = await agent.execute(plan, session)
    assert result["analysis_type"] == "peak_hours"
    assert result["data"]
    assert "avg_demand" in result["data"][0]
    assert result["metrics"]["busiest_hours"]


@pytest.mark.asyncio
async def test_availability_anomaly_heathrow(
    agent: AnalyticsAgent, session: AsyncSession
) -> None:
    plan = {
        "analysis_type": "availability_anomaly",
        "city": "London",
        "zone_name": "Heathrow",
        "cities": None,
        "hour": None,
    }
    result = await agent.execute(plan, session)
    assert result["analysis_type"] == "availability_anomaly"
    assert result["data"]
    point = result["data"][0]
    assert {"timestamp", "available", "expected", "deviation_pct"} <= set(point)
    assert "z_score" in result["metrics"] or "latest_z_score" in result["metrics"]


@pytest.mark.asyncio
async def test_city_comparison(agent: AnalyticsAgent, session: AsyncSession) -> None:
    plan = {
        "analysis_type": "city_comparison",
        "city": None,
        "cities": ["London", "Manchester"],
        "zone_name": None,
        "hour": None,
    }
    result = await agent.execute(plan, session)
    assert result["analysis_type"] == "city_comparison"
    cities = {row["city"] for row in result["data"]}
    assert "London" in cities and "Manchester" in cities


@pytest.mark.asyncio
async def test_demand_supply_gap(agent: AnalyticsAgent, session: AsyncSession) -> None:
    plan = {
        "analysis_type": "demand_supply_gap",
        "city": None,
        "cities": None,
        "zone_name": None,
        "hour": None,
    }
    result = await agent.execute(plan, session)
    assert result["data"]
    assert "demand_supply_gap" in result["data"][0]


@pytest.mark.asyncio
async def test_normal_snapshot_vs_anomaly_types(
    agent: AnalyticsAgent, session: AsyncSession
) -> None:
    snap = await agent.execute(
        {
            "analysis_type": "normal_snapshot",
            "city": "London",
            "zone_name": None,
            "cities": None,
            "hour": 8,
        },
        session,
    )
    assert snap["analysis_type"] == "normal_snapshot"
    assert snap["metrics"]["hour"] == 8
    assert snap["metrics"]["avg_demand"] is not None

    anom = await agent.execute(
        {
            "analysis_type": "demand_anomaly",
            "city": "London",
            "zone_name": None,
            "cities": None,
            "hour": None,
        },
        session,
    )
    assert anom["analysis_type"] == "demand_anomaly"


@pytest.mark.asyncio
async def test_end_to_end_question(agent: AnalyticsAgent, session: AsyncSession) -> None:
    result = await agent.run(
        "Compare taxi demand between London and Manchester.", session=session
    )
    assert result.analysis_type in {"city_comparison", "demand_trend"}
    assert result.summary
    assert isinstance(result.data, list)


def test_api_analytics_endpoint(neon_ready: None) -> None:
    client = TestClient(app)
    response = client.post(
        "/api/agents/analytics",
        json={"question": "What are the busiest hours in London?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["analysis_type"]
    assert "summary" in body
    assert "metrics" in body
    assert "observations" in body
    assert "recommendations" in body
    assert isinstance(body["data"], list)
