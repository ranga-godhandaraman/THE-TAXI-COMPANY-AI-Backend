"""Deterministic session title generation."""

from __future__ import annotations

from app.chat_history.titles import generate_session_title


def test_title_from_heathrow_mayfair() -> None:
    assert (
        generate_session_title("I need a car from Heathrow to Mayfair.")
        == "Heathrow to Mayfair"
    )


def test_title_from_didsbury_birmingham() -> None:
    assert (
        generate_session_title("I want to travel from Didsbury to Birmingham.")
        == "Didsbury to Birmingham"
    )


def test_title_suv_fallback() -> None:
    assert generate_session_title("I need an SUV for 4 people tomorrow evening") == (
        "SUV booking"
    )


def test_title_clips_long() -> None:
    long = "Please arrange something " + ("very long " * 20)
    title = generate_session_title(long)
    assert len(title) <= 60
