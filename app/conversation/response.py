"""Human-facing response phrasing for the taxi assistant."""

from __future__ import annotations

from app.conversation.models import ConversationState, DomainResult, Intent


def clarification_message(state: ConversationState) -> tuple[str, str] | None:
    """
    Non-journey clarifications only.

    Journey planning uses conversation.journey_flow (asks for passengers /
    vehicle choice before calling the pricing engine).
    """
    # Location clarifications for unresolved places
    for loc, role in ((state.pickup, "pickup"), (state.destination, "destination")):
        if loc and loc.needs_clarification and loc.clarification_prompt:
            if not loc.resolved or loc.confidence < 0.6:
                return loc.clarification_prompt, f"location_{role}"

    if intent_is_fleet(state.intent):
        loc = state.pickup or state.destination
        if not loc or not loc.resolved:
            return "Which area should I check for cars?", "pickup"

    return None


def intent_is_fleet(intent: Intent) -> bool:
    return intent in {
        Intent.NEARBY_VEHICLES,
        Intent.VEHICLE_AVAILABILITY,
        Intent.FLEET_SEARCH,
        Intent.ACCESSIBILITY_SEARCH,
    }


def compose_answer(
    *,
    state: ConversationState,
    domain: DomainResult | None,
    clarification: str | None = None,
) -> str:
    if clarification:
        return clarification
    if domain is None:
        return _unsupported(state)
    return domain.summary.strip()


def _unsupported(state: ConversationState) -> str:
    if state.intent == Intent.UNKNOWN:
        return (
            "I can help with journey fares and times, cars near an area, "
            "wheelchair-accessible vehicles, and taxi/PHV policy questions. "
            "What would you like to know?"
        )
    return (
        "I understood the request, but I can't complete that particular action yet. "
        "I can estimate fares, check nearby cars, or explain taxi/PHV policy."
    )


def public_route_label(intent: Intent, domain: str | None) -> str:
    """User-facing route label (not SQL/RAG jargon)."""
    mapping = {
        Intent.FARE_ESTIMATE: "journey",
        Intent.JOURNEY_ESTIMATE: "journey",
        Intent.BOOKING_REQUEST: "booking",
        Intent.VEHICLE_AVAILABILITY: "fleet",
        Intent.NEARBY_VEHICLES: "fleet",
        Intent.VEHICLE_SEARCH: "journey",
        Intent.ACCESSIBILITY_SEARCH: "fleet",
        Intent.FLEET_SEARCH: "fleet",
        Intent.TRIP_LOOKUP: "trips",
        Intent.POLICY: "policy",
        Intent.OPERATIONS_ANALYSIS: "operations",
        Intent.DEMAND_ANALYSIS: "operations",
        Intent.GENERAL_TAXI_QUERY: "assistant",
        Intent.UNKNOWN: "assistant",
    }
    return mapping.get(intent, domain or "assistant")
