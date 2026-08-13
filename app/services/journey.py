"""Deterministic journey distance / duration / fare estimation (POC).

Isolated so a real routing API can replace haversine + road factor later.
Does NOT require historical trips rows.
"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.conversation.models import ConversationState, DomainResult, LocationRef
from app.db import repository as repo

ROAD_FACTOR = 1.35
# Blended average speed for UK intercity / urban mix (mph)
AVG_SPEED_MPH = 45.0
# Fare range band around the point estimate
FARE_LOW_FACTOR = 0.88
FARE_HIGH_FACTOR = 1.18

# Vehicle type multipliers applied on top of Saloon city rules when no exact fare row
VEHICLE_MULTIPLIER = {
    "Saloon": 1.0,
    "Black Cab": 1.15,
    "Executive": 1.45,
    "MPV": 1.35,
}


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def estimate_route_miles(
    pickup: LocationRef, destination: LocationRef
) -> float | None:
    if (
        pickup.latitude is None
        or pickup.longitude is None
        or destination.latitude is None
        or destination.longitude is None
    ):
        return None
    straight = haversine_miles(
        pickup.latitude,
        pickup.longitude,
        destination.latitude,
        destination.longitude,
    )
    return round(straight * ROAD_FACTOR, 1)


def estimate_duration_minutes(distance_miles: float) -> int:
    if distance_miles <= 0:
        return 5
    return max(5, int(round((distance_miles / AVG_SPEED_MPH) * 60)))


async def estimate_journey(
    session: AsyncSession,
    state: ConversationState,
    *,
    want_fare: bool = True,
) -> DomainResult:
    pickup = state.pickup
    destination = state.destination
    if not pickup or not pickup.resolved or not destination or not destination.resolved:
        return DomainResult(
            domain="journey",
            summary="I still need a clear pickup and destination to estimate that journey.",
            error=False,
            meta={"needs_locations": True},
        )

    distance = estimate_route_miles(pickup, destination)
    if distance is None:
        return DomainResult(
            domain="journey",
            summary="I couldn't estimate the route for those locations yet.",
            error=True,
        )

    duration = estimate_duration_minutes(distance)
    vehicle = state.vehicle_type or "Saloon"
    fare_city = pickup.city or destination.city or "Manchester"

    fare_min = fare_max = None
    if want_fare:
        fare_min, fare_max = await _fare_range(
            session,
            city=fare_city,
            vehicle_type=vehicle,
            distance_miles=distance,
            duration_minutes=duration,
            passengers=state.passengers,
            min_seats=state.min_seats,
        )

    pickup_label = pickup.resolved or ""
    if pickup.city and pickup.resolved and pickup.resolved.lower() in {"city centre", "city center"}:
        pickup_label = f"{pickup.city} {pickup.resolved}"
    dest_label = destination.resolved or ""
    if destination.city and destination.resolved and destination.resolved.lower() in {
        "city centre",
        "city center",
    }:
        dest_label = f"{destination.city} {destination.resolved}"

    payload: dict[str, Any] = {
        "pickup": pickup.resolved,
        "pickup_city": pickup.city,
        "destination": destination.resolved,
        "destination_city": destination.city,
        "estimated_distance_miles": distance,
        "estimated_duration_minutes": duration,
        "vehicle_type": vehicle,
        "passengers": state.passengers,
        "currency": "GBP",
        "is_estimate": True,
    }
    if fare_min is not None and fare_max is not None:
        payload["estimated_fare_min"] = fare_min
        payload["estimated_fare_max"] = fare_max

    if want_fare and fare_min is not None:
        summary = (
            f"Estimated journey from {pickup_label} to {dest_label}: "
            f"about {distance:g} miles, roughly {duration} minutes, "
            f"fare around £{fare_min:.0f}–£{fare_max:.0f} ({vehicle}). "
            "This is an estimate, not a live quote."
        )
    else:
        summary = (
            f"Estimated journey from {pickup_label} to {dest_label}: "
            f"about {distance:g} miles, roughly {duration} minutes. "
            "This is an estimate based on typical road distance."
        )

    return DomainResult(
        domain="journey",
        summary=summary,
        data=[payload],
        estimate=True,
        meta={"analysis_type": "journey_estimate"},
    )


async def _fare_range(
    session: AsyncSession,
    *,
    city: str,
    vehicle_type: str,
    distance_miles: float,
    duration_minutes: int,
    passengers: int | None,
    min_seats: int | None,
) -> tuple[float, float]:
    rules = await repo.get_fare_rules(session, city=city)
    if not rules:
        # Fallback to Manchester Saloon-like defaults
        base, per_mile, per_min = 3.5, 2.1, 0.22
    else:
        # Prefer exact vehicle type; else Saloon; else first
        match = next((r for r in rules if r.vehicle_type == vehicle_type), None)
        if match is None:
            match = next((r for r in rules if r.vehicle_type == "Saloon"), rules[0])
        base = float(match.base_fare_gbp)
        per_mile = float(match.per_mile_gbp)
        per_min = float(match.per_minute_gbp)
        # If requested Executive/MPV but only Saloon in table, apply multiplier
        if match.vehicle_type != vehicle_type:
            mult = VEHICLE_MULTIPLIER.get(vehicle_type, 1.0)
            base *= mult
            per_mile *= mult
            per_min *= mult

    point = base + distance_miles * per_mile + duration_minutes * per_min
    if min_seats and min_seats >= 6:
        point *= 1.1
    if passengers and passengers >= 5:
        point *= 1.05

    low = round(point * FARE_LOW_FACTOR, 0)
    high = round(point * FARE_HIGH_FACTOR, 0)
    if high < low:
        high = low
    return float(low), float(high)
