"""Thin REST API for structured taxi data (no NL→SQL)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_user
from app.db import repository as repo
from app.db.session import get_session
from app.schemas.taxi import (
    BookingOut,
    BookingSearchParams,
    CountResult,
    DemandOut,
    DriverOut,
    FareOut,
    PaginatedBookings,
    PaginatedTrips,
    PaginatedVehicles,
    RevenueResult,
    StatusDistribution,
    TripOut,
    TripSearchParams,
    VehicleOut,
    VehicleSearchParams,
    ZoneAvailabilityResult,
    ZoneDemandResult,
    ZoneOut,
)
from app.services import taxi_queries

router = APIRouter(
    prefix="/api",
    tags=["taxi-data"],
    dependencies=[Depends(require_user)],
)


@router.get("/vehicles", response_model=PaginatedVehicles)
async def list_vehicles(
    city: str | None = None,
    status: str | None = None,
    zone_id: str | None = None,
    vehicle_type: str | None = None,
    wheelchair_accessible: bool | None = None,
    driver_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> PaginatedVehicles:
    return await repo.search_vehicles(
        session,
        VehicleSearchParams(
            city=city,
            status=status,
            zone_id=zone_id,
            vehicle_type=vehicle_type,
            wheelchair_accessible=wheelchair_accessible,
            driver_id=driver_id,
            limit=limit,
            offset=offset,
        ),
    )


@router.get("/vehicles/available", response_model=PaginatedVehicles)
async def list_available_vehicles(
    city: str | None = None,
    zone_id: str | None = None,
    wheelchair_accessible: bool | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> PaginatedVehicles:
    return await repo.get_available_vehicles(
        session,
        city=city,
        zone_id=zone_id,
        wheelchair_accessible=wheelchair_accessible,
        limit=limit,
        offset=offset,
    )


@router.get("/vehicles/{vehicle_id}", response_model=VehicleOut)
async def read_vehicle(
    vehicle_id: str, session: AsyncSession = Depends(get_session)
) -> VehicleOut:
    row = await repo.get_vehicle(session, vehicle_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return row


@router.get("/drivers/{driver_id}", response_model=DriverOut)
async def read_driver(
    driver_id: str, session: AsyncSession = Depends(get_session)
) -> DriverOut:
    row = await repo.get_driver(session, driver_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Driver not found")
    return row


@router.get("/trips/{trip_id}", response_model=TripOut)
async def read_trip(
    trip_id: str, session: AsyncSession = Depends(get_session)
) -> TripOut:
    row = await repo.get_trip(session, trip_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    return row


@router.get("/trips", response_model=PaginatedTrips)
async def list_trips(
    city: str | None = None,
    vehicle_id: str | None = None,
    driver_id: str | None = None,
    pickup_zone_id: str | None = None,
    dropoff_zone_id: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> PaginatedTrips:
    return await repo.search_trips(
        session,
        TripSearchParams(
            city=city,
            vehicle_id=vehicle_id,
            driver_id=driver_id,
            pickup_zone_id=pickup_zone_id,
            dropoff_zone_id=dropoff_zone_id,
            status=status,
            limit=limit,
            offset=offset,
        ),
    )


@router.get("/bookings/{booking_id}", response_model=BookingOut)
async def read_booking(
    booking_id: str, session: AsyncSession = Depends(get_session)
) -> BookingOut:
    row = await repo.get_booking(session, booking_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    return row


@router.get("/bookings", response_model=PaginatedBookings)
async def list_bookings(
    city: str | None = None,
    trip_id: str | None = None,
    booking_status: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> PaginatedBookings:
    return await repo.search_bookings(
        session,
        BookingSearchParams(
            city=city,
            trip_id=trip_id,
            booking_status=booking_status,
            limit=limit,
            offset=offset,
        ),
    )


@router.get("/zones", response_model=list[ZoneOut])
async def list_zones(
    city: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[ZoneOut]:
    return await repo.list_zones(session, city=city, limit=limit)


@router.get("/zones/{zone_id}", response_model=ZoneOut)
async def read_zone(
    zone_id: str, session: AsyncSession = Depends(get_session)
) -> ZoneOut:
    row = await repo.get_zone(session, zone_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Zone not found")
    return row


@router.get("/demand", response_model=list[DemandOut])
async def list_demand(
    zone_id: str | None = None,
    city: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[DemandOut]:
    return await repo.get_demand(session, zone_id=zone_id, city=city, limit=limit)


@router.get("/fares", response_model=list[FareOut])
async def list_fares(
    city: str | None = None,
    vehicle_type: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[FareOut]:
    return await repo.get_fare_rules(session, city=city, vehicle_type=vehicle_type)


@router.get("/analytics/availability", response_model=CountResult)
async def analytics_availability(
    city: str | None = None,
    zone_id: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> CountResult:
    if zone_id:
        return await taxi_queries.availability_by_zone(session, zone_id)
    if city:
        return await taxi_queries.availability_by_city(session, city)
    return await taxi_queries.current_vehicle_availability(session)


@router.get("/analytics/revenue", response_model=RevenueResult)
async def analytics_revenue(
    city: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> RevenueResult:
    return await taxi_queries.revenue(session, city=city)


@router.get("/analytics/demand", response_model=list[ZoneDemandResult])
async def analytics_demand(
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> list[ZoneDemandResult]:
    return await taxi_queries.demand_by_zone(session, limit=limit)


@router.get(
    "/analytics/zone-availability",
    response_model=list[ZoneAvailabilityResult],
)
async def analytics_zone_availability(
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> list[ZoneAvailabilityResult]:
    return await taxi_queries.zones_lowest_vehicle_availability(session, limit=limit)


@router.get("/analytics/status-distribution", response_model=list[StatusDistribution])
async def analytics_status_distribution(
    city: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[StatusDistribution]:
    return await taxi_queries.vehicle_status_distribution(session, city=city)
