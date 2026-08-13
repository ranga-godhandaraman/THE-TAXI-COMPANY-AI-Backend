"""Repository accessors for synthetic vehicle / pricing tables."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CityModifier,
    FareRule,
    PeakRule,
    PricingConfig,
    SurgeRule,
    VehicleCatalog,
    VehicleClass,
    VehicleSelectionRule,
)
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
    result = await session.execute(
        select(VehicleClass).where(VehicleClass.vehicle_class_id == vehicle_class_id)
    )
    row = result.scalar_one_or_none()
    return VehicleClassOut.model_validate(row) if row else None


async def get_vehicle_classes(session: AsyncSession) -> list[VehicleClassOut]:
    result = await session.execute(
        select(VehicleClass).order_by(VehicleClass.vehicle_class_id)
    )
    return [VehicleClassOut.model_validate(r) for r in result.scalars().all()]


async def get_vehicle_catalog(
    session: AsyncSession,
    *,
    city: str | None = None,
    vehicle_class_id: str | None = None,
    wheelchair_accessible: bool | None = None,
    limit: int = 500,
) -> list[VehicleCatalogOut]:
    stmt = select(VehicleCatalog)
    if city is not None:
        stmt = stmt.where(VehicleCatalog.city == city)
    if vehicle_class_id is not None:
        stmt = stmt.where(VehicleCatalog.vehicle_class_id == vehicle_class_id)
    if wheelchair_accessible is not None:
        stmt = stmt.where(VehicleCatalog.wheelchair_accessible.is_(wheelchair_accessible))
    result = await session.execute(
        stmt.order_by(VehicleCatalog.vehicle_id).limit(limit)
    )
    return [VehicleCatalogOut.model_validate(r) for r in result.scalars().all()]


async def find_vehicle_classes_for_passengers(
    session: AsyncSession, passengers: int
) -> list[VehicleClassOut]:
    """Eligible classes from vehicle_selection_rules for a passenger count."""
    rules = await session.execute(
        select(VehicleSelectionRule)
        .where(
            VehicleSelectionRule.min_passengers <= passengers,
            VehicleSelectionRule.max_passengers >= passengers,
        )
        .order_by(VehicleSelectionRule.priority, VehicleSelectionRule.vehicle_class_id)
    )
    class_ids = [r.vehicle_class_id for r in rules.scalars().all()]
    if not class_ids:
        return []
    # Preserve selection priority order
    classes = await get_vehicle_classes(session)
    by_id = {c.vehicle_class_id: c for c in classes}
    return [by_id[cid] for cid in class_ids if cid in by_id]


async def find_accessible_vehicle_classes(
    session: AsyncSession,
) -> list[VehicleClassOut]:
    result = await session.execute(
        select(VehicleClass)
        .where(VehicleClass.wheelchair_accessible.is_(True))
        .order_by(VehicleClass.vehicle_class_id)
    )
    return [VehicleClassOut.model_validate(r) for r in result.scalars().all()]


async def get_fare_rule(
    session: AsyncSession, pricing_tier: str
) -> FareRuleOut | None:
    result = await session.execute(
        select(FareRule).where(FareRule.pricing_tier == pricing_tier)
    )
    row = result.scalar_one_or_none()
    return FareRuleOut.model_validate(row) if row else None


async def get_city_modifier(
    session: AsyncSession, city: str
) -> CityModifierOut | None:
    result = await session.execute(
        select(CityModifier).where(CityModifier.city == city)
    )
    row = result.scalar_one_or_none()
    return CityModifierOut.model_validate(row) if row else None


async def get_peak_rule(
    session: AsyncSession, rule_id: str | None = None
) -> PeakRuleOut | list[PeakRuleOut] | None:
    if rule_id is not None:
        result = await session.execute(
            select(PeakRule).where(PeakRule.rule_id == rule_id)
        )
        row = result.scalar_one_or_none()
        return PeakRuleOut.model_validate(row) if row else None
    result = await session.execute(select(PeakRule).order_by(PeakRule.rule_id))
    return [PeakRuleOut.model_validate(r) for r in result.scalars().all()]


async def get_surge_rule(
    session: AsyncSession, state: str | None = None
) -> SurgeRuleOut | list[SurgeRuleOut] | None:
    if state is not None:
        result = await session.execute(
            select(SurgeRule).where(SurgeRule.state == state)
        )
        row = result.scalar_one_or_none()
        return SurgeRuleOut.model_validate(row) if row else None
    result = await session.execute(select(SurgeRule).order_by(SurgeRule.state))
    return [SurgeRuleOut.model_validate(r) for r in result.scalars().all()]


async def get_pricing_config(
    session: AsyncSession, key: str | None = None
) -> PricingConfigOut | list[PricingConfigOut] | None:
    if key is not None:
        result = await session.execute(
            select(PricingConfig).where(PricingConfig.key == key)
        )
        row = result.scalar_one_or_none()
        return PricingConfigOut.model_validate(row) if row else None
    result = await session.execute(select(PricingConfig).order_by(PricingConfig.key))
    return [PricingConfigOut.model_validate(r) for r in result.scalars().all()]


async def get_vehicle_selection_rules(
    session: AsyncSession,
) -> list[VehicleSelectionRuleOut]:
    result = await session.execute(
        select(VehicleSelectionRule).order_by(
            VehicleSelectionRule.priority,
            VehicleSelectionRule.min_passengers,
            VehicleSelectionRule.vehicle_class_id,
        )
    )
    return [VehicleSelectionRuleOut.model_validate(r) for r in result.scalars().all()]
