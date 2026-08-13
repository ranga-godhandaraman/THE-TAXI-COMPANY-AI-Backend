"""Zone location resolution for the pricing engine (zones table only)."""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repository as repo
from app.pricing.models import LocationResolutionError, ResolvedLocation
from app.schemas.taxi import ZoneOut

_ALIASES: dict[str, str] = {
    "brum": "birmingham",
    "bham": "birmingham",
    "manchester airport": "manchester airport",
    "man airport": "manchester airport",
    "heathrow": "heathrow",
    "heathrow airport": "heathrow",
    "lhr": "heathrow",
    "gatwick": "gatwick",
    "lgw": "gatwick",
    "westminster": "westminster",
    "central london": "westminster",
    "london city centre": "city of london",
    "london centre": "westminster",
    "city of london": "city of london",
    "manchester city centre": "manchester|city centre",
    "manchester centre": "manchester|city centre",
    "birmingham city centre": "birmingham|city centre",
    "birmingham centre": "birmingham|city centre",
    "leeds city centre": "leeds|city centre",
    "liverpool city centre": "liverpool|city centre",
    "leeds bradford airport": "leeds bradford airport",
    "birmingham airport": "birmingham airport",
    "liverpool airport": "liverpool airport",
    "didsbury": "didsbury",
    "canary wharf": "canary wharf",
}

_CITY_DEFAULT_ZONE = {
    "birmingham": "city centre",
    "manchester": "city centre",
    "london": "westminster",
    "leeds": "city centre",
    "liverpool": "city centre",
}


def normalize(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"[^\w\s'/-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def resolve_zone_name(raw: str, zones: list[ZoneOut]) -> ResolvedLocation:
    original = raw.strip()
    if not original:
        raise LocationResolutionError("location", raw, "Location is empty.")

    norm = normalize(original)
    aliased = _ALIASES.get(norm, norm)

    # city|zone composite alias
    if "|" in aliased:
        city_part, zone_part = aliased.split("|", 1)
        matches = [
            z
            for z in zones
            if normalize(z.city) == city_part and normalize(z.zone_name) == zone_part
        ]
        if matches:
            return _to_resolved(original, matches[0])

    # Exact zone_name
    exact = [z for z in zones if normalize(z.zone_name) == aliased]
    if len(exact) == 1:
        return _to_resolved(original, exact[0])
    if len(exact) > 1:
        # Prefer unique if city also in query
        city_hint = next((c for c in _CITY_DEFAULT_ZONE if c in norm), None)
        if city_hint:
            filtered = [z for z in exact if normalize(z.city) == city_hint]
            if len(filtered) == 1:
                return _to_resolved(original, filtered[0])

    # City-only → default zone
    if aliased in _CITY_DEFAULT_ZONE or norm in _CITY_DEFAULT_ZONE:
        city_key = aliased if aliased in _CITY_DEFAULT_ZONE else norm
        zone_name = _CITY_DEFAULT_ZONE[city_key]
        matches = [
            z
            for z in zones
            if normalize(z.city) == city_key and normalize(z.zone_name) == zone_name
        ]
        if matches:
            return _to_resolved(original, matches[0])

    # Contains match
    contains = [
        z
        for z in zones
        if aliased in normalize(z.zone_name) or normalize(z.zone_name) in aliased
    ]
    if "airport" in aliased:
        airports = [z for z in contains if (z.zone_type or "").upper() == "AIRPORT"]
        if len(airports) == 1:
            return _to_resolved(original, airports[0])
        if airports:
            return _to_resolved(original, airports[0])
    if len(contains) == 1:
        return _to_resolved(original, contains[0])

    # "Leeds City Centre" style without alias hit
    m = re.match(
        r"^(birmingham|manchester|leeds|liverpool)\s+(city\s+)?centre$",
        aliased,
    )
    if m:
        city_key = m.group(1)
        matches = [
            z
            for z in zones
            if normalize(z.city) == city_key and normalize(z.zone_name) == "city centre"
        ]
        if matches:
            return _to_resolved(original, matches[0])

    raise LocationResolutionError(
        "location",
        original,
        f"Could not resolve “{original}” to a known zone.",
    )


async def load_zones(session: AsyncSession) -> list[ZoneOut]:
    return await repo.list_zones(session, limit=200)


def _to_resolved(raw: str, zone: ZoneOut) -> ResolvedLocation:
    return ResolvedLocation(
        raw=raw,
        zone_id=zone.zone_id,
        zone_name=zone.zone_name,
        city=zone.city,
        latitude=float(zone.latitude),
        longitude=float(zone.longitude),
    )
