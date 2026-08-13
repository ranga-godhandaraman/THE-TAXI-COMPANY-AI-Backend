"""Read-only repository layer for structured taxi tables."""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Booking, Demand, Driver, Fare, Trip, Vehicle, Zone
from app.schemas.taxi import (
    BookingOut,
    BookingSearchParams,
    DemandOut,
    DriverOut,
    FareOut,
    PaginatedBookings,
    PaginatedTrips,
    PaginatedVehicles,
    TripOut,
    TripSearchParams,
    VehicleOut,
    VehicleSearchParams,
    ZoneOut,
)


def _vehicle_filters(stmt: Select, params: VehicleSearchParams) -> Select:
    if params.city is not None:
        stmt = stmt.where(Vehicle.city == params.city)
    if params.status is not None:
        stmt = stmt.where(Vehicle.status == params.status)
    if params.zone_id is not None:
        stmt = stmt.where(Vehicle.zone_id == params.zone_id)
    if params.vehicle_type is not None:
        stmt = stmt.where(Vehicle.vehicle_type == params.vehicle_type)
    if params.wheelchair_accessible is not None:
        stmt = stmt.where(Vehicle.wheelchair_accessible.is_(params.wheelchair_accessible))
    if params.driver_id is not None:
        stmt = stmt.where(Vehicle.driver_id == params.driver_id)
    return stmt


async def get_vehicle(session: AsyncSession, vehicle_id: str) -> VehicleOut | None:
    result = await session.execute(
        select(Vehicle).where(Vehicle.vehicle_id == vehicle_id)
    )
    row = result.scalar_one_or_none()
    return VehicleOut.model_validate(row) if row else None


async def search_vehicles(
    session: AsyncSession, params: VehicleSearchParams
) -> PaginatedVehicles:
    base = _vehicle_filters(select(Vehicle), params)
    count_stmt = _vehicle_filters(select(func.count()).select_from(Vehicle), params)
    total = int((await session.execute(count_stmt)).scalar_one())
    result = await session.execute(
        base.order_by(Vehicle.vehicle_id).limit(params.limit).offset(params.offset)
    )
    items = [VehicleOut.model_validate(r) for r in result.scalars().all()]
    return PaginatedVehicles(
        items=items, total=total, limit=params.limit, offset=params.offset
    )


async def get_available_vehicles(
    session: AsyncSession,
    *,
    city: str | None = None,
    zone_id: str | None = None,
    wheelchair_accessible: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> PaginatedVehicles:
    return await search_vehicles(
        session,
        VehicleSearchParams(
            city=city,
            zone_id=zone_id,
            wheelchair_accessible=wheelchair_accessible,
            status="AVAILABLE",
            limit=limit,
            offset=offset,
        ),
    )


async def get_driver(session: AsyncSession, driver_id: str) -> DriverOut | None:
    result = await session.execute(select(Driver).where(Driver.driver_id == driver_id))
    row = result.scalar_one_or_none()
    return DriverOut.model_validate(row) if row else None


async def get_trip(session: AsyncSession, trip_id: str) -> TripOut | None:
    result = await session.execute(select(Trip).where(Trip.trip_id == trip_id))
    row = result.scalar_one_or_none()
    return TripOut.model_validate(row) if row else None


def _trip_filters(stmt: Select, params: TripSearchParams) -> Select:
    if params.city is not None:
        stmt = stmt.where(Trip.city == params.city)
    if params.vehicle_id is not None:
        stmt = stmt.where(Trip.vehicle_id == params.vehicle_id)
    if params.driver_id is not None:
        stmt = stmt.where(Trip.driver_id == params.driver_id)
    if params.pickup_zone_id is not None:
        stmt = stmt.where(Trip.pickup_zone_id == params.pickup_zone_id)
    if params.dropoff_zone_id is not None:
        stmt = stmt.where(Trip.dropoff_zone_id == params.dropoff_zone_id)
    if params.status is not None:
        stmt = stmt.where(Trip.status == params.status)
    return stmt


async def search_trips(
    session: AsyncSession, params: TripSearchParams
) -> PaginatedTrips:
    base = _trip_filters(select(Trip), params)
    count_stmt = _trip_filters(select(func.count()).select_from(Trip), params)
    total = int((await session.execute(count_stmt)).scalar_one())
    result = await session.execute(
        base.order_by(Trip.pickup_time.desc()).limit(params.limit).offset(params.offset)
    )
    items = [TripOut.model_validate(r) for r in result.scalars().all()]
    return PaginatedTrips(
        items=items, total=total, limit=params.limit, offset=params.offset
    )


async def get_booking(session: AsyncSession, booking_id: str) -> BookingOut | None:
    result = await session.execute(
        select(Booking).where(Booking.booking_id == booking_id)
    )
    row = result.scalar_one_or_none()
    return BookingOut.model_validate(row) if row else None


def _booking_filters(stmt: Select, params: BookingSearchParams) -> Select:
    if params.city is not None:
        stmt = stmt.where(Booking.city == params.city)
    if params.trip_id is not None:
        stmt = stmt.where(Booking.trip_id == params.trip_id)
    if params.booking_status is not None:
        stmt = stmt.where(Booking.booking_status == params.booking_status)
    return stmt


async def search_bookings(
    session: AsyncSession, params: BookingSearchParams
) -> PaginatedBookings:
    base = _booking_filters(select(Booking), params)
    count_stmt = _booking_filters(select(func.count()).select_from(Booking), params)
    total = int((await session.execute(count_stmt)).scalar_one())
    result = await session.execute(
        base.order_by(Booking.booking_time.desc())
        .limit(params.limit)
        .offset(params.offset)
    )
    items = [BookingOut.model_validate(r) for r in result.scalars().all()]
    return PaginatedBookings(
        items=items, total=total, limit=params.limit, offset=params.offset
    )


async def get_zone(session: AsyncSession, zone_id: str) -> ZoneOut | None:
    result = await session.execute(select(Zone).where(Zone.zone_id == zone_id))
    row = result.scalar_one_or_none()
    return ZoneOut.model_validate(row) if row else None


async def get_zone_by_name(
    session: AsyncSession, zone_name: str, city: str | None = None
) -> ZoneOut | None:
    stmt = select(Zone).where(Zone.zone_name == zone_name)
    if city is not None:
        stmt = stmt.where(Zone.city == city)
    result = await session.execute(stmt.limit(1))
    row = result.scalar_one_or_none()
    return ZoneOut.model_validate(row) if row else None


async def list_zones(
    session: AsyncSession, *, city: str | None = None, limit: int = 100
) -> list[ZoneOut]:
    stmt = select(Zone)
    if city is not None:
        stmt = stmt.where(Zone.city == city)
    result = await session.execute(stmt.order_by(Zone.zone_id).limit(limit))
    return [ZoneOut.model_validate(r) for r in result.scalars().all()]


async def get_demand(
    session: AsyncSession,
    *,
    zone_id: str | None = None,
    city: str | None = None,
    limit: int = 100,
) -> list[DemandOut]:
    stmt = select(Demand)
    if zone_id is not None:
        stmt = stmt.where(Demand.zone_id == zone_id)
    if city is not None:
        stmt = stmt.where(Demand.city == city)
    result = await session.execute(
        stmt.order_by(Demand.timestamp.desc()).limit(limit)
    )
    return [DemandOut.model_validate(r) for r in result.scalars().all()]


async def get_fare_rules(
    session: AsyncSession,
    *,
    city: str | None = None,
    vehicle_type: str | None = None,
) -> list[FareOut]:
    stmt = select(Fare)
    if city is not None:
        stmt = stmt.where(Fare.city == city)
    if vehicle_type is not None:
        stmt = stmt.where(Fare.vehicle_type == vehicle_type)
    result = await session.execute(stmt.order_by(Fare.city, Fare.vehicle_type))
    return [FareOut.model_validate(r) for r in result.scalars().all()]
