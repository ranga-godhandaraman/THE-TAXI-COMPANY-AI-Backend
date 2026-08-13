"""Route estimation interface — Haversine POC, swappable for a real router later."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from app.pricing.models import ResolvedLocation


@dataclass(frozen=True)
class RouteEstimate:
    straight_line_miles: float
    estimated_road_miles: float
    method: str


class RouteEstimator(Protocol):
    def estimate(
        self,
        pickup: ResolvedLocation,
        destination: ResolvedLocation,
        *,
        road_distance_multiplier: float,
    ) -> RouteEstimate: ...


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles."""
    radius = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(min(1.0, a)))


class HaversineRouteEstimator:
    """POC estimator: straight-line × configured road multiplier."""

    def estimate(
        self,
        pickup: ResolvedLocation,
        destination: ResolvedLocation,
        *,
        road_distance_multiplier: float,
    ) -> RouteEstimate:
        straight = haversine_miles(
            pickup.latitude,
            pickup.longitude,
            destination.latitude,
            destination.longitude,
        )
        road = round(straight * road_distance_multiplier, 2)
        return RouteEstimate(
            straight_line_miles=round(straight, 2),
            estimated_road_miles=road,
            method="haversine_x_road_multiplier",
        )
