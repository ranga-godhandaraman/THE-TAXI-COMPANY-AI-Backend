"""Surge state from demand / availability — never invented by an LLM."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import pricing_repository as repo
from app.db.models import Demand, Vehicle
from app.schemas.pricing import SurgeRuleOut


@dataclass(frozen=True)
class SurgeAssessment:
    state: str
    multiplier: float
    availability_ratio: float | None
    source: str  # "override" | "neon" | "default_normal"


async def assess_surge(
    session: AsyncSession,
    *,
    zone_id: str | None,
    available_vehicles: int | None = None,
    demand_requests: int | None = None,
) -> SurgeAssessment:
    """
    Determine surge from availability_ratio = available / demand.

    Thresholds come from surge_rules (supply_ratio_threshold).
    If live data is unavailable → NORMAL (never invent a value).
    """
    rules = await repo.get_surge_rule(session)
    assert isinstance(rules, list)
    if not rules:
        return SurgeAssessment("NORMAL", 1.0, None, "default_normal")

    avail = available_vehicles
    demand = demand_requests

    source = "override"
    if avail is None or demand is None:
        neon = await _load_zone_availability(session, zone_id)
        if neon is None:
            normal = _pick_state(rules, "NORMAL")
            return SurgeAssessment(
                "NORMAL",
                float(normal.multiplier) if normal else 1.0,
                None,
                "default_normal",
            )
        avail, demand = neon
        source = "neon"

    if demand <= 0:
        ratio = 1.0 if (avail or 0) > 0 else 0.0
    else:
        ratio = float(avail) / float(demand)

    state, rule = _state_for_ratio(rules, ratio)
    return SurgeAssessment(
        state=state,
        multiplier=float(rule.multiplier),
        availability_ratio=round(ratio, 4),
        source=source,
    )


async def _load_zone_availability(
    session: AsyncSession, zone_id: str | None
) -> tuple[int, int] | None:
    if not zone_id:
        return None

    # Latest demand snapshot for the zone
    demand_row = await session.execute(
        select(Demand)
        .where(Demand.zone_id == zone_id)
        .order_by(Demand.timestamp.desc())
        .limit(1)
    )
    demand = demand_row.scalar_one_or_none()
    if demand is None:
        # Fall back to counting AVAILABLE vehicles in zone
        avail_result = await session.execute(
            select(func.count())
            .select_from(Vehicle)
            .where(Vehicle.zone_id == zone_id, Vehicle.status == "AVAILABLE")
        )
        available = int(avail_result.scalar_one())
        if available <= 0:
            return None
        # No demand row — cannot compute ratio meaningfully
        return None

    return int(demand.available_vehicles), int(demand.demand_requests)


def _pick_state(rules: list[SurgeRuleOut], state: str) -> SurgeRuleOut | None:
    return next((r for r in rules if r.state == state), None)


def _state_for_ratio(
    rules: list[SurgeRuleOut], ratio: float
) -> tuple[str, SurgeRuleOut]:
    """
    Lower availability ratio → more severe surge.

    CRITICAL if ratio < 0.5, HIGH_DEMAND if < 0.7, TIGHT if < 0.85, else NORMAL.
    Thresholds are read from each rule's supply_ratio_threshold.
    """
    ordered = sorted(rules, key=lambda r: float(r.supply_ratio_threshold))
    # Find the most severe (lowest threshold) that still has ratio < threshold
    # CRITICAL threshold 0.5: ratio < 0.5
    chosen: SurgeRuleOut | None = None
    for rule in ordered:
        if ratio < float(rule.supply_ratio_threshold):
            chosen = rule
            break
    if chosen is None:
        normal = _pick_state(rules, "NORMAL") or ordered[-1]
        return normal.state, normal
    return chosen.state, chosen
