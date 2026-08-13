"""Conversational assistant acceptance scenarios."""

from __future__ import annotations

import pytest

from app.conversation import reset_all, run_turn
from app.conversation.models import ConversationState, Intent
from app.conversation.nlu import understand
from app.db.session import dispose_engine


@pytest.fixture(autouse=True)
async def _clear_memory_and_engine() -> None:
    reset_all()
    yield
    reset_all()
    await dispose_engine()


@pytest.mark.asyncio
async def test_01_fare_didsbury_birmingham_asks_passengers() -> None:
    turn = await run_turn(
        "I wanna travel from Didsbury to Birmingham, what cost could I get"
    )
    assert turn.intent == Intent.FARE_ESTIMATE
    assert turn.route == "journey"
    assert turn.state.pickup and "Didsbury" in (turn.state.pickup.resolved or "")
    assert turn.state.destination and "Birmingham" in (
        turn.state.destination.resolved or ""
    )
    assert turn.state.passengers is None
    assert "people" in turn.answer.lower() or "passenger" in turn.answer.lower()
    assert "couldn't complete" not in turn.answer.lower()


@pytest.mark.asyncio
async def test_02_fare_heathrow_westminster_asks_passengers() -> None:
    turn = await run_turn("How much from Heathrow to Westminster?")
    assert turn.intent == Intent.FARE_ESTIMATE
    assert turn.state.pickup and turn.state.pickup.resolved == "Heathrow"
    assert turn.state.destination and turn.state.destination.resolved == "Westminster"
    assert "people" in turn.answer.lower() or "passenger" in turn.answer.lower()


@pytest.mark.asyncio
async def test_03_followup_how_long_after_quote() -> None:
    first = await run_turn("How much from Heathrow to Westminster?")
    cid = first.conversation_id
    await run_turn("3", conversation_id=cid)
    quoted = await run_turn("SUV", conversation_id=cid)
    assert "£" in quoted.answer
    second = await run_turn("How long will it take?", conversation_id=cid)
    assert second.intent in {Intent.JOURNEY_ESTIMATE, Intent.FARE_ESTIMATE}
    assert "minute" in second.answer.lower() or "miles" in second.answer.lower() or "h" in second.answer.lower()
    assert second.state.pickup and second.state.pickup.resolved == "Heathrow"


@pytest.mark.asyncio
async def test_04_followup_executive() -> None:
    first = await run_turn("How much from Didsbury to Birmingham?")
    cid = first.conversation_id
    await run_turn("3", conversation_id=cid)
    second = await run_turn("What about an executive car?", conversation_id=cid)
    assert second.state.vehicle_type == "EXECUTIVE"
    assert second.state.pickup and "Didsbury" in (second.state.pickup.resolved or "")
    assert "£" in second.answer or "estimate" in second.answer.lower()


@pytest.mark.asyncio
async def test_05_wheelchair() -> None:
    turn = await run_turn("I need a wheelchair accessible car near Heathrow")
    assert turn.state.accessibility_required is True
    assert turn.intent in {
        Intent.ACCESSIBILITY_SEARCH,
        Intent.NEARBY_VEHICLES,
        Intent.VEHICLE_SEARCH,
        Intent.FLEET_SEARCH,
    }
    assert (
        "wheelchair" in turn.answer.lower()
        or "accessible" in turn.answer.lower()
        or "available" in turn.answer.lower()
        or "pick" in turn.answer.lower()
    )


@pytest.mark.asyncio
async def test_06_cars_near_heathrow() -> None:
    turn = await run_turn("Any cars available near Heathrow?")
    assert turn.route == "fleet"
    assert "heathrow" in turn.answer.lower()
    assert "couldn't complete" not in turn.answer.lower()


@pytest.mark.asyncio
async def test_07_six_seater_manchester_airport() -> None:
    turn = await run_turn("Can I get a 6 seater from Manchester Airport?")
    assert turn.state.min_seats == 6 or turn.state.vehicle_type == "XL"
    assert turn.state.pickup and "Manchester Airport" in (turn.state.pickup.resolved or "")
    assert "couldn't complete" not in turn.answer.lower()
    assert turn.answer


@pytest.mark.asyncio
async def test_08_taxi_vs_phv() -> None:
    turn = await run_turn("What is the difference between a taxi and PHV?")
    assert turn.intent == Intent.POLICY
    assert turn.route == "policy"
    assert turn.sources or "taxi" in turn.answer.lower() or "phv" in turn.answer.lower()


@pytest.mark.asyncio
async def test_09_why_heathrow_low() -> None:
    turn = await run_turn("Why are there hardly any cars at Heathrow?")
    assert turn.intent in {Intent.OPERATIONS_ANALYSIS, Intent.DEMAND_ANALYSIS}
    assert turn.answer
    assert "couldn't complete" not in turn.answer.lower()


@pytest.mark.asyncio
async def test_10_fare_with_passengers_offers_vehicles() -> None:
    turn = await run_turn(
        "How much would it be for 3 people from Didsbury to Birmingham?"
    )
    assert turn.intent == Intent.FARE_ESTIMATE
    assert turn.state.passengers == 3
    low = turn.answer.lower()
    # Multiple classes → choose; do not invent a fare yet
    assert "sedan" in low or "suv" in low or "executive" in low
    assert "£" not in turn.answer


@pytest.mark.asyncio
async def test_11_booking_tomorrow() -> None:
    turn = await run_turn(
        "I want to go from Manchester Airport to Birmingham tomorrow morning"
    )
    assert turn.state.date == "tomorrow" or turn.intent in {
        Intent.BOOKING_REQUEST,
        Intent.FARE_ESTIMATE,
        Intent.JOURNEY_ESTIMATE,
    }
    assert turn.state.pickup and turn.state.destination
    assert "couldn't complete" not in turn.answer.lower()
    assert "people" in turn.answer.lower() or "passenger" in turn.answer.lower() or "£" in turn.answer


@pytest.mark.asyncio
async def test_12_how_much_followup() -> None:
    first = await run_turn(
        "I want to go from Manchester Airport to Birmingham tomorrow morning"
    )
    cid = first.conversation_id
    await run_turn("2", conversation_id=cid)
    second = await run_turn("SUV", conversation_id=cid)
    assert second.state.pickup and second.state.destination
    assert "£" in second.answer or "estimate" in second.answer.lower()


def test_nlu_brum_alias() -> None:
    state = understand(
        "how much from didsbury to brum",
        ConversationState(),
    )
    assert state.pickup and "didsbury" in (state.pickup.raw or "").lower()
    assert state.destination and "brum" in (state.destination.raw or "").lower()
    assert state.intent == Intent.FARE_ESTIMATE
