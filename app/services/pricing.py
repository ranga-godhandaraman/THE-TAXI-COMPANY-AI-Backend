"""Service wrappers for the synthetic vehicle / pricing data layer."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import pricing_repository as repo
from app.schemas.pricing import (
    CityModifierOut,
    FareRuleOut,
    PeakRuleOut,
    PricingConfigOut,
    SurgeRuleOut,
    VehicleCatalogOut,
    VehicleClassOut,
    VehicleSelectionRuleOut,
)


async def get_vehicle_class(
    session: AsyncSession, vehicle_class_id: str
) -> VehicleClassOut | None:
    return await repo.get_vehicle_class(session, vehicle_class_id)


async def get_vehicle_classes(session: AsyncSession) -> list[VehicleClassOut]:
    return await repo.get_vehicle_classes(session)


async def get_vehicle_catalog(
    session: AsyncSession,
    *,
    city: str | None = None,
    vehicle_class_id: str | None = None,
    wheelchair_accessible: bool | None = None,
    limit: int = 500,
) -> list[VehicleCatalogOut]:
    return await repo.get_vehicle_catalog(
        session,
        city=city,
        vehicle_class_id=vehicle_class_id,
        wheelchair_accessible=wheelchair_accessible,
        limit=limit,
    )


async def find_vehicle_classes_for_passengers(
    session: AsyncSession, passengers: int
) -> list[VehicleClassOut]:
    return await repo.find_vehicle_classes_for_passengers(session, passengers)


async def find_accessible_vehicle_classes(
    session: AsyncSession,
) -> list[VehicleClassOut]:
    return await repo.find_accessible_vehicle_classes(session)


async def get_fare_rule(
    session: AsyncSession, pricing_tier: str
) -> FareRuleOut | None:
    return await repo.get_fare_rule(session, pricing_tier)


async def get_city_modifier(
    session: AsyncSession, city: str
) -> CityModifierOut | None:
    return await repo.get_city_modifier(session, city)


async def get_peak_rule(
    session: AsyncSession, rule_id: str | None = None
) -> PeakRuleOut | list[PeakRuleOut] | None:
    return await repo.get_peak_rule(session, rule_id)


async def get_surge_rule(
    session: AsyncSession, state: str | None = None
) -> SurgeRuleOut | list[SurgeRuleOut] | None:
    return await repo.get_surge_rule(session, state)


async def get_pricing_config(
    session: AsyncSession, key: str | None = None
) -> PricingConfigOut | list[PricingConfigOut] | None:
    return await repo.get_pricing_config(session, key)


async def get_vehicle_selection_rules(
    session: AsyncSession,
) -> list[VehicleSelectionRuleOut]:
    return await repo.get_vehicle_selection_rules(session)
