"""Deterministic eligible vehicle-class selection from selection rules."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import pricing_repository as repo
from app.schemas.pricing import VehicleClassOut


async def select_vehicle_classes(
    session: AsyncSession,
    *,
    passengers: int,
    accessibility_required: bool = False,
    preferred_vehicle_class: str | None = None,
) -> list[VehicleClassOut]:
    """
    Return all eligible customer-facing classes.

    Does not auto-pick a single option when several apply.
    Accessibility: prefer / restrict to accessible classes when required.
    """
    if accessibility_required:
        accessible = await repo.find_accessible_vehicle_classes(session)
        # Keep only those that can seat the passengers
        eligible = [
            c
            for c in accessible
            if c.min_passengers <= passengers <= c.max_passengers
        ]
        if preferred_vehicle_class:
            pref = preferred_vehicle_class.strip().upper()
            filtered = [c for c in eligible if c.vehicle_class_id.upper() == pref]
            return filtered or eligible
        return eligible

    classes = await repo.find_vehicle_classes_for_passengers(session, passengers)

    if preferred_vehicle_class:
        pref = preferred_vehicle_class.strip().upper()
        # Aliases
        aliases = {
            "SEDAN": "SEDAN",
            "STANDARD": "SEDAN",
            "STANDARD SEDAN": "SEDAN",
            "SUV": "SUV",
            "XL": "XL",
            "7-SEATER": "XL",
            "7 SEATER": "XL",
            "EXECUTIVE": "EXECUTIVE",
            "EXEC": "EXECUTIVE",
            "ACCESSIBLE": "ACCESSIBLE",
        }
        target = aliases.get(pref, pref)
        matched = [c for c in classes if c.vehicle_class_id.upper() == target]
        if matched:
            return matched
        # If preferred class exists but not in passenger band, still return it
        # only when it can seat them per class capacity
        all_classes = await repo.get_vehicle_classes(session)
        for c in all_classes:
            if c.vehicle_class_id.upper() == target and c.min_passengers <= passengers <= c.max_passengers:
                return [c]

    return classes
