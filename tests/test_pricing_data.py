"""Acceptance tests for the synthetic vehicle / pricing data layer."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import dispose_engine, get_session_factory, require_neon_settings
from app.services import pricing as pricing_service


@pytest.fixture(scope="module")
def neon_ready() -> None:
    require_neon_settings()


@pytest_asyncio.fixture
async def session(neon_ready: None):
    factory = get_session_factory()
    async with factory() as sess:
        yield sess
    await dispose_engine()


@pytest.mark.asyncio
async def test_eligible_vehicles_3_passengers(session: AsyncSession) -> None:
    classes = await pricing_service.find_vehicle_classes_for_passengers(session, 3)
    ids = {c.vehicle_class_id for c in classes}
    names = {c.display_name for c in classes}
    assert ids == {"SEDAN", "SUV", "EXECUTIVE"}
    assert "Standard Sedan" in names
    assert "SUV" in names
    assert "Executive" in names


@pytest.mark.asyncio
async def test_eligible_vehicles_6_passengers(session: AsyncSession) -> None:
    classes = await pricing_service.find_vehicle_classes_for_passengers(session, 6)
    ids = {c.vehicle_class_id for c in classes}
    assert ids == {"XL"}
    assert classes[0].display_name == "XL / 7-Seater"


@pytest.mark.asyncio
async def test_eligible_vehicles_7_passengers(session: AsyncSession) -> None:
    classes = await pricing_service.find_vehicle_classes_for_passengers(session, 7)
    ids = {c.vehicle_class_id for c in classes}
    assert ids == {"XL"}
    assert "7-Seater" in classes[0].display_name or classes[0].vehicle_class_id == "XL"


@pytest.mark.asyncio
async def test_accessible_vehicle_classes(session: AsyncSession) -> None:
    classes = await pricing_service.find_accessible_vehicle_classes(session)
    assert classes
    assert all(c.wheelchair_accessible for c in classes)
    assert any(c.vehicle_class_id == "ACCESSIBLE" for c in classes)


@pytest.mark.asyncio
async def test_standard_fare_rules(session: AsyncSession) -> None:
    rule = await pricing_service.get_fare_rule(session, "STANDARD")
    assert rule is not None
    assert rule.pricing_tier == "STANDARD"
    assert float(rule.base_fare_gbp) == 5.5
    assert float(rule.included_distance_miles) == 3.0
    assert float(rule.per_mile_gbp) == 1.75
    assert float(rule.per_minute_gbp) == 0.28


@pytest.mark.asyncio
async def test_xl_fare_rules(session: AsyncSession) -> None:
    rule = await pricing_service.get_fare_rule(session, "XL")
    assert rule is not None
    assert rule.pricing_tier == "XL"
    assert float(rule.base_fare_gbp) == 8.0
    assert float(rule.per_mile_gbp) == 2.35


@pytest.mark.asyncio
async def test_london_city_multiplier(session: AsyncSession) -> None:
    mod = await pricing_service.get_city_modifier(session, "London")
    assert mod is not None
    assert float(mod.city_multiplier) == 1.15


@pytest.mark.asyncio
async def test_peak_hour_rules(session: AsyncSession) -> None:
    morning = await pricing_service.get_peak_rule(session, "WEEKDAY_MORNING")
    assert morning is not None
    assert morning.start_hour == 7
    assert morning.end_hour == 9
    assert float(morning.multiplier) == 1.1
    all_rules = await pricing_service.get_peak_rule(session)
    assert isinstance(all_rules, list)
    assert len(all_rules) >= 3


@pytest.mark.asyncio
async def test_surge_pricing_configuration(session: AsyncSession) -> None:
    normal = await pricing_service.get_surge_rule(session, "NORMAL")
    assert normal is not None
    assert float(normal.multiplier) == 1.0
    critical = await pricing_service.get_surge_rule(session, "CRITICAL")
    assert critical is not None
    assert float(critical.multiplier) == 1.35
    all_states = await pricing_service.get_surge_rule(session)
    assert isinstance(all_states, list)
    assert {s.state for s in all_states} >= {"NORMAL", "TIGHT", "HIGH_DEMAND", "CRITICAL"}


@pytest.mark.asyncio
async def test_vehicle_catalog_and_config(session: AsyncSession) -> None:
    catalog = await pricing_service.get_vehicle_catalog(session, limit=10)
    assert len(catalog) == 10
    cfg = await pricing_service.get_pricing_config(session, "road_distance_multiplier")
    assert cfg is not None
    assert cfg.value == "1.18"
    rules = await pricing_service.get_vehicle_selection_rules(session)
    assert len(rules) == 6
