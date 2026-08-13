"""SQLAlchemy ORM models for the UK taxi structured dataset."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Zone(Base):
    __tablename__ = "zones"

    zone_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    zone_name: Mapped[str] = mapped_column(Text, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    zone_type: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("idx_zones_city", "city"),
        Index("idx_zones_zone_name", "zone_name"),
        Index("idx_zones_zone_type", "zone_type"),
    )


class Driver(Base):
    __tablename__ = "drivers"

    driver_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    driver_name: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    licence_type: Mapped[str] = mapped_column(Text, nullable=False)
    licence_status: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    years_experience: Mapped[int] = mapped_column(Integer, nullable=False)
    wheelchair_training: Mapped[bool] = mapped_column(Boolean, nullable=False)

    __table_args__ = (
        Index("idx_drivers_city", "city"),
        Index("idx_drivers_licence_status", "licence_status"),
        Index("idx_drivers_licence_type", "licence_type"),
        Index("idx_drivers_rating", "rating"),
    )


class Vehicle(Base):
    __tablename__ = "vehicles"

    vehicle_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    registration: Mapped[str] = mapped_column(Text, nullable=False)
    driver_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("drivers.driver_id"), nullable=False
    )
    city: Mapped[str] = mapped_column(Text, nullable=False)
    vehicle_type: Mapped[str] = mapped_column(Text, nullable=False)
    make: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    fuel_type: Mapped[str] = mapped_column(Text, nullable=False)
    seats: Mapped[int] = mapped_column(Integer, nullable=False)
    wheelchair_accessible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    zone_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("zones.zone_id"), nullable=False
    )
    current_lat: Mapped[float] = mapped_column(Float, nullable=False)
    current_lon: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    last_status_update: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index("idx_vehicles_city_status", "city", "status"),
        Index(
            "idx_vehicles_zone_status_accessible",
            "zone_id",
            "status",
            "wheelchair_accessible",
        ),
        Index("idx_vehicles_driver_id", "driver_id"),
        Index("idx_vehicles_vehicle_type", "vehicle_type"),
        Index("idx_vehicles_status", "status"),
    )


class Fare(Base):
    __tablename__ = "fares"

    city: Mapped[str] = mapped_column(Text, primary_key=True)
    vehicle_type: Mapped[str] = mapped_column(Text, primary_key=True)
    base_fare_gbp: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    per_mile_gbp: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    per_minute_gbp: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)


class Trip(Base):
    __tablename__ = "trips"

    trip_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    vehicle_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("vehicles.vehicle_id"), nullable=False
    )
    driver_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("drivers.driver_id"), nullable=False
    )
    city: Mapped[str] = mapped_column(Text, nullable=False)
    pickup_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    distance_miles: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    dropoff_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    pickup_zone_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("zones.zone_id"), nullable=False
    )
    pickup_zone: Mapped[str] = mapped_column(Text, nullable=False)
    dropoff_zone_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("zones.zone_id"), nullable=False
    )
    dropoff_zone: Mapped[str] = mapped_column(Text, nullable=False)
    pickup_lat: Mapped[float] = mapped_column(Float, nullable=False)
    pickup_lon: Mapped[float] = mapped_column(Float, nullable=False)
    dropoff_lat: Mapped[float] = mapped_column(Float, nullable=False)
    dropoff_lon: Mapped[float] = mapped_column(Float, nullable=False)
    passenger_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fare_gbp: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    payment_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("idx_trips_vehicle_id", "vehicle_id"),
        Index("idx_trips_driver_id", "driver_id"),
        Index("idx_trips_city", "city"),
        Index("idx_trips_pickup_zone_id", "pickup_zone_id"),
        Index("idx_trips_dropoff_zone_id", "dropoff_zone_id"),
        Index("idx_trips_pickup_dropoff_zones", "pickup_zone_id", "dropoff_zone_id"),
        Index("idx_trips_pickup_time", "pickup_time"),
        Index("idx_trips_dropoff_time", "dropoff_time"),
    )


class Booking(Base):
    __tablename__ = "bookings"

    booking_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    trip_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("trips.trip_id"), nullable=False
    )
    city: Mapped[str] = mapped_column(Text, nullable=False)
    booking_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    requested_pickup_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    booking_channel: Mapped[str] = mapped_column(Text, nullable=False)
    booking_status: Mapped[str] = mapped_column(Text, nullable=False)
    passenger_count: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("idx_bookings_trip_id", "trip_id"),
        Index("idx_bookings_city_status", "city", "booking_status"),
        Index("idx_bookings_booking_time", "booking_time"),
        Index("idx_bookings_requested_pickup_time", "requested_pickup_time"),
    )


class VehicleEvent(Base):
    __tablename__ = "vehicle_events"

    event_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    vehicle_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("vehicles.vehicle_id"), nullable=False
    )
    driver_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("drivers.driver_id"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    zone_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("zones.zone_id"), nullable=False
    )
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("idx_vehicle_events_vehicle_id", "vehicle_id"),
        Index("idx_vehicle_events_driver_id", "driver_id"),
        Index("idx_vehicle_events_zone_id", "zone_id"),
        Index("idx_vehicle_events_city", "city"),
        Index("idx_vehicle_events_timestamp", "timestamp"),
        Index("idx_vehicle_events_status", "status"),
    )


class Demand(Base):
    __tablename__ = "demand"

    timestamp: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    zone_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("zones.zone_id"), primary_key=True
    )
    city: Mapped[str] = mapped_column(Text, nullable=False)
    zone: Mapped[str] = mapped_column(Text, nullable=False)
    demand_requests: Mapped[int] = mapped_column(Integer, nullable=False)
    available_vehicles: Mapped[int] = mapped_column(Integer, nullable=False)
    unserved_requests: Mapped[int] = mapped_column(Integer, nullable=False)
    demand_index: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)

    __table_args__ = (
        Index("idx_demand_zone_id", "zone_id"),
        Index("idx_demand_city", "city"),
        Index("idx_demand_timestamp", "timestamp"),
    )


# ---------------------------------------------------------------------------
# Synthetic vehicle / pricing layer (new datasets)
# ---------------------------------------------------------------------------


class FareRule(Base):
    """Pricing tier fare parameters (not the legacy city/vehicle fares table)."""

    __tablename__ = "fare_rules"

    pricing_tier: Mapped[str] = mapped_column(String(32), primary_key=True)
    base_fare_gbp: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    included_distance_miles: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    per_mile_gbp: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    per_minute_gbp: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)


class VehicleClass(Base):
    __tablename__ = "vehicle_classes"

    vehicle_class_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    min_passengers: Mapped[int] = mapped_column(Integer, nullable=False)
    max_passengers: Mapped[int] = mapped_column(Integer, nullable=False)
    luggage_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    pricing_tier: Mapped[str] = mapped_column(
        String(32), ForeignKey("fare_rules.pricing_tier"), nullable=False
    )
    wheelchair_accessible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("idx_vehicle_classes_pricing_tier", "pricing_tier"),
        Index("idx_vehicle_classes_accessible", "wheelchair_accessible"),
        Index("idx_vehicle_classes_passengers", "min_passengers", "max_passengers"),
    )


class VehicleCatalog(Base):
    __tablename__ = "vehicle_catalog"

    vehicle_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    vehicle_class_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("vehicle_classes.vehicle_class_id"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    make: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    passenger_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    luggage_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    wheelchair_accessible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Denormalised from vehicle_classes for the required FK to fare_rules
    pricing_tier: Mapped[str] = mapped_column(
        String(32), ForeignKey("fare_rules.pricing_tier"), nullable=False
    )

    __table_args__ = (
        Index("idx_vehicle_catalog_class", "vehicle_class_id"),
        Index("idx_vehicle_catalog_city", "city"),
        Index("idx_vehicle_catalog_accessible", "wheelchair_accessible"),
        Index("idx_vehicle_catalog_pricing_tier", "pricing_tier"),
    )


class CityModifier(Base):
    __tablename__ = "city_modifiers"

    city: Mapped[str] = mapped_column(Text, primary_key=True)
    city_multiplier: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)


class PeakRule(Base):
    __tablename__ = "peak_rules"

    rule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    start_hour: Mapped[int] = mapped_column(Integer, nullable=False)
    end_hour: Mapped[int] = mapped_column(Integer, nullable=False)
    multiplier: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)

    __table_args__ = (Index("idx_peak_rules_hours", "start_hour", "end_hour"),)


class SurgeRule(Base):
    __tablename__ = "surge_rules"

    state: Mapped[str] = mapped_column(String(32), primary_key=True)
    multiplier: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    supply_ratio_threshold: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    min_surge: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    max_surge: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)


class VehicleSelectionRule(Base):
    __tablename__ = "vehicle_selection_rules"

    min_passengers: Mapped[int] = mapped_column(Integer, primary_key=True)
    max_passengers: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_class_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("vehicle_classes.vehicle_class_id"),
        primary_key=True,
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    __table_args__ = (
        Index("idx_vehicle_selection_passengers", "min_passengers", "max_passengers"),
        Index("idx_vehicle_selection_priority", "priority"),
    )


class PricingConfig(Base):
    __tablename__ = "pricing_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class PricingTestCase(Base):
    __tablename__ = "pricing_test_cases"

    test_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    pickup: Mapped[str] = mapped_column(Text, nullable=False)
    destination: Mapped[str] = mapped_column(Text, nullable=False)
    passengers: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_vehicle_class: Mapped[str] = mapped_column(Text, nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("idx_users_email", "email", unique=True),
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("idx_auth_sessions_token_hash", "token_hash", unique=True),
        Index("idx_auth_sessions_user_id", "user_id"),
        Index("idx_auth_sessions_expires_at", "expires_at"),
    )


class UserProfile(Base):
    """Optional personal details — auth identity stays on users."""

    __tablename__ = "user_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, unique=True
    )
    phone_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    date_of_birth: Mapped[str | None] = mapped_column(String(16), nullable=True)
    address_line_1: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address_line_2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postcode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    profile_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    preferred_vehicle_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    special_requirements: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_user_profiles_user_id", "user_id", unique=True),
    )


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_chat_sessions_user_id", "user_id"),
        Index("idx_chat_sessions_updated_at", "updated_at"),
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_chat_messages_session_id", "session_id"),
        Index("idx_chat_messages_created_at", "created_at"),
    )


TABLE_ORDER = (
    "zones",
    "drivers",
    "vehicles",
    "fares",
    "trips",
    "bookings",
    "vehicle_events",
    "demand",
    # Pricing layer (FK order: fare_rules → classes → catalog/selection)
    "fare_rules",
    "vehicle_classes",
    "vehicle_catalog",
    "city_modifiers",
    "peak_rules",
    "surge_rules",
    "vehicle_selection_rules",
    "pricing_config",
    "pricing_test_cases",
    # Auth
    "users",
    "auth_sessions",
    # Profile (FK → users)
    "user_profiles",
    # Chat history (FK → users, cascade messages)
    "chat_sessions",
    "chat_messages",
)

PRICING_TABLE_ORDER = (
    "fare_rules",
    "vehicle_classes",
    "vehicle_catalog",
    "city_modifiers",
    "peak_rules",
    "surge_rules",
    "vehicle_selection_rules",
    "pricing_config",
    "pricing_test_cases",
)

EXPECTED_INDEXES = (
    "idx_zones_city",
    "idx_zones_zone_name",
    "idx_zones_zone_type",
    "idx_drivers_city",
    "idx_drivers_licence_status",
    "idx_drivers_licence_type",
    "idx_drivers_rating",
    "idx_vehicles_city_status",
    "idx_vehicles_zone_status_accessible",
    "idx_vehicles_driver_id",
    "idx_vehicles_vehicle_type",
    "idx_vehicles_status",
    "idx_trips_vehicle_id",
    "idx_trips_driver_id",
    "idx_trips_city",
    "idx_trips_pickup_zone_id",
    "idx_trips_dropoff_zone_id",
    "idx_trips_pickup_dropoff_zones",
    "idx_trips_pickup_time",
    "idx_trips_dropoff_time",
    "idx_bookings_trip_id",
    "idx_bookings_city_status",
    "idx_bookings_booking_time",
    "idx_bookings_requested_pickup_time",
    "idx_vehicle_events_vehicle_id",
    "idx_vehicle_events_driver_id",
    "idx_vehicle_events_zone_id",
    "idx_vehicle_events_city",
    "idx_vehicle_events_timestamp",
    "idx_vehicle_events_status",
    "idx_demand_zone_id",
    "idx_demand_city",
    "idx_demand_timestamp",
    "idx_vehicle_classes_pricing_tier",
    "idx_vehicle_classes_accessible",
    "idx_vehicle_classes_passengers",
    "idx_vehicle_catalog_class",
    "idx_vehicle_catalog_city",
    "idx_vehicle_catalog_accessible",
    "idx_vehicle_catalog_pricing_tier",
    "idx_peak_rules_hours",
    "idx_vehicle_selection_passengers",
    "idx_vehicle_selection_priority",
    "idx_users_email",
    "idx_auth_sessions_token_hash",
    "idx_auth_sessions_user_id",
    "idx_auth_sessions_expires_at",
    "idx_user_profiles_user_id",
    "idx_chat_sessions_user_id",
    "idx_chat_sessions_updated_at",
    "idx_chat_messages_session_id",
    "idx_chat_messages_created_at",
)
