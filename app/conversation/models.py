"""Conversational state and intent models (internal — never shown raw to users)."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Intent(str, Enum):
    FARE_ESTIMATE = "FARE_ESTIMATE"
    JOURNEY_ESTIMATE = "JOURNEY_ESTIMATE"
    VEHICLE_AVAILABILITY = "VEHICLE_AVAILABILITY"
    NEARBY_VEHICLES = "NEARBY_VEHICLES"
    VEHICLE_SEARCH = "VEHICLE_SEARCH"
    ACCESSIBILITY_SEARCH = "ACCESSIBILITY_SEARCH"
    BOOKING_REQUEST = "BOOKING_REQUEST"
    TRIP_LOOKUP = "TRIP_LOOKUP"
    POLICY = "POLICY"
    OPERATIONS_ANALYSIS = "OPERATIONS_ANALYSIS"
    DEMAND_ANALYSIS = "DEMAND_ANALYSIS"
    FLEET_SEARCH = "FLEET_SEARCH"
    GENERAL_TAXI_QUERY = "GENERAL_TAXI_QUERY"
    UNKNOWN = "UNKNOWN"


class LocationRef(BaseModel):
    raw: str | None = None
    resolved: str | None = None
    zone_id: str | None = None
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    confidence: float = 0.0
    needs_clarification: bool = False
    clarification_prompt: str | None = None


class ConversationState(BaseModel):
    """Sticky slot-filling state across turns."""

    intent: Intent = Intent.UNKNOWN
    pickup: LocationRef | None = None
    destination: LocationRef | None = None
    passengers: int | None = None
    vehicle_type: str | None = None
    accessibility_required: bool = False
    min_seats: int | None = None
    requested_time: str | None = None
    date: str | None = None
    last_domain: str | None = None
    pending_clarification: str | None = None


class DomainResult(BaseModel):
    domain: str
    summary: str
    data: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    estimate: bool = False
    error: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)


class TurnResult(BaseModel):
    conversation_id: str
    answer: str
    route: str
    intent: Intent
    state: ConversationState
    sources: list[dict[str, Any]] = Field(default_factory=list)
    data: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
