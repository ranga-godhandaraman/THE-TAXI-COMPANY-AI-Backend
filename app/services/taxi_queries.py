"""High-level read-only query services over Neon PostgreSQL."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repository as repo
from app.db.models import Demand, Trip, Vehicle, Zone
from app.schemas.taxi import (
    AvgMetric,
    CountResult,
    RevenueResult,
    StatusDistribution,
    ZoneAvailabilityResult,
    ZoneDemandResult,
)


async def current_vehicle_availability(session: AsyncSession) -> CountResult:
    result = await session.execute(
        select(func.count())
        .select_from(Vehicle)
        .where(Vehicle.status == "AVAILABLE")
    )
    return CountResult(count=int(result.scalar_one()), status="AVAILABLE")


async def availability_by_city(session: AsyncSession, city: str) -> CountResult:
    result = await session.execute(
        select(func.count())
        .select_from(Vehicle)
        .where(Vehicle.status == "AVAILABLE", Vehicle.city == city)
    )
    return CountResult(count=int(result.scalar_one()), city=city, status="AVAILABLE")


async def availability_by_zone(
    session: AsyncSession, zone_id: str
) -> CountResult:
    zone = await repo.get_zone(session, zone_id)
    result = await session.execute(
        select(func.count())
        .select_from(Vehicle)
        .where(Vehicle.status == "AVAILABLE", Vehicle.zone_id == zone_id)
    )
    return CountResult(
        count=int(result.scalar_one()),
        zone_id=zone_id,
        zone_name=zone.zone_name if zone else None,
        status="AVAILABLE",
    )


async def available_accessible_vehicles(
    session: AsyncSession,
    *,
    city: str | None = None,
    zone_id: str | None = None,
    zone_name: str | None = None,
) -> CountResult:
    resolved_zone_id = zone_id
    resolved_name = zone_name
    if zone_name and not zone_id:
        zone = await repo.get_zone_by_name(session, zone_name, city=city)
        if zone is None:
            return CountResult(
                count=0,
                city=city,
                zone_name=zone_name,
                wheelchair_accessible=True,
                status="AVAILABLE",
            )
        resolved_zone_id = zone.zone_id
        resolved_name = zone.zone_name

    stmt = (
        select(func.count())
        .select_from(Vehicle)
        .where(
            Vehicle.status == "AVAILABLE",
            Vehicle.wheelchair_accessible.is_(True),
        )
    )
    if city is not None:
        stmt = stmt.where(Vehicle.city == city)
    if resolved_zone_id is not None:
        stmt = stmt.where(Vehicle.zone_id == resolved_zone_id)

    result = await session.execute(stmt)
    return CountResult(
        count=int(result.scalar_one()),
        city=city,
        zone_id=resolved_zone_id,
        zone_name=resolved_name,
        wheelchair_accessible=True,
        status="AVAILABLE",
    )


async def trips_between_zones(
    session: AsyncSession,
    *,
    pickup_zone_id: str | None = None,
    dropoff_zone_id: str | None = None,
    pickup_zone_name: str | None = None,
    dropoff_zone_name: str | None = None,
    city: str | None = None,
) -> CountResult:
    pickup_id = pickup_zone_id
    dropoff_id = dropoff_zone_id
    pickup_name = pickup_zone_name
    dropoff_name = dropoff_zone_name

    if pickup_zone_name and not pickup_zone_id:
        z = await repo.get_zone_by_name(session, pickup_zone_name, city=city)
        pickup_id = z.zone_id if z else None
        pickup_name = z.zone_name if z else pickup_zone_name
    if dropoff_zone_name and not dropoff_zone_id:
        z = await repo.get_zone_by_name(session, dropoff_zone_name, city=city)
        dropoff_id = z.zone_id if z else None
        dropoff_name = z.zone_name if z else dropoff_zone_name

    if pickup_id is None or dropoff_id is None:
        return CountResult(count=0, zone_name=f"{pickup_name}→{dropoff_name}")

    stmt = select(func.count()).select_from(Trip).where(
        Trip.pickup_zone_id == pickup_id,
        Trip.dropoff_zone_id == dropoff_id,
    )
    result = await session.execute(stmt)
    return CountResult(
        count=int(result.scalar_one()),
        zone_name=f"{pickup_name or pickup_id}→{dropoff_name or dropoff_id}",
    )


async def average_trip_distance(
    session: AsyncSession, *, city: str | None = None
) -> AvgMetric:
    stmt = select(
        func.avg(Trip.distance_miles),
        func.count(),
    ).select_from(Trip)
    if city is not None:
        stmt = stmt.where(Trip.city == city)
    avg, n = (await session.execute(stmt)).one()
    return AvgMetric(
        city=city,
        average=Decimal(str(avg)) if avg is not None else None,
        sample_size=int(n),
    )


async def average_trip_duration(
    session: AsyncSession, *, city: str | None = None
) -> AvgMetric:
    stmt = select(func.avg(Trip.duration_minutes), func.count()).select_from(Trip)
    if city is not None:
        stmt = stmt.where(Trip.city == city)
    avg, n = (await session.execute(stmt)).one()
    return AvgMetric(
        city=city,
        average=Decimal(str(avg)) if avg is not None else None,
        sample_size=int(n),
    )


async def average_fare(
    session: AsyncSession, *, city: str | None = None
) -> AvgMetric:
    stmt = select(func.avg(Trip.fare_gbp), func.count()).select_from(Trip)
    if city is not None:
        stmt = stmt.where(Trip.city == city)
    avg, n = (await session.execute(stmt)).one()
    return AvgMetric(
        city=city,
        average=Decimal(str(avg)) if avg is not None else None,
        sample_size=int(n),
    )


async def revenue(
    session: AsyncSession, *, city: str | None = None
) -> RevenueResult:
    stmt = select(func.coalesce(func.sum(Trip.fare_gbp), 0), func.count()).select_from(
        Trip
    )
    if city is not None:
        stmt = stmt.where(Trip.city == city)
    total, n = (await session.execute(stmt)).one()
    return RevenueResult(
        city=city,
        total_fare_gbp=Decimal(str(total)),
        trip_count=int(n),
    )


async def demand_by_zone(
    session: AsyncSession, *, limit: int = 10
) -> list[ZoneDemandResult]:
    stmt = (
        select(
            Demand.zone_id,
            Demand.zone,
            Demand.city,
            func.sum(Demand.demand_requests).label("total_demand"),
            func.sum(Demand.available_vehicles).label("total_available"),
            func.sum(Demand.unserved_requests).label("total_unserved"),
            func.avg(Demand.demand_index).label("avg_index"),
        )
        .group_by(Demand.zone_id, Demand.zone, Demand.city)
        .order_by(func.sum(Demand.demand_requests).desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        ZoneDemandResult(
            zone_id=r.zone_id,
            zone_name=r.zone,
            city=r.city,
            total_demand_requests=int(r.total_demand),
            total_available_vehicles=int(r.total_available),
            total_unserved_requests=int(r.total_unserved),
            avg_demand_index=Decimal(str(r.avg_index)) if r.avg_index is not None else None,
        )
        for r in rows
    ]


async def unserved_demand_by_zone(
    session: AsyncSession, *, limit: int = 10
) -> list[ZoneDemandResult]:
    stmt = (
        select(
            Demand.zone_id,
            Demand.zone,
            Demand.city,
            func.sum(Demand.demand_requests).label("total_demand"),
            func.sum(Demand.available_vehicles).label("total_available"),
            func.sum(Demand.unserved_requests).label("total_unserved"),
            func.avg(Demand.demand_index).label("avg_index"),
        )
        .group_by(Demand.zone_id, Demand.zone, Demand.city)
        .order_by(func.sum(Demand.unserved_requests).desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        ZoneDemandResult(
            zone_id=r.zone_id,
            zone_name=r.zone,
            city=r.city,
            total_demand_requests=int(r.total_demand),
            total_available_vehicles=int(r.total_available),
            total_unserved_requests=int(r.total_unserved),
            avg_demand_index=Decimal(str(r.avg_index)) if r.avg_index is not None else None,
        )
        for r in rows
    ]


async def zones_lowest_vehicle_availability(
    session: AsyncSession, *, limit: int = 10
) -> list[ZoneAvailabilityResult]:
    """Zones with fewest currently AVAILABLE vehicles (fleet snapshot)."""
    stmt = (
        select(
            Zone.zone_id,
            Zone.zone_name,
            Zone.city,
            func.count(Vehicle.vehicle_id).label("available_vehicles"),
        )
        .select_from(Zone)
        .outerjoin(
            Vehicle,
            (Vehicle.zone_id == Zone.zone_id) & (Vehicle.status == "AVAILABLE"),
        )
        .group_by(Zone.zone_id, Zone.zone_name, Zone.city)
        .order_by(func.count(Vehicle.vehicle_id).asc(), Zone.zone_id.asc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        ZoneAvailabilityResult(
            zone_id=r.zone_id,
            zone_name=r.zone_name,
            city=r.city,
            available_vehicles=int(r.available_vehicles),
        )
        for r in rows
    ]


async def vehicle_status_distribution(
    session: AsyncSession, *, city: str | None = None
) -> list[StatusDistribution]:
    stmt = select(Vehicle.status, func.count()).group_by(Vehicle.status)
    if city is not None:
        stmt = stmt.where(Vehicle.city == city)
    stmt = stmt.order_by(func.count().desc())
    rows = (await session.execute(stmt)).all()
    return [StatusDistribution(status=status, count=int(n)) for status, n in rows]
