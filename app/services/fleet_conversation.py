"""Fleet / availability helpers for conversational intents (reuse taxi_queries)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversation.models import ConversationState, DomainResult
from app.db.models import Vehicle
from app.services import taxi_queries


async def fleet_availability(session: AsyncSession, state: ConversationState) -> DomainResult:
    zone = state.pickup or state.destination
    accessibility = state.accessibility_required
    min_seats = state.min_seats

    if zone and zone.zone_id:
        if accessibility:
            result = await taxi_queries.available_accessible_vehicles(
                session, zone_id=zone.zone_id, city=zone.city
            )
            count = result.count
            label = f"wheelchair-accessible vehicles near {zone.resolved}"
        else:
            result = await taxi_queries.availability_by_zone(session, zone.zone_id)
            count = result.count
            label = f"vehicles available near {zone.resolved}"

        if min_seats:
            count = await _count_available(
                session,
                zone_id=zone.zone_id,
                min_seats=min_seats,
                accessible=accessibility,
            )
            label = f"{min_seats}+ seater vehicles near {zone.resolved}"

        summary = (
            f"There {'is' if count == 1 else 'are'} currently {count} {label}."
            if count
            else f"I can't see any {label} right now."
        )
        return DomainResult(
            domain="fleet",
            summary=summary,
            data=[
                {
                    "location": zone.resolved,
                    "city": zone.city,
                    "available": count,
                    "accessibility_required": accessibility,
                    "min_seats": min_seats,
                }
            ],
        )

    if zone and zone.city:
        result = await taxi_queries.availability_by_city(session, zone.city)
        summary = (
            f"There are currently {result.count} vehicles available in {zone.city}."
        )
        return DomainResult(
            domain="fleet",
            summary=summary,
            data=[{"city": zone.city, "available": result.count}],
        )

    result = await taxi_queries.current_vehicle_availability(session)
    return DomainResult(
        domain="fleet",
        summary=f"There are currently {result.count} vehicles available across the network.",
        data=[{"available": result.count}],
    )


async def _count_available(
    session: AsyncSession,
    *,
    zone_id: str,
    min_seats: int,
    accessible: bool,
) -> int:
    stmt = select(func.count()).select_from(Vehicle).where(
        Vehicle.status == "AVAILABLE",
        Vehicle.zone_id == zone_id,
        Vehicle.seats >= min_seats,
    )
    if accessible:
        stmt = stmt.where(Vehicle.wheelchair_accessible.is_(True))
    result = await session.execute(stmt)
    return int(result.scalar_one())
