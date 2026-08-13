"""Multi-turn journey-planning acceptance tests (pricing engine only for fares)."""

from __future__ import annotations

import pytest

from app.conversation import reset_all, run_turn
from app.conversation.models import Intent
from app.db.session import dispose_engine


@pytest.fixture(autouse=True)
async def _clear_memory_and_engine() -> None:
    reset_all()
    yield
    reset_all()
    await dispose_engine()


def _assert_no_internals(answer: str) -> None:
    low = answer.lower()
    for banned in ("sql", "postgresql", "langgraph", "qdrant", "vector search", "database"):
        assert banned not in low


@pytest.mark.asyncio
async def test_acceptance_heathrow_westminster_suv_then_executive() -> None:
    """Tests 1–4: route → passengers → SUV → switch to executive."""
    t1 = await run_turn("How much from Heathrow to Westminster?")
    assert t1.intent == Intent.FARE_ESTIMATE
    assert t1.state.pickup and t1.state.pickup.resolved == "Heathrow"
    assert t1.state.destination and t1.state.destination.resolved == "Westminster"
    assert t1.state.passengers is None
    assert "people" in t1.answer.lower() or "passenger" in t1.answer.lower()
    assert "£" not in t1.answer
    _assert_no_internals(t1.answer)

    t2 = await run_turn("3", conversation_id=t1.conversation_id)
    assert t2.state.passengers == 3
    assert t2.state.pickup and t2.state.pickup.resolved == "Heathrow"
    assert t2.state.destination and t2.state.destination.resolved == "Westminster"
    low = t2.answer.lower()
    assert "sedan" in low or "standard" in low
    assert "suv" in low
    assert "executive" in low
    assert "£" not in t2.answer
    _assert_no_internals(t2.answer)

    t3 = await run_turn("SUV", conversation_id=t1.conversation_id)
    assert t3.state.vehicle_type == "SUV"
    assert t3.state.passengers == 3
    assert "£" in t3.answer
    assert "heathrow" in t3.answer.lower()
    assert "westminster" in t3.answer.lower()
    assert "estimate" in t3.answer.lower()
    _assert_no_internals(t3.answer)

    t4 = await run_turn("What about executive?", conversation_id=t1.conversation_id)
    assert t4.state.vehicle_type == "EXECUTIVE"
    assert t4.state.passengers == 3
    assert t4.state.pickup and t4.state.pickup.resolved == "Heathrow"
    assert t4.state.destination and t4.state.destination.resolved == "Westminster"
    assert "£" in t4.answer
    assert "executive" in t4.answer.lower()
    _assert_no_internals(t4.answer)


@pytest.mark.asyncio
async def test_passenger_change_reoffers_vehicle_options() -> None:
    """After a quote, changing party size clears the old class and shows cars again."""
    t1 = await run_turn("How much from Heathrow to Westminster?")
    t2 = await run_turn("2", conversation_id=t1.conversation_id)
    assert "£" not in t2.answer
    assert t2.data and len(t2.data) > 1
    assert t2.metadata.get("vehicle_options") is True

    t3 = await run_turn("SEDAN", conversation_id=t1.conversation_id)
    assert "£" in t3.answer
    assert t3.state.vehicle_type == "SEDAN"

    t4 = await run_turn("ok now we are 5", conversation_id=t1.conversation_id)
    assert t4.state.passengers == 5
    assert t4.state.vehicle_type is None
    assert "£" not in t4.answer
    assert t4.data and len(t4.data) > 1
    assert t4.metadata.get("vehicle_options") is True
    assert t4.metadata.get("pending_clarification") == "vehicle_type"
    ids = {str(row.get("vehicle_class_id", "")).upper() for row in t4.data}
    assert "SEDAN" not in ids
    assert "SUV" in ids or "XL" in ids
    _assert_no_internals(t4.answer)


@pytest.mark.asyncio
async def test_acceptance_six_people_didsbury_birmingham() -> None:
    """Test 5: six passengers → XL / 7-seater path without sedan options."""
    turn = await run_turn(
        "How much from Didsbury to Birmingham for 6 people?"
    )
    assert turn.state.passengers == 6
    assert turn.state.pickup and "Didsbury" in (turn.state.pickup.resolved or "")
    assert turn.state.destination and "Birmingham" in (turn.state.destination.resolved or "")
    low = turn.answer.lower()
    # Should quote XL (only class) or ask only for XL — not sedan/suv for 6
    assert "sedan" not in low
    assert "suv" not in low or "xl" in low or "£" in turn.answer
    if "£" in turn.answer:
        assert turn.state.vehicle_type == "XL"
        assert "estimate" in low
    else:
        assert "xl" in low or "7" in low or "seater" in low
    _assert_no_internals(turn.answer)


@pytest.mark.asyncio
async def test_acceptance_wheelchair_keeps_route() -> None:
    """Test 6: accessibility follow-up keeps prior journey slots."""
    first = await run_turn("How much from Heathrow to Westminster?")
    await run_turn("3", conversation_id=first.conversation_id)
    turn = await run_turn(
        "I need a wheelchair accessible vehicle.",
        conversation_id=first.conversation_id,
    )
    assert turn.state.accessibility_required is True
    assert turn.state.pickup and turn.state.pickup.resolved == "Heathrow"
    assert turn.state.destination and turn.state.destination.resolved == "Westminster"
    assert turn.state.passengers == 3
    low = turn.answer.lower()
    assert "£" in turn.answer or "access" in low or "wheelchair" in low
    _assert_no_internals(turn.answer)


@pytest.mark.asyncio
async def test_acceptance_seven_seater_followup() -> None:
    """Test 7: 7-seater preference on an existing route."""
    first = await run_turn(
        "How much from Manchester Airport to Birmingham for 6 people?"
    )
    turn = await run_turn("Can I get a 7 seater?", conversation_id=first.conversation_id)
    assert turn.state.min_seats == 7 or turn.state.vehicle_type == "XL"
    assert turn.state.pickup and turn.state.destination
    assert "£" in turn.answer or "xl" in turn.answer.lower() or "seater" in turn.answer.lower()
    _assert_no_internals(turn.answer)


@pytest.mark.asyncio
async def test_acceptance_tomorrow_eight_am_asks_passengers() -> None:
    """Test 8: datetime captured; still ask for passengers before quoting."""
    turn = await run_turn(
        "How much from Manchester Airport to Birmingham tomorrow at 8 AM?"
    )
    assert turn.state.date == "tomorrow"
    assert turn.state.requested_time
    assert "8" in turn.state.requested_time
    assert turn.state.pickup and "Manchester Airport" in (turn.state.pickup.resolved or "")
    assert turn.state.destination and "Birmingham" in (turn.state.destination.resolved or "")
    assert turn.state.passengers is None
    assert "people" in turn.answer.lower() or "passenger" in turn.answer.lower()
    assert "£" not in turn.answer
    _assert_no_internals(turn.answer)
