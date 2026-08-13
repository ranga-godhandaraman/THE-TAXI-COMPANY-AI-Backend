"""Request / response models for the deterministic pricing engine."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class LocationResolutionError(ValueError):
    """Structured failure when a place cannot be matched to a zone."""

    def __init__(self, field: str, raw: str, message: str):
        self.field = field
        self.raw = raw
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "location_resolution_failed",
            "field": self.field,
            "raw": self.raw,
            "message": self.message,
        }


class ResolvedLocation(BaseModel):
    raw: str
    zone_id: str
    zone_name: str
    city: str
    latitude: float
    longitude: float


class JourneyEstimateRequest(BaseModel):
    pickup: str
    destination: str
    passengers: int = Field(..., ge=1, le=16)
    vehicle_class: str | None = None
    requested_datetime: datetime | None = None
    accessibility_required: bool = False
    # Optional live overrides — if omitted, engine may read Neon demand
    available_vehicles: int | None = None
    demand_requests: int | None = None
    pickup_zone_id: str | None = None


class VehicleFareOption(BaseModel):
    vehicle_class: str
    display_name: str
    pricing_tier: str
    estimated_distance_miles: float
    estimated_duration_minutes: int
    base_fare: float
    distance_fare: float
    time_fare: float
    city_multiplier: float
    peak_multiplier: float
    surge_multiplier: float
    surge_state: str
    subtotal: float
    adjusted_fare: float
    estimated_min_gbp: float
    estimated_max_gbp: float


class JourneyEstimateResult(BaseModel):
    pickup: str
    destination: str
    pickup_zone_id: str
    destination_zone_id: str
    pickup_city: str
    destination_city: str
    passengers: int
    estimated_distance_miles: float
    estimated_duration_minutes: int
    peak_rule_id: str | None
    peak_multiplier: float
    surge_state: str
    surge_multiplier: float
    availability_ratio: float | None
    vehicle_options: list[VehicleFareOption]
    estimate_type: str = "POC_ESTIMATE"
    currency: str = "GBP"
    notes: list[str] = Field(default_factory=list)
