"""Resolve free-text places against zone catalogue (lat/lon aware)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.conversation.aliases import (
    CITY_ALIASES,
    apply_location_alias,
    normalize_text,
)
from app.conversation.models import LocationRef
from app.db import repository as repo
from app.schemas.taxi import ZoneOut

# When user names a city only, prefer these zone names within that city.
_CITY_DEFAULT_ZONE = {
    "Birmingham": "City Centre",
    "Manchester": "City Centre",
    "London": "Westminster",
    "Leeds": "City Centre",
    "Liverpool": "City Centre",
}


def re_match_city_centre(norm: str) -> tuple[str, str] | None:
    m = re.match(r"^(birmingham|manchester|leeds|liverpool)\s+city\s+centre$", norm)
    if m:
        city = m.group(1).title()
        return city, "City Centre"
    return None


@dataclass
class ZoneCatalogue:
    zones: list[ZoneOut]

    def by_id(self, zone_id: str) -> ZoneOut | None:
        return next((z for z in self.zones if z.zone_id == zone_id), None)


async def load_catalogue(session: AsyncSession) -> ZoneCatalogue:
    return ZoneCatalogue(zones=await repo.list_zones(session, limit=200))


def resolve_location(
    raw: str | None,
    catalogue: ZoneCatalogue,
    *,
    prefer_city: str | None = None,
) -> LocationRef:
    if not raw or not str(raw).strip():
        return LocationRef(raw=raw)

    original = str(raw).strip()
    aliased = apply_location_alias(original)
    norm = normalize_text(aliased)

    # "Birmingham City Centre" / "Manchester City Centre" style aliases
    city_centre = re_match_city_centre(norm)
    if city_centre:
        city_name, zone_name = city_centre
        matches = [
            z
            for z in catalogue.zones
            if z.city == city_name and normalize_text(z.zone_name) == normalize_text(zone_name)
        ]
        if matches:
            return _from_zone(original, matches[0], confidence=0.95)

    city_hint = CITY_ALIASES.get(norm)

    # Exact zone_name match (case-insensitive)
    exact = [
        z
        for z in catalogue.zones
        if normalize_text(z.zone_name) == norm
        and (prefer_city is None or z.city.lower() == prefer_city.lower())
    ]
    if not exact and prefer_city is None:
        exact = [z for z in catalogue.zones if normalize_text(z.zone_name) == norm]
    if len(exact) == 1:
        return _from_zone(original, exact[0], confidence=0.98)
    if len(exact) > 1 and prefer_city:
        city_exact = [z for z in exact if z.city.lower() == prefer_city.lower()]
        if len(city_exact) == 1:
            return _from_zone(original, city_exact[0], confidence=0.95)

    # City-only → default zone in that city
    city_name = city_hint or (aliased if aliased in _CITY_DEFAULT_ZONE else None)
    if city_name and city_name in _CITY_DEFAULT_ZONE:
        default_zone = _CITY_DEFAULT_ZONE[city_name]
        matches = [
            z
            for z in catalogue.zones
            if z.city == city_name and normalize_text(z.zone_name) == normalize_text(default_zone)
        ]
        if matches:
            return _from_zone(original, matches[0], confidence=0.85)

    # Substring / contains match on zone_name
    contains = [
        z
        for z in catalogue.zones
        if norm in normalize_text(z.zone_name) or normalize_text(z.zone_name) in norm
    ]
    if prefer_city:
        filtered = [z for z in contains if z.city.lower() == prefer_city.lower()]
        if filtered:
            contains = filtered
    if len(contains) == 1:
        return _from_zone(original, contains[0], confidence=0.8)
    if len(contains) > 1:
        # Prefer AIRPORT if user said airport, else first URBAN city centre-ish
        airports = [z for z in contains if (z.zone_type or "").upper() == "AIRPORT"]
        if "airport" in norm and airports:
            return _from_zone(original, airports[0], confidence=0.75)
        centres = [z for z in contains if "centre" in normalize_text(z.zone_name) or "center" in normalize_text(z.zone_name)]
        pick = centres[0] if centres else contains[0]
        prompt = (
            f"I found a few matches for “{original}”. "
            f"Do you mean {pick.zone_name} in {pick.city}?"
        )
        ref = _from_zone(original, pick, confidence=0.55)
        ref.needs_clarification = True
        ref.clarification_prompt = prompt
        return ref

    # City name appears in zone.city with fuzzy zone
    for z in catalogue.zones:
        if prefer_city and z.city.lower() != prefer_city.lower():
            continue
        if norm == normalize_text(z.city):
            default = _CITY_DEFAULT_ZONE.get(z.city)
            if default:
                match = next(
                    (
                        x
                        for x in catalogue.zones
                        if x.city == z.city
                        and normalize_text(x.zone_name) == normalize_text(default)
                    ),
                    z,
                )
                return _from_zone(original, match, confidence=0.7)

    return LocationRef(
        raw=original,
        resolved=None,
        confidence=0.0,
        needs_clarification=True,
        clarification_prompt=(
            f"I couldn't place “{original}”. "
            "Could you confirm the area or city (for example Didsbury in Manchester)?"
        ),
    )


def _from_zone(raw: str, zone: ZoneOut, *, confidence: float) -> LocationRef:
    resolved = zone.zone_name
    # City-only defaults resolve to "City Centre" — show a clearer label
    if normalize_text(zone.zone_name) == "city centre":
        resolved = f"{zone.city} City Centre"
    return LocationRef(
        raw=raw,
        resolved=resolved,
        zone_id=zone.zone_id,
        city=zone.city,
        latitude=float(zone.latitude) if zone.latitude is not None else None,
        longitude=float(zone.longitude) if zone.longitude is not None else None,
        confidence=confidence,
        needs_clarification=False,
    )
