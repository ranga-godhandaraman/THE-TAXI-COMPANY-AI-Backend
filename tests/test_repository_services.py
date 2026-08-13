"""
Integration tests against the configured Neon database.

Requires NEON_POSTGRES_STRING in the project-root .env and an imported schema.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repository as repo
from app.db.session import dispose_engine, get_session_factory, require_neon_settings
from app.schemas.taxi import (
    BookingSearchParams,
    TripSearchParams,
    VehicleSearchParams,
)
from app.services import taxi_queries


@pytest.fixture(scope="session")
def neon_configured() -> None:
    require_neon_settings()


@pytest_asyncio.fixture
async def session(neon_configured: None):
    factory = get_session_factory()
    async with factory() as sess:
        yield sess
    await dispose_engine()


@pytest.mark.asyncio
async def test_get_vehicle(session: AsyncSession) -> None:
    page = await repo.search_vehicles(session, VehicleSearchParams(limit=1))
    assert page.total > 0
    vehicle = await repo.get_vehicle(session, page.items[0].vehicle_id)
    assert vehicle is not None
    assert vehicle.vehicle_id == page.items[0].vehicle_id


@pytest.mark.asyncio
async def test_search_vehicles(session: AsyncSession) -> None:
    page = await repo.search_vehicles(
        session, VehicleSearchParams(city="London", limit=10)
    )
    assert page.total >= 0
    assert all(v.city == "London" for v in page.items)


@pytest.mark.asyncio
async def test_available_vehicles(session: AsyncSession) -> None:
    page = await repo.get_available_vehicles(session, city="London", limit=10)
    assert all(v.status == "AVAILABLE" for v in page.items)
    assert all(v.city == "London" for v in page.items)


@pytest.mark.asyncio
async def test_get_driver(session: AsyncSession) -> None:
    page = await repo.search_vehicles(session, VehicleSearchParams(limit=1))
    driver = await repo.get_driver(session, page.items[0].driver_id)
    assert driver is not None
    assert driver.driver_id == page.items[0].driver_id


@pytest.mark.asyncio
async def test_get_trip(session: AsyncSession) -> None:
    page = await repo.search_trips(session, TripSearchParams(limit=1))
    assert page.total > 0
    trip = await repo.get_trip(session, page.items[0].trip_id)
    assert trip is not None


@pytest.mark.asyncio
async def test_search_trips(session: AsyncSession) -> None:
    page = await repo.search_trips(
        session, TripSearchParams(city="Manchester", limit=5)
    )
    assert all(t.city == "Manchester" for t in page.items)


@pytest.mark.asyncio
async def test_get_booking(session: AsyncSession) -> None:
    page = await repo.search_bookings(session, BookingSearchParams(limit=1))
    assert page.total > 0
    booking = await repo.get_booking(session, page.items[0].booking_id)
    assert booking is not None


@pytest.mark.asyncio
async def test_search_bookings(session: AsyncSession) -> None:
    page = await repo.search_bookings(
        session, BookingSearchParams(city="London", limit=5)
    )
    assert all(b.city == "London" for b in page.items)


@pytest.mark.asyncio
async def test_get_zone(session: AsyncSession) -> None:
    zone = await repo.get_zone(session, "Z0001")
    assert zone is not None
    assert zone.zone_name == "Heathrow"


@pytest.mark.asyncio
async def test_demand_lookup(session: AsyncSession) -> None:
    rows = await repo.get_demand(session, zone_id="Z0001", limit=5)
    assert rows
    assert all(r.zone_id == "Z0001" for r in rows)


@pytest.mark.asyncio
async def test_fare_lookup(session: AsyncSession) -> None:
    fares = await repo.get_fare_rules(session, city="London")
    assert fares
    assert all(f.city == "London" for f in fares)


@pytest.mark.asyncio
async def test_city_availability(session: AsyncSession) -> None:
    result = await taxi_queries.availability_by_city(session, "London")
    assert result.count >= 0
    assert result.city == "London"
    assert result.status == "AVAILABLE"


@pytest.mark.asyncio
async def test_zone_availability(session: AsyncSession) -> None:
    result = await taxi_queries.availability_by_zone(session, "Z0001")
    assert result.zone_id == "Z0001"
    assert result.count >= 0


@pytest.mark.asyncio
async def test_revenue(session: AsyncSession) -> None:
    result = await taxi_queries.revenue(session, city="London")
    assert result.trip_count > 0
    assert result.total_fare_gbp > 0


@pytest.mark.asyncio
async def test_demand_aggregation(session: AsyncSession) -> None:
    rows = await taxi_queries.demand_by_zone(session, limit=5)
    assert rows
    assert rows[0].total_demand_requests >= rows[-1].total_demand_requests
