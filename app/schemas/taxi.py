"""Pydantic schemas for structured taxi data."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class VehicleOut(ORMModel):
    vehicle_id: str
    registration: str
    driver_id: str
    city: str
    vehicle_type: str
    make: str
    model: str
    fuel_type: str
    seats: int
    wheelchair_accessible: bool
    zone_id: str
    current_lat: float
    current_lon: float
    status: str
    last_status_update: datetime


class DriverOut(ORMModel):
    driver_id: str
    driver_name: str
    city: str
    licence_type: str
    licence_status: str
    rating: Decimal
    years_experience: int
    wheelchair_training: bool


class TripOut(ORMModel):
    trip_id: str
    vehicle_id: str
    driver_id: str
    city: str
    pickup_time: datetime
    distance_miles: Decimal
    duration_minutes: int
    dropoff_time: datetime
    pickup_zone_id: str
    pickup_zone: str
    dropoff_zone_id: str
    dropoff_zone: str
    pickup_lat: float
    pickup_lon: float
    dropoff_lat: float
    dropoff_lon: float
    passenger_count: int
    fare_gbp: Decimal
    payment_type: str
    status: str


class BookingOut(ORMModel):
    booking_id: str
    trip_id: str
    city: str
    booking_time: datetime
    requested_pickup_time: datetime
    booking_channel: str
    booking_status: str
    passenger_count: int


class ZoneOut(ORMModel):
    zone_id: str
    city: str
    zone_name: str
    latitude: float
    longitude: float
    zone_type: str


class DemandOut(ORMModel):
    timestamp: datetime
    zone_id: str
    city: str
    zone: str
    demand_requests: int
    available_vehicles: int
    unserved_requests: int
    demand_index: Decimal


class FareOut(ORMModel):
    city: str
    vehicle_type: str
    base_fare_gbp: Decimal
    per_mile_gbp: Decimal
    per_minute_gbp: Decimal


class VehicleSearchParams(BaseModel):
    city: str | None = None
    status: str | None = None
    zone_id: str | None = None
    vehicle_type: str | None = None
    wheelchair_accessible: bool | None = None
    driver_id: str | None = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class TripSearchParams(BaseModel):
    city: str | None = None
    vehicle_id: str | None = None
    driver_id: str | None = None
    pickup_zone_id: str | None = None
    dropoff_zone_id: str | None = None
    status: str | None = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class BookingSearchParams(BaseModel):
    city: str | None = None
    trip_id: str | None = None
    booking_status: str | None = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class CountResult(BaseModel):
    count: int
    city: str | None = None
    zone_id: str | None = None
    zone_name: str | None = None
    wheelchair_accessible: bool | None = None
    status: str | None = None


class AvgMetric(BaseModel):
    city: str | None = None
    average: Decimal | float | None
    sample_size: int


class RevenueResult(BaseModel):
    city: str | None = None
    total_fare_gbp: Decimal
    trip_count: int


class ZoneDemandResult(BaseModel):
    zone_id: str
    zone_name: str
    city: str
    total_demand_requests: int
    total_available_vehicles: int
    total_unserved_requests: float | int
    avg_demand_index: Decimal | float | None


class ZoneAvailabilityResult(BaseModel):
    zone_id: str
    zone_name: str
    city: str
    available_vehicles: int


class StatusDistribution(BaseModel):
    status: str
    count: int


class PaginatedVehicles(BaseModel):
    items: list[VehicleOut]
    total: int
    limit: int
    offset: int


class PaginatedTrips(BaseModel):
    items: list[TripOut]
    total: int
    limit: int
    offset: int


class PaginatedBookings(BaseModel):
    items: list[BookingOut]
    total: int
    limit: int
    offset: int
