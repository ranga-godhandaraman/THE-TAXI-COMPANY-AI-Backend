"""Deterministic taxi journey + pricing engine (no LLM)."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import pricing_repository as repo
from app.pricing.locations import load_zones, resolve_zone_name
from app.pricing.models import (
    JourneyEstimateRequest,
    JourneyEstimateResult,
    LocationResolutionError,
    VehicleFareOption,
)
from app.pricing.route import HaversineRouteEstimator, RouteEstimator
from app.pricing.surge import assess_surge
from app.pricing.vehicles import select_vehicle_classes
from app.schemas.pricing import PeakRuleOut


class PricingEngine:
    """
    Pure rules engine over Neon pricing tables.

    Formula (per vehicle class):
      distance_charge = max(0, distance - included_distance) * per_mile
      time_charge     = duration * per_minute
      subtotal        = base + distance_charge + time_charge
      adjusted        = subtotal * city * peak * surge
      min/max         = adjusted * fare_min/max multipliers
    """

    def __init__(self, route_estimator: RouteEstimator | None = None):
        self.route_estimator = route_estimator or HaversineRouteEstimator()

    async def estimate(
        self, session: AsyncSession, request: JourneyEstimateRequest
    ) -> JourneyEstimateResult:
        zones = await load_zones(session)
        try:
            pickup = resolve_zone_name(request.pickup, zones)
        except LocationResolutionError as exc:
            raise LocationResolutionError("pickup", request.pickup, exc.message) from None
        try:
            destination = resolve_zone_name(request.destination, zones)
        except LocationResolutionError as exc:
            raise LocationResolutionError(
                "destination", request.destination, exc.message
            ) from None

        config = await self._load_config(session)
        route = self.route_estimator.estimate(
            pickup,
            destination,
            road_distance_multiplier=config["road_distance_multiplier"],
        )
        distance = route.estimated_road_miles

        when = request.requested_datetime or datetime.now(timezone.utc).replace(tzinfo=None)
        peak_rule, peak_mult = await self._peak_multiplier(session, when)

        # Duration from distance / average speed, with peak traffic adjustment
        base_hours = distance / config["average_speed_mph"] if distance > 0 else 0
        duration_hours = base_hours * peak_mult
        duration_minutes = max(1, int(round(duration_hours * 60)))

        surge = await assess_surge(
            session,
            zone_id=request.pickup_zone_id or pickup.zone_id,
            available_vehicles=request.available_vehicles,
            demand_requests=request.demand_requests,
        )

        city_mod = await repo.get_city_modifier(session, pickup.city)
        city_mult = float(city_mod.city_multiplier) if city_mod else 1.0

        classes = await select_vehicle_classes(
            session,
            passengers=request.passengers,
            accessibility_required=request.accessibility_required,
            preferred_vehicle_class=request.vehicle_class,
        )
        if not classes:
            raise ValueError(
                f"No eligible vehicle classes for {request.passengers} passengers"
                + (" with accessibility required" if request.accessibility_required else "")
            )

        options: list[VehicleFareOption] = []
        for vc in classes:
            fare = await repo.get_fare_rule(session, vc.pricing_tier)
            if fare is None:
                continue
            option = self._price_class(
                vehicle_class=vc.vehicle_class_id,
                display_name=vc.display_name,
                pricing_tier=vc.pricing_tier,
                base_fare=float(fare.base_fare_gbp),
                included_distance=float(fare.included_distance_miles),
                per_mile=float(fare.per_mile_gbp),
                per_minute=float(fare.per_minute_gbp),
                distance=distance,
                duration_minutes=duration_minutes,
                city_mult=city_mult,
                peak_mult=peak_mult,
                surge_mult=surge.multiplier,
                surge_state=surge.state,
                fare_min_mult=config["fare_min_multiplier"],
                fare_max_mult=config["fare_max_multiplier"],
            )
            options.append(option)

        if not options:
            raise ValueError("No fare rules found for eligible vehicle classes")

        notes = [
            "Distance is a POC estimate (haversine × road multiplier), not live road routing.",
            f"Route method: {route.method}",
            f"Surge source: {surge.source}",
        ]

        # Friendly display labels
        pickup_label = pickup.zone_name
        dest_label = (
            f"{destination.city} City Centre"
            if destination.zone_name.lower() == "city centre"
            else destination.zone_name
        )

        return JourneyEstimateResult(
            pickup=pickup_label,
            destination=dest_label,
            pickup_zone_id=pickup.zone_id,
            destination_zone_id=destination.zone_id,
            pickup_city=pickup.city,
            destination_city=destination.city,
            passengers=request.passengers,
            estimated_distance_miles=distance,
            estimated_duration_minutes=duration_minutes,
            peak_rule_id=peak_rule.rule_id if peak_rule else None,
            peak_multiplier=peak_mult,
            surge_state=surge.state,
            surge_multiplier=surge.multiplier,
            availability_ratio=surge.availability_ratio,
            vehicle_options=options,
            estimate_type="POC_ESTIMATE",
            currency="GBP",
            notes=notes,
        )

    def _price_class(
        self,
        *,
        vehicle_class: str,
        display_name: str,
        pricing_tier: str,
        base_fare: float,
        included_distance: float,
        per_mile: float,
        per_minute: float,
        distance: float,
        duration_minutes: int,
        city_mult: float,
        peak_mult: float,
        surge_mult: float,
        surge_state: str,
        fare_min_mult: float,
        fare_max_mult: float,
    ) -> VehicleFareOption:
        distance_fare = max(0.0, distance - included_distance) * per_mile
        time_fare = duration_minutes * per_minute
        subtotal = base_fare + distance_fare + time_fare
        adjusted = subtotal * city_mult * peak_mult * surge_mult
        est_min = _money(adjusted * fare_min_mult)
        est_max = _money(adjusted * fare_max_mult)
        if est_max < est_min:
            est_max = est_min
        return VehicleFareOption(
            vehicle_class=vehicle_class,
            display_name=display_name,
            pricing_tier=pricing_tier,
            estimated_distance_miles=round(distance, 2),
            estimated_duration_minutes=duration_minutes,
            base_fare=round(base_fare, 2),
            distance_fare=round(distance_fare, 2),
            time_fare=round(time_fare, 2),
            city_multiplier=round(city_mult, 4),
            peak_multiplier=round(peak_mult, 4),
            surge_multiplier=round(surge_mult, 4),
            surge_state=surge_state,
            subtotal=round(subtotal, 2),
            adjusted_fare=round(adjusted, 2),
            estimated_min_gbp=est_min,
            estimated_max_gbp=est_max,
        )

    async def _load_config(self, session: AsyncSession) -> dict[str, float]:
        rows = await repo.get_pricing_config(session)
        assert isinstance(rows, list)
        raw = {r.key: r.value for r in rows}
        return {
            "road_distance_multiplier": float(raw.get("road_distance_multiplier", "1.18")),
            "average_speed_mph": float(raw.get("average_speed_mph", "32.0")),
            "fare_min_multiplier": float(raw.get("fare_min_multiplier", "0.9")),
            "fare_max_multiplier": float(raw.get("fare_max_multiplier", "1.15")),
        }

    async def _peak_multiplier(
        self, session: AsyncSession, when: datetime
    ) -> tuple[PeakRuleOut | None, float]:
        rules = await repo.get_peak_rule(session)
        assert isinstance(rules, list)
        if not rules:
            return None, 1.0

        hour = when.hour
        weekday = when.weekday()  # Mon=0 … Sun=6
        is_weekend = weekday >= 5

        candidates: list[PeakRuleOut] = []
        for rule in rules:
            rid = rule.rule_id.upper()
            if is_weekend and "WEEKEND" not in rid:
                continue
            if not is_weekend and "WEEKEND" in rid:
                continue
            if _hour_in_window(hour, rule.start_hour, rule.end_hour):
                candidates.append(rule)

        if not candidates:
            return None, 1.0
        # Highest multiplier wins if overlapping
        best = max(candidates, key=lambda r: float(r.multiplier))
        return best, float(best.multiplier)


def _hour_in_window(hour: int, start: int, end: int) -> bool:
    """Inclusive start, exclusive end (e.g. 7–9 → 7,8)."""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    # overnight window
    return hour >= start or hour < end


def _money(value: float) -> float:
    """Round fare bounds to whole pounds for the POC estimate range."""
    return float(int(round(value)))


async def estimate_journey_fare(
    session: AsyncSession,
    request: JourneyEstimateRequest,
    *,
    engine: PricingEngine | None = None,
) -> JourneyEstimateResult:
    """Convenience entrypoint for the deterministic pricing engine."""
    return await (engine or PricingEngine()).estimate(session, request)
