"""Acceptance tests for the deterministic journey / pricing engine."""

from __future__ import annotations

from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import dispose_engine, get_session_factory, require_neon_settings
from app.pricing import (
    JourneyEstimateRequest,
    LocationResolutionError,
    PricingEngine,
    estimate_journey_fare,
)
from app.pricing.locations import resolve_zone_name, load_zones
from app.pricing.route import HaversineRouteEstimator, haversine_miles
from app.pricing.surge import assess_surge


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
async def test_didsbury_birmingham_3_passengers(session: AsyncSession) -> None:
    result = await estimate_journey_fare(
        session,
        JourneyEstimateRequest(
            pickup="Didsbury",
            destination="Birmingham",
            passengers=3,
        ),
    )
    assert result.estimate_type == "POC_ESTIMATE"
    assert result.currency == "GBP"
    assert result.estimated_distance_miles > 50
    classes = {o.vehicle_class for o in result.vehicle_options}
    assert classes == {"SEDAN", "SUV", "EXECUTIVE"}
    for opt in result.vehicle_options:
        assert opt.estimated_min_gbp > 0
        assert opt.estimated_max_gbp >= opt.estimated_min_gbp
        assert opt.base_fare > 0
        assert opt.city_multiplier > 0


@pytest.mark.asyncio
async def test_heathrow_westminster_2_passengers(session: AsyncSession) -> None:
    result = await estimate_journey_fare(
        session,
        JourneyEstimateRequest(
            pickup="Heathrow",
            destination="Westminster",
            passengers=2,
        ),
    )
    assert "Heathrow" in result.pickup or result.pickup_zone_id == "Z0001"
    assert result.destination_zone_id  # Westminster
    classes = {o.vehicle_class for o in result.vehicle_options}
    assert "SEDAN" in classes
    assert "SUV" in classes
    assert "EXECUTIVE" in classes
    # London city multiplier
    assert all(o.city_multiplier == pytest.approx(1.15) for o in result.vehicle_options)


@pytest.mark.asyncio
async def test_manchester_airport_birmingham_6(session: AsyncSession) -> None:
    result = await estimate_journey_fare(
        session,
        JourneyEstimateRequest(
            pickup="Manchester Airport",
            destination="Birmingham",
            passengers=6,
        ),
    )
    classes = {o.vehicle_class for o in result.vehicle_options}
    assert classes == {"XL"}
    assert "7-Seater" in result.vehicle_options[0].display_name or result.vehicle_options[0].vehicle_class == "XL"


@pytest.mark.asyncio
async def test_leeds_bradford_to_leeds_centre_7(session: AsyncSession) -> None:
    result = await estimate_journey_fare(
        session,
        JourneyEstimateRequest(
            pickup="Leeds Bradford Airport",
            destination="Leeds City Centre",
            passengers=7,
        ),
    )
    assert {o.vehicle_class for o in result.vehicle_options} == {"XL"}
    assert result.estimated_distance_miles > 0
    assert result.estimated_duration_minutes >= 1


@pytest.mark.asyncio
async def test_heathrow_westminster_peak_hour(session: AsyncSession) -> None:
    # Wednesday 08:00 → WEEKDAY_MORNING peak
    when = datetime(2026, 8, 12, 8, 0, 0)
    result = await estimate_journey_fare(
        session,
        JourneyEstimateRequest(
            pickup="Heathrow",
            destination="Westminster",
            passengers=2,
            requested_datetime=when,
        ),
    )
    assert result.peak_multiplier == pytest.approx(1.1)
    assert result.peak_rule_id == "WEEKDAY_MORNING"
    off_peak = await estimate_journey_fare(
        session,
        JourneyEstimateRequest(
            pickup="Heathrow",
            destination="Westminster",
            passengers=2,
            requested_datetime=datetime(2026, 8, 12, 11, 0, 0),
        ),
    )
    assert off_peak.peak_multiplier == pytest.approx(1.0)
    # Peak should inflate duration and/or max fare vs off-peak
    peak_sedan = next(o for o in result.vehicle_options if o.vehicle_class == "SEDAN")
    off_sedan = next(o for o in off_peak.vehicle_options if o.vehicle_class == "SEDAN")
    assert peak_sedan.estimated_max_gbp >= off_sedan.estimated_max_gbp


@pytest.mark.asyncio
async def test_high_demand_low_availability_surge(session: AsyncSession) -> None:
    result = await estimate_journey_fare(
        session,
        JourneyEstimateRequest(
            pickup="Heathrow",
            destination="Westminster",
            passengers=1,
            available_vehicles=20,
            demand_requests=100,  # ratio 0.2 → CRITICAL
        ),
    )
    assert result.surge_state == "CRITICAL"
    assert result.surge_multiplier == pytest.approx(1.35)
    assert result.availability_ratio == pytest.approx(0.2)

    tight = await assess_surge(
        session, zone_id="Z0001", available_vehicles=80, demand_requests=100
    )
    assert tight.state == "TIGHT"
    assert tight.multiplier == pytest.approx(1.1)


@pytest.mark.asyncio
async def test_accessible_vehicle_requirement(session: AsyncSession) -> None:
    result = await estimate_journey_fare(
        session,
        JourneyEstimateRequest(
            pickup="Heathrow",
            destination="Westminster",
            passengers=2,
            accessibility_required=True,
        ),
    )
    assert result.vehicle_options
    assert all(o.vehicle_class == "ACCESSIBLE" for o in result.vehicle_options)


@pytest.mark.asyncio
async def test_executive_vehicle_request(session: AsyncSession) -> None:
    result = await estimate_journey_fare(
        session,
        JourneyEstimateRequest(
            pickup="Didsbury",
            destination="Birmingham",
            passengers=3,
            vehicle_class="EXECUTIVE",
        ),
    )
    assert len(result.vehicle_options) == 1
    assert result.vehicle_options[0].vehicle_class == "EXECUTIVE"
    assert result.vehicle_options[0].pricing_tier == "PREMIUM"


@pytest.mark.asyncio
async def test_location_resolution_case_insensitive(session: AsyncSession) -> None:
    zones = await load_zones(session)
    a = resolve_zone_name("manchester airport", zones)
    b = resolve_zone_name("Manchester Airport", zones)
    assert a.zone_id == b.zone_id
    central = resolve_zone_name("Central London", zones)
    assert central.zone_name == "Westminster"


@pytest.mark.asyncio
async def test_unresolved_location_structured_error(session: AsyncSession) -> None:
    with pytest.raises(LocationResolutionError) as ei:
        await estimate_journey_fare(
            session,
            JourneyEstimateRequest(
                pickup="Atlantis Harbour",
                destination="Westminster",
                passengers=1,
            ),
        )
    err = ei.value.to_dict()
    assert err["error"] == "location_resolution_failed"
    assert err["field"] == "pickup"


def test_haversine_deterministic() -> None:
    # Heathrow ≈ 51.47,-0.4543 ; Westminster ≈ 51.4975,-0.1357
    miles = haversine_miles(51.47, -0.4543, 51.4975, -0.1357)
    assert 10 < miles < 20
    est = HaversineRouteEstimator()
    from app.pricing.models import ResolvedLocation

    pickup = ResolvedLocation(
        raw="Heathrow",
        zone_id="Z0001",
        zone_name="Heathrow",
        city="London",
        latitude=51.47,
        longitude=-0.4543,
    )
    dest = ResolvedLocation(
        raw="Westminster",
        zone_id="Z0002",
        zone_name="Westminster",
        city="London",
        latitude=51.4975,
        longitude=-0.1357,
    )
    route = est.estimate(pickup, dest, road_distance_multiplier=1.18)
    assert route.estimated_road_miles == round(route.straight_line_miles * 1.18, 2)
    assert route.method == "haversine_x_road_multiplier"
