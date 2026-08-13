"""Soft booking request handling for the conversational assistant (POC)."""

from __future__ import annotations

from app.conversation.models import ConversationState, DomainResult


async def handle_booking_request(state: ConversationState) -> DomainResult:
    pickup = state.pickup.resolved if state.pickup else None
    destination = state.destination.resolved if state.destination else None
    when = " ".join(x for x in [state.date, state.requested_time] if x) or "as soon as possible"
    vehicle = state.vehicle_type or ("accessible vehicle" if state.accessibility_required else "standard car")
    seats = f", {state.min_seats}+ seats" if state.min_seats else ""
    pax = f" for {state.passengers} passengers" if state.passengers else ""

    if pickup and destination:
        summary = (
            f"I can help arrange a {vehicle}{seats} from {pickup} to {destination}{pax} "
            f"({when}). This assistant can take the details and estimate the journey, "
            "but it cannot place a live booking yet. "
            "Would you like a fare estimate for that trip?"
        )
    elif pickup:
        summary = (
            f"I can look into a {vehicle} near {pickup}{pax} ({when}). "
            "Where would you like to go?"
        )
    else:
        summary = (
            "Happy to help with a car. Where should we pick you up, and where are you going?"
        )

    return DomainResult(
        domain="booking",
        summary=summary,
        data=[
            {
                "pickup": pickup,
                "destination": destination,
                "when": when,
                "vehicle_type": vehicle,
                "passengers": state.passengers,
                "status": "request_noted",
            }
        ],
        meta={"booking_supported": False},
    )
