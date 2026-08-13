"""SQL agent tests — validation, read-only execution, scenario coverage."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.sql import SQLAgent, SQLValidationError, validate_sql
from app.config import get_settings
from app.db.session import dispose_engine, get_session_factory, require_neon_settings

# Handcrafted safe SQL for each required scenario (no LLM required)
SCENARIO_SQL = {
    "vehicle_availability": """
        SELECT COUNT(*) AS available_vehicles
        FROM vehicles
        WHERE city = 'London' AND status = 'AVAILABLE'
    """,
    "accessible_vehicles": """
        SELECT COUNT(*) AS accessible_available
        FROM vehicles v
        JOIN zones z ON z.zone_id = v.zone_id
        WHERE v.status = 'AVAILABLE'
          AND v.wheelchair_accessible = TRUE
          AND z.zone_name = 'Heathrow'
    """,
    "trips": """
        SELECT trip_id, city, pickup_zone, dropoff_zone, fare_gbp
        FROM trips
        WHERE city = 'London'
        ORDER BY pickup_time DESC
        LIMIT 10
    """,
    "average_distance": """
        SELECT AVG(distance_miles) AS avg_distance_miles, COUNT(*) AS trip_count
        FROM trips
        WHERE pickup_zone = 'Heathrow'
    """,
    "average_duration": """
        SELECT AVG(duration_minutes) AS avg_duration_minutes, COUNT(*) AS trip_count
        FROM trips
        WHERE city = 'Manchester'
    """,
    "revenue": """
        SELECT COALESCE(SUM(fare_gbp), 0) AS total_revenue_gbp, COUNT(*) AS trip_count
        FROM trips
        WHERE city = 'London'
    """,
    "demand": """
        SELECT city, SUM(demand_requests) AS total_demand
        FROM demand
        GROUP BY city
        ORDER BY total_demand DESC
        LIMIT 5
    """,
    "top_zones": """
        SELECT zone, city, SUM(demand_requests) AS total_demand
        FROM demand
        GROUP BY zone, city
        ORDER BY total_demand DESC
        LIMIT 10
    """,
    "driver_performance": """
        SELECT d.driver_id, d.driver_name, d.rating, COUNT(t.trip_id) AS completed_trips
        FROM drivers d
        LEFT JOIN trips t ON t.driver_id = d.driver_id
        WHERE d.city = 'London'
        GROUP BY d.driver_id, d.driver_name, d.rating
        ORDER BY completed_trips DESC, d.rating DESC
        LIMIT 10
    """,
    "booking_cancellation_rate": """
        SELECT
          COUNT(*) FILTER (WHERE booking_status = 'CANCELLED') AS cancelled,
          COUNT(*) AS total_bookings,
          ROUND(
            100.0 * COUNT(*) FILTER (WHERE booking_status = 'CANCELLED') / NULLIF(COUNT(*), 0),
            2
          ) AS cancellation_rate_pct
        FROM bookings
    """,
}


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
def agent() -> SQLAgent:
    return SQLAgent()


def test_validate_accepts_select() -> None:
    sql = validate_sql("SELECT COUNT(*) FROM vehicles WHERE status = 'AVAILABLE'")
    assert sql.upper().startswith("SELECT")


def test_validate_rejects_insert() -> None:
    with pytest.raises(SQLValidationError):
        validate_sql("INSERT INTO vehicles (vehicle_id) VALUES ('TX-X')")


def test_validate_rejects_drop() -> None:
    with pytest.raises(SQLValidationError):
        validate_sql("DROP TABLE vehicles")


def test_validate_rejects_multi_statement() -> None:
    with pytest.raises(SQLValidationError):
        validate_sql("SELECT 1; DELETE FROM vehicles")


def test_validate_rejects_select_without_from() -> None:
    with pytest.raises(SQLValidationError):
        validate_sql("SELECT pg_sleep(1)")


def test_validate_appends_limit_for_raw_select() -> None:
    sql = validate_sql("SELECT vehicle_id, city FROM vehicles WHERE city = 'London'")
    assert "LIMIT" in sql.upper()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", list(SCENARIO_SQL))
async def test_scenario_execute(
    agent: SQLAgent, session: AsyncSession, scenario: str
) -> None:
    result = await agent.execute_sql(SCENARIO_SQL[scenario], session=session)
    assert result["columns"]
    assert isinstance(result["rows"], list)
    assert result["execution"].row_count >= 0
    # Aggregates / limited lists should return at least one row for this dataset
    assert result["execution"].row_count >= 1


@pytest.mark.asyncio
async def test_london_availability_count(agent: SQLAgent, session: AsyncSession) -> None:
    result = await agent.execute_sql(SCENARIO_SQL["vehicle_availability"], session=session)
    count = result["rows"][0][0]
    assert int(count) == 223


@pytest.mark.asyncio
async def test_summarize_template_without_llm(agent: SQLAgent) -> None:
    # Force template path regardless of key
    summary = SQLAgent._template_summary(
        "How many vehicles?",
        ["available_vehicles"],
        [[223]],
    )
    assert "223" in summary


@pytest.mark.asyncio
async def test_full_agent_run_optional_llm(session: AsyncSession) -> None:
    settings = get_settings()
    if not settings.llm_configured:
        pytest.skip("GROQ_API_KEY not configured")
    agent = SQLAgent(settings=settings)
    result = await agent.run(
        "How many vehicles are available in London?", session=session
    )
    assert result.sql
    assert "SELECT" in result.sql.upper()
    assert result.columns
    assert result.rows
    assert result.summary
    assert 0.0 <= result.confidence <= 1.0
