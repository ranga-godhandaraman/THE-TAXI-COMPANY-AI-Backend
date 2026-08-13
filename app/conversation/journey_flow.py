"""Conversational journey-planning flow — calls the deterministic pricing engine.

The LLM/heuristics only fill slots. All distance/duration/fare maths come from
app.pricing.PricingEngine (never from the LLM).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.conversation.models import ConversationState, DomainResult, Intent
from app.pricing import (
    JourneyEstimateRequest,
    LocationResolutionError,
    estimate_journey_fare,
)
from app.pricing.vehicles import select_vehicle_classes
from app.schemas.pricing import VehicleClassOut

_JOURNEY_INTENTS = {
    Intent.FARE_ESTIMATE,
    Intent.JOURNEY_ESTIMATE,
    Intent.BOOKING_REQUEST,
    Intent.VEHICLE_SEARCH,
}


def is_journey_planning(state: ConversationState) -> bool:
    if state.intent in _JOURNEY_INTENTS:
        return True
    # Mid-flow slot fills
    if state.pending_clarification in {"passengers", "vehicle_type", "destination", "pickup"}:
        return True
    if state.pickup and state.destination and state.intent in {
        Intent.ACCESSIBILITY_SEARCH,
        Intent.GENERAL_TAXI_QUERY,
        Intent.UNKNOWN,
    }:
        return True
    return False


def sync_slot_clarification(state: ConversationState) -> tuple[str, str] | None:
    """Return (message, pending_slot) for missing journey slots (no DB)."""
    if not is_journey_planning(state) and not (
        state.pickup or state.destination or state.vehicle_type
    ):
        return None

    # Treat vehicle-only requests as journey planning once we have a place
    if state.vehicle_type and not (state.pickup and state.destination):
        if not state.pickup or not state.pickup.resolved:
            return "Sure — where should we pick you up?", "pickup"
        if not state.destination or not state.destination.resolved:
            return (
                f"Got it from {state.pickup.resolved}. Where are you going?",
                "destination",
            )

    if state.intent not in _JOURNEY_INTENTS and not (
        state.pickup and state.destination
    ):
        return None

    if not state.pickup or not state.pickup.resolved:
        if state.destination and state.destination.resolved:
            return (
                f"Sure — heading to {state.destination.resolved}. "
                "Where should we pick you up?",
                "pickup",
            )
        return "Where would you like to be picked up?", "pickup"

    if not state.destination or not state.destination.resolved:
        return (
            f"Got it — from {state.pickup.resolved}. Where are you going?",
            "destination",
        )

    if state.passengers is None:
        return (
            "Absolutely. How many people will be travelling?",
            "passengers",
        )

    return None


async def handle_journey_turn(
    session: AsyncSession,
    state: ConversationState,
) -> tuple[DomainResult | None, ConversationState, str | None, str | None]:
    """
    Drive the journey-planning conversation.

    Returns:
      (domain_result, updated_state, clarification_message, pending_slot)
    """
    # Ensure journey intent once we have a route
    if state.pickup and state.destination and state.intent in {
        Intent.UNKNOWN,
        Intent.GENERAL_TAXI_QUERY,
        Intent.VEHICLE_SEARCH,
        Intent.ACCESSIBILITY_SEARCH,
        Intent.BOOKING_REQUEST,
    }:
        state.intent = Intent.FARE_ESTIMATE

    sync = sync_slot_clarification(state)
    if sync:
        msg, slot = sync
        state.pending_clarification = slot
        return None, state, msg, slot

    # Map 7-seater / min_seats into passenger floor if needed
    passengers = state.passengers or 1
    if state.min_seats and state.min_seats > passengers:
        passengers = state.min_seats
        if state.passengers is None:
            state.passengers = passengers

    preferred = _normalize_vehicle_class(state.vehicle_type)
    if preferred == "ACCESSIBLE":
        state.accessibility_required = True
    if state.min_seats and state.min_seats >= 6 and not preferred:
        preferred = "XL"
        state.vehicle_type = preferred

    # Always load the full passenger-eligible set first (no preference filter).
    # If a prior pick no longer fits (e.g. Sedan after "we are 5"), clear it
    # and re-offer the choice cards with images.
    classes = await select_vehicle_classes(
        session,
        passengers=passengers,
        accessibility_required=state.accessibility_required,
        preferred_vehicle_class=None,
    )

    if not classes:
        state.pending_clarification = None
        return (
            DomainResult(
                domain="journey",
                summary=(
                    f"I don't have a suitable vehicle class for {passengers} passengers"
                    + (" with accessibility" if state.accessibility_required else "")
                    + ". I can usually cover 1–7 passengers — want to try a different number?"
                ),
            ),
            state,
            None,
            None,
        )

    eligible_ids = {c.vehicle_class_id.upper() for c in classes}
    if preferred is not None and preferred.upper() not in eligible_ids:
        preferred = None
        state.vehicle_type = None

    # Multiple options and no preference yet → present choices (no pricing)
    if preferred is None and len(classes) > 1:
        msg = _format_vehicle_choices(passengers, classes)
        state.pending_clarification = "vehicle_type"
        option_rows = [
            {
                "vehicle_class_id": c.vehicle_class_id,
                "display_name": c.display_name,
                "passenger_capacity": c.max_passengers,
                "luggage_capacity": c.luggage_capacity,
                "min_passengers": c.min_passengers,
                "wheelchair_accessible": c.wheelchair_accessible,
            }
            for c in classes
        ]
        return (
            DomainResult(
                domain="journey",
                summary=msg,
                data=option_rows,
                meta={"vehicle_options": True, "pending_clarification": "vehicle_type"},
            ),
            state,
            None,
            None,
        )

    # Single eligible class → use it
    if preferred is None and len(classes) == 1:
        preferred = classes[0].vehicle_class_id
        state.vehicle_type = preferred

    vehicle_class = preferred or classes[0].vehicle_class_id
    state.vehicle_type = vehicle_class
    state.pending_clarification = None

    when = _resolve_datetime(state)
    pickup_raw = state.pickup.resolved or state.pickup.raw or ""
    dest_raw = state.destination.resolved or state.destination.raw or ""

    try:
        result = await estimate_journey_fare(
            session,
            JourneyEstimateRequest(
                pickup=pickup_raw,
                destination=dest_raw,
                passengers=passengers,
                vehicle_class=vehicle_class,
                requested_datetime=when,
                accessibility_required=state.accessibility_required,
                pickup_zone_id=state.pickup.zone_id,
            ),
        )
    except LocationResolutionError as exc:
        return (
            DomainResult(
                domain="journey",
                summary=exc.message,
                error=True,
                meta=exc.to_dict(),
            ),
            state,
            None,
            None,
        )
    except ValueError as exc:
        return (
            DomainResult(domain="journey", summary=str(exc), error=True),
            state,
            None,
            None,
        )

    # Prefer the matching option
    option = next(
        (o for o in result.vehicle_options if o.vehicle_class == vehicle_class),
        result.vehicle_options[0] if result.vehicle_options else None,
    )
    if option is None:
        return (
            DomainResult(
                domain="journey",
                summary="I couldn't produce a fare estimate for that vehicle.",
                error=True,
            ),
            state,
            None,
            None,
        )

    summary = _format_fare_reply(result.pickup, result.destination, option)
    data = [option.model_dump()]
    return (
        DomainResult(
            domain="journey",
            summary=summary,
            data=data,
            estimate=True,
            meta={
                "estimate_type": result.estimate_type,
                "surge_state": result.surge_state,
                "peak_multiplier": result.peak_multiplier,
                "distance_miles": result.estimated_distance_miles,
                "duration_minutes": result.estimated_duration_minutes,
            },
        ),
        state,
        None,
        None,
    )


def _normalize_vehicle_class(raw: str | None) -> str | None:
    if not raw:
        return None
    key = raw.strip().upper().replace(" ", "_").replace("-", "_")
    aliases = {
        "SEDAN": "SEDAN",
        "SALOON": "SEDAN",
        "STANDARD": "SEDAN",
        "STANDARD_SEDAN": "SEDAN",
        "SUV": "SUV",
        "EXECUTIVE": "EXECUTIVE",
        "EXEC": "EXECUTIVE",
        "XL": "XL",
        "MPV": "XL",
        "7_SEATER": "XL",
        "7SEATER": "XL",
        "ACCESSIBLE": "ACCESSIBLE",
    }
    # Also handle display names
    low = raw.strip().lower()
    if "executive" in low:
        return "EXECUTIVE"
    if "suv" in low:
        return "SUV"
    if "sedan" in low or "saloon" in low or "standard" in low:
        return "SEDAN"
    if "7" in low or "xl" in low or "mpv" in low or "seater" in low:
        return "XL"
    if "access" in low or "wheelchair" in low:
        return "ACCESSIBLE"
    return aliases.get(key)


def _format_vehicle_choices(passengers: int, classes: list[VehicleClassOut]) -> str:
    lines = [
        f"For {passengers} passenger{'s' if passengers != 1 else ''}, I can offer:",
        "",
    ]
    for c in classes:
        lines.append(f"{c.display_name} — up to {c.max_passengers}")
    lines.append("")
    lines.append("Which would you prefer?")
    return "\n".join(lines)


def _format_fare_reply(pickup: str, destination: str, option: Any) -> str:
    hours = option.estimated_duration_minutes // 60
    mins = option.estimated_duration_minutes % 60
    if hours and mins:
        duration = f"{hours}h {mins}m"
    elif hours:
        duration = f"{hours}h"
    else:
        duration = f"{mins} minutes"

    return (
        f"For a {option.display_name}, the estimated fare is "
        f"£{option.estimated_min_gbp:.0f}–£{option.estimated_max_gbp:.0f}.\n\n"
        f"{pickup} → {destination}\n"
        f"~{option.estimated_distance_miles:g} miles\n"
        f"~{duration}\n\n"
        "This is an estimate, not a live quote."
    )


def _resolve_datetime(state: ConversationState) -> datetime | None:
    """Best-effort datetime from date/time slots (no LLM)."""
    if not state.date and not state.requested_time:
        return None
    now = datetime.now()
    base = now
    if state.date:
        d = state.date.lower()
        if "tomorrow" in d:
            base = now + timedelta(days=1)
        elif "today" in d:
            base = now
    hour = 9
    if state.requested_time:
        t = state.requested_time.lower()
        if "morning" in t or "8" in t or "08" in t:
            hour = 8
        elif "afternoon" in t:
            hour = 14
        elif "evening" in t:
            hour = 18
        else:
            m = __import__("re").search(r"\b(\d{1,2})\b", t)
            if m:
                hour = int(m.group(1))
                if hour <= 12 and ("pm" in t or "p.m" in t):
                    hour = hour % 12 + 12
    return base.replace(hour=hour, minute=0, second=0, microsecond=0)
