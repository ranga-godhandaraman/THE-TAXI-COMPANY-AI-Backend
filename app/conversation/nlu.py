"""Natural-language understanding: LLM extraction + deterministic fallback."""

from __future__ import annotations

import re
from typing import Any

from app.agents.llm import LLMError, chat_json
from app.conversation.aliases import (
    VEHICLE_ALIASES,
    WEAK_VEHICLE_ALIASES,
    normalize_text,
)
from app.conversation.models import ConversationState, Intent, LocationRef

_CANONICAL_VEHICLE_IDS = frozenset(
    {"SEDAN", "SUV", "EXECUTIVE", "XL", "ACCESSIBLE", "LUXURY_VAN", "BLACK_CAB", "MINIBUS"}
)


def _match_vehicle_in_message(
    message: str,
    *,
    allow_weak: bool = False,
) -> str | None:
    """Return a canonical vehicle class when the message clearly names one."""
    q = message.lower().strip()
    if not q:
        return None
    # Frontend vehicle cards send the class id (e.g. "SUV", "SEDAN")
    if q.upper() in _CANONICAL_VEHICLE_IDS:
        return q.upper()
    for alias, canon in sorted(VEHICLE_ALIASES.items(), key=lambda x: -len(x[0])):
        if not allow_weak and alias in WEAK_VEHICLE_ALIASES:
            continue
        if re.search(rf"\b{re.escape(alias)}\b", q) or q == alias:
            return canon
    return None

NLU_SYSTEM = """You extract structured taxi/PHV journey-planning meaning from a UK customer message.

Return JSON only with keys:
intent, pickup, destination, passengers, vehicle_type, accessibility_required,
min_seats, requested_time, date, follow_up

Rules:
- intent must be one of:
  FARE_ESTIMATE, JOURNEY_ESTIMATE, VEHICLE_AVAILABILITY, NEARBY_VEHICLES,
  VEHICLE_SEARCH, ACCESSIBILITY_SEARCH, BOOKING_REQUEST, TRIP_LOOKUP,
  POLICY, OPERATIONS_ANALYSIS, DEMAND_ANALYSIS, FLEET_SEARCH,
  GENERAL_TAXI_QUERY, UNKNOWN
- pickup/destination: short place strings or null (do not invent places)
- passengers: integer or null
- vehicle_type: SEDAN | SUV | EXECUTIVE | XL | ACCESSIBLE | null
- accessibility_required: boolean
- min_seats: integer or null (e.g. 7 seater → 7)
- requested_time / date: short strings or null (e.g. "8 AM", "tomorrow")
- follow_up: true if the message refers to a previous journey
  ("3", "SUV", "what about executive", "how long")
- Prefer FARE_ESTIMATE for cost/price/how much / "I wanna go from A to B"
- Prefer POLICY for taxi vs PHV / rules / licensing
- Prefer ACCESSIBILITY_SEARCH only when accessibility is the main ask without a fare
- Never invent prices, distances, or multipliers. Never mention SQL/databases.
"""


def understand(
    message: str,
    prior: ConversationState,
) -> ConversationState:
    """Merge prior state with this turn's extraction."""
    heur = _heuristic_extract(message, prior)
    llm = _llm_extract(message, prior)
    extracted = _blend_extract(llm, heur, message) if llm else heur
    return _merge(prior, extracted, message)


def _blend_extract(
    llm: dict[str, Any],
    heur: dict[str, Any],
    message: str,
) -> dict[str, Any]:
    """Prefer LLM structure, but keep deterministic safety overlays."""
    out = dict(llm)
    q = message.lower()

    # Never let the LLM drop clear accessibility / vehicle / passenger signals
    if heur.get("accessibility_required") or re.search(
        r"\b(wheelchair|accessible)\b", q
    ):
        out["accessibility_required"] = True
        if not out.get("vehicle_type"):
            out["vehicle_type"] = heur.get("vehicle_type") or "ACCESSIBLE"

    if heur.get("passengers") is not None and out.get("passengers") is None:
        out["passengers"] = heur["passengers"]

    if heur.get("vehicle_type") and (
        not out.get("vehicle_type")
        or heur.get("follow_up")
        or re.search(r"\b(what about|instead)\b", q)
    ):
        out["vehicle_type"] = heur["vehicle_type"]

    if heur.get("min_seats") is not None:
        out["min_seats"] = heur["min_seats"]
        if not out.get("vehicle_type"):
            out["vehicle_type"] = heur.get("vehicle_type")

    if heur.get("date") and not out.get("date"):
        out["date"] = heur["date"]
    if heur.get("requested_time") and not out.get("requested_time"):
        out["requested_time"] = heur["requested_time"]

    # Ops / demand patterns beat a vague GENERAL classification
    if heur.get("intent") in {
        Intent.OPERATIONS_ANALYSIS.value,
        Intent.DEMAND_ANALYSIS.value,
        Intent.POLICY.value,
    }:
        out["intent"] = heur["intent"]

    if heur.get("follow_up"):
        out["follow_up"] = True

    # Fill missing route from heuristics
    if not out.get("pickup") and heur.get("pickup"):
        out["pickup"] = heur["pickup"]
    if not out.get("destination") and heur.get("destination"):
        out["destination"] = heur["destination"]

    return out


def _llm_extract(message: str, prior: ConversationState) -> dict[str, Any] | None:
    prior_brief = {
        "intent": prior.intent.value if prior.intent else None,
        "pickup": prior.pickup.raw if prior.pickup else None,
        "destination": prior.destination.raw if prior.destination else None,
        "passengers": prior.passengers,
        "vehicle_type": prior.vehicle_type,
        "accessibility_required": prior.accessibility_required,
        "min_seats": prior.min_seats,
        "date": prior.date,
        "requested_time": prior.requested_time,
        "pending_clarification": prior.pending_clarification,
    }
    try:
        return chat_json(
            system=NLU_SYSTEM,
            user=(
                f"Previous conversation slots:\n{prior_brief}\n\n"
                f"Customer message:\n{message}\n"
            ),
            temperature=0.0,
        )
    except (LLMError, Exception):  # noqa: BLE001
        return None


def _heuristic_extract(message: str, prior: ConversationState) -> dict[str, Any]:
    q = message.lower().strip()
    out: dict[str, Any] = {
        "intent": Intent.UNKNOWN.value,
        "pickup": None,
        "destination": None,
        "passengers": None,
        "vehicle_type": None,
        "accessibility_required": False,
        "min_seats": None,
        "requested_time": None,
        "date": None,
        "follow_up": False,
    }

    # Bare replies while clarifying
    if prior.pending_clarification == "passengers":
        m = re.fullmatch(r"\s*(\d{1,2})\s*", message)
        if m:
            out["passengers"] = int(m.group(1))
            out["intent"] = (
                prior.intent.value
                if prior.intent != Intent.UNKNOWN
                else Intent.FARE_ESTIMATE.value
            )
            out["follow_up"] = True
            return out

    if prior.pending_clarification == "vehicle_type" or (
        prior.pickup and prior.destination and prior.passengers
    ):
        matched = _match_vehicle_in_message(
            message,
            allow_weak=prior.pending_clarification == "vehicle_type",
        )
        if matched:
            out["vehicle_type"] = matched
            out["intent"] = Intent.FARE_ESTIMATE.value
            out["follow_up"] = True
            if prior.pending_clarification == "vehicle_type":
                return out
        if re.search(r"\bwhat about\b", q) and out["vehicle_type"]:
            out["follow_up"] = True
            out["intent"] = Intent.FARE_ESTIMATE.value

    # Follow-ups referring to prior journey
    if prior.pickup and prior.destination:
        if re.search(r"\b(how long|how much|what about|and for|what would)\b", q):
            out["follow_up"] = True
        if re.search(r"\bhow long\b", q) and not _has_new_route(q):
            out["intent"] = Intent.JOURNEY_ESTIMATE.value
            out["follow_up"] = True
            return out
        if re.search(r"\b(how much|cost|fare|price|£)\b", q) and not _has_new_route(q):
            out["intent"] = Intent.FARE_ESTIMATE.value
            out["follow_up"] = True

    if re.search(r"\b(wheelchair|accessible)\b", q):
        out["accessibility_required"] = True
        if prior.pickup and prior.destination:
            out["intent"] = Intent.FARE_ESTIMATE.value
            out["follow_up"] = True
        else:
            out["intent"] = Intent.ACCESSIBILITY_SEARCH.value

    if re.search(r"\b(7[\s-]?seater|seven seater)\b", q):
        out["min_seats"] = 7
        out["vehicle_type"] = "XL"
        out["intent"] = (
            Intent.FARE_ESTIMATE.value
            if (prior.pickup and prior.destination) or _has_new_route(q)
            else Intent.VEHICLE_SEARCH.value
        )
    elif re.search(r"\b(6[\s-]?seater|six seater|mpv|people carrier)\b", q):
        out["min_seats"] = 6
        out["vehicle_type"] = "XL"
        if out["intent"] == Intent.UNKNOWN.value:
            out["intent"] = Intent.VEHICLE_SEARCH.value

    # Only explicit class names here — not weak words like "taxi" / "cab"
    if not out.get("vehicle_type"):
        matched = _match_vehicle_in_message(message, allow_weak=False)
        if matched:
            out["vehicle_type"] = matched

    m_pass = re.search(r"\b(\d+)\s*(people|passengers|pax|persons)\b", q)
    if m_pass:
        out["passengers"] = int(m_pass.group(1))
    else:
        # "we are 5", "we're 5", "ok now we ar 5", "party of 5"
        m_are = re.search(
            r"\b(?:we're|we\s+(?:are|ar|re)|party\s+of|there\s+are|there'?s)\s+(\d{1,2})\b",
            q,
        )
        m_for = re.search(r"\bfor\s+(\d+)\b", q)
        if m_are:
            out["passengers"] = int(m_are.group(1))
            if prior.pickup and prior.destination:
                out["follow_up"] = True
                out["intent"] = Intent.FARE_ESTIMATE.value
        elif m_for:
            out["passengers"] = int(m_for.group(1))
        elif re.fullmatch(r"\s*(\d{1,2})\s*", message) and prior.pickup and prior.destination:
            out["passengers"] = int(message.strip())
            out["follow_up"] = True
            out["intent"] = Intent.FARE_ESTIMATE.value

    if re.search(r"\btomorrow\b", q):
        out["date"] = "tomorrow"
    if re.search(r"\b(\d{1,2})\s*(am|pm|a\.m\.|p\.m\.)\b", q):
        m = re.search(r"\b(\d{1,2})\s*(am|pm|a\.m\.|p\.m\.)\b", q)
        assert m
        out["requested_time"] = f"{m.group(1)} {m.group(2)}"
    elif "morning" in q:
        out["requested_time"] = "morning"
    elif "afternoon" in q:
        out["requested_time"] = "afternoon"
    elif "evening" in q:
        out["requested_time"] = "evening"

    if re.search(r"\b(taxi vs|taxi versus|difference between|what is a (taxi|phv)|phv)\b", q):
        out["intent"] = Intent.POLICY.value
    elif re.search(r"\b(busiest|highest demand)\b", q):
        out["intent"] = Intent.DEMAND_ANALYSIS.value
    elif re.search(r"\b(why|hardly any|low availability|no cars)\b", q):
        out["intent"] = Intent.OPERATIONS_ANALYSIS.value
    elif re.search(r"\b(available|any cars|near|around)\b", q) and not _looks_like_fare(q) and not out["vehicle_type"]:
        out["intent"] = Intent.NEARBY_VEHICLES.value
    elif _looks_like_fare(q) or re.search(r"\b(wanna go|want to go|travel from|i need a car for)\b", q):
        out["intent"] = Intent.FARE_ESTIMATE.value
    elif out["vehicle_type"] and out["intent"] == Intent.UNKNOWN.value:
        out["intent"] = Intent.VEHICLE_SEARCH.value
    elif re.search(r"\b(how long|duration)\b", q):
        out["intent"] = Intent.JOURNEY_ESTIMATE.value

    pickup, dest = _extract_route(q)
    out["pickup"] = pickup
    out["destination"] = dest

    # "SUV from Heathrow" without destination
    if out["vehicle_type"] and not pickup and not dest:
        m = re.search(r"\bfrom\s+([a-z0-9 .'/-]{3,40})$", q)
        if m:
            out["pickup"] = m.group(1).strip(" .,")

    if prior.pending_clarification and out["intent"] == Intent.UNKNOWN.value:
        out["intent"] = prior.intent.value
        out["follow_up"] = True

    if out["intent"] == Intent.UNKNOWN.value and (pickup or dest):
        out["intent"] = Intent.FARE_ESTIMATE.value

    return out


def _looks_like_fare(q: str) -> bool:
    return bool(
        re.search(
            r"\b(how much|cost|fare|price|£|what would it (cost|be)|travel from|go from|what cost)\b",
            q,
        )
    )


def _has_new_route(q: str) -> bool:
    return bool(re.search(r"\bfrom\b.+\bto\b|\bto\b.+\bfrom\b", q))


def _extract_route(q: str) -> tuple[str | None, str | None]:
    m = re.search(
        r"\b(?:from|near|around|at)\s+(.+?)\s+(?:to|→|->)\s+(.+?)(?:\?|$|,|\.| for | tomorrow| with | in a | at )",
        q,
        flags=re.I,
    )
    if m:
        return m.group(1).strip(" .,"), m.group(2).strip(" .,")
    m = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+)$", q, flags=re.I)
    if m:
        dest = m.group(2).strip(" .,")
        # Strip trailing "for N people" / "tomorrow..."
        dest = re.split(r"\s+for\s+\d+|\s+tomorrow|\s+at\s+\d", dest, maxsplit=1)[0].strip(" .,")
        return m.group(1).strip(" .,"), dest
    m = re.search(r"\bhow much (?:from |for )(.+?)\s+to\s+(.+?)(?:\?|$)", q, flags=re.I)
    if m:
        return m.group(1).strip(" .,"), m.group(2).strip(" .,")
    m = re.search(r"\b(?:near|around|at|from)\s+([a-z0-9 .'/-]{3,40})$", q, flags=re.I)
    if m and not re.search(r"\bto\b", q):
        return m.group(1).strip(" .,"), None
    return None, None


def _merge(
    prior: ConversationState,
    extracted: dict[str, Any],
    message: str,
) -> ConversationState:
    state = prior.model_copy(deep=True)
    follow_up = bool(extracted.get("follow_up"))

    intent_raw = str(extracted.get("intent") or Intent.UNKNOWN.value).upper()
    try:
        new_intent = Intent(intent_raw)
    except ValueError:
        new_intent = Intent.UNKNOWN

    if new_intent != Intent.UNKNOWN:
        if follow_up and new_intent in {Intent.GENERAL_TAXI_QUERY, Intent.UNKNOWN}:
            pass
        elif follow_up and new_intent in {
            Intent.VEHICLE_SEARCH,
            Intent.ACCESSIBILITY_SEARCH,
        } and prior.intent == Intent.FARE_ESTIMATE:
            state.intent = Intent.FARE_ESTIMATE
        else:
            state.intent = new_intent
    elif state.intent == Intent.UNKNOWN:
        state.intent = new_intent

    if follow_up and prior.intent in {Intent.FARE_ESTIMATE, Intent.JOURNEY_ESTIMATE}:
        if extracted.get("vehicle_type") or "executive" in message.lower() or "suv" in message.lower():
            state.intent = Intent.FARE_ESTIMATE

    pickup = extracted.get("pickup")
    dest = extracted.get("destination")
    if pickup:
        state.pickup = LocationRef(raw=str(pickup))
    if dest:
        state.destination = LocationRef(raw=str(dest))

    pax_changed = False
    if extracted.get("passengers") is not None:
        try:
            new_pax = int(extracted["passengers"])
            # Passenger count change invalidates a prior vehicle pick so the
            # UI can re-offer eligible cars (with images) for the new party size.
            if state.passengers is not None and state.passengers != new_pax:
                state.vehicle_type = None
                pax_changed = True
            state.passengers = new_pax
        except (TypeError, ValueError):
            pass

    mentioned_vehicle = _match_vehicle_in_message(
        message,
        allow_weak=prior.pending_clarification == "vehicle_type",
    )
    if mentioned_vehicle:
        state.vehicle_type = mentioned_vehicle
    elif extracted.get("vehicle_type") and not pax_changed:
        vt = str(extracted["vehicle_type"])
        canon = VEHICLE_ALIASES.get(normalize_text(vt), vt.upper())
        # Skip weak guesses ("taxi"/"cab" → SEDAN) so choice cards still appear
        if prior.pending_clarification == "vehicle_type" or not _is_weak_vehicle_guess(
            message, canon
        ):
            state.vehicle_type = canon

    if extracted.get("accessibility_required"):
        state.accessibility_required = True
    if state.vehicle_type and "ACCESS" in state.vehicle_type.upper():
        state.accessibility_required = True
    if re.search(r"\b(wheelchair|accessible)\b", message.lower()):
        state.accessibility_required = True
        if not state.vehicle_type:
            state.vehicle_type = "ACCESSIBLE"
    if extracted.get("min_seats") is not None:
        try:
            state.min_seats = int(extracted["min_seats"])
        except (TypeError, ValueError):
            pass
    if extracted.get("requested_time"):
        state.requested_time = str(extracted["requested_time"])
    if extracted.get("date"):
        state.date = str(extracted["date"])

    # Bare number reply → passengers
    if prior.pending_clarification == "passengers":
        m = re.fullmatch(r"\s*(\d{1,2})\s*", message)
        if m:
            state.passengers = int(m.group(1))
            state.intent = (
                prior.intent if prior.intent != Intent.UNKNOWN else Intent.FARE_ESTIMATE
            )

    # Bare / follow-up vehicle reply (word-boundary; class ids from cards)
    if prior.pending_clarification == "vehicle_type" or (
        prior.pickup and prior.destination and prior.passengers and follow_up
    ):
        matched = _match_vehicle_in_message(
            message,
            allow_weak=prior.pending_clarification == "vehicle_type",
        )
        if matched:
            state.vehicle_type = matched
            state.intent = (
                prior.intent if prior.intent != Intent.UNKNOWN else Intent.FARE_ESTIMATE
            )

    return state


def _is_weak_vehicle_guess(message: str, canon: str) -> bool:
    """True when SEDAN likely came from a generic word like taxi/cab."""
    if canon != "SEDAN":
        return False
    q = message.lower()
    # Explicit sedan/saloon names are fine
    if re.search(r"\b(sedan|saloon|standard sedan)\b", q):
        return False
    # Generic taxi wording should not lock the class and skip choice cards
    if re.search(r"\b(taxi|cab|black cab|standard)\b", q):
        return True
    # LLM invented SEDAN with no vehicle words in the message
    if not _match_vehicle_in_message(message, allow_weak=False):
        return True
    return False
