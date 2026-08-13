"""Deterministic chat session titles (no LLM)."""

from __future__ import annotations

import re

_MAX = 60

_FROM_TO = re.compile(
    r"\b(?:from|near|around|at)\s+(.+?)\s+(?:to|→|->)\s+(.+?)(?:\?|$|,|\.| for | tomorrow| with | in a | at | for\b)",
    flags=re.I,
)
_FROM_TO_END = re.compile(
    r"\bfrom\s+(.+?)\s+to\s+(.+)$",
    flags=re.I,
)


def generate_session_title(message: str) -> str:
    """
    Build a short title from the first meaningful user message.

    Prefer "Pickup to Destination". Fall back to a short snippet / vehicle hint.
    """
    text = (message or "").strip()
    if not text:
        return "New Booking"

    route = _extract_route(text)
    if route:
        return _clip(f"{route[0]} to {route[1]}")

    low = text.lower()
    if re.search(r"\b(suv)\b", low):
        return "SUV booking"
    if re.search(r"\b(executive)\b", low):
        return "Executive booking"
    if re.search(r"\b(wheelchair|accessible)\b", low):
        return "Accessible booking"
    if re.search(r"\b(7[\s-]?seater|xl|mpv)\b", low):
        return "XL booking"
    if re.search(r"\b(sedan|saloon)\b", low):
        return "Sedan booking"

    # First meaningful chunk
    cleaned = re.sub(r"\s+", " ", text)
    cleaned = re.sub(
        r"^(i need|i want|i'd like|please|can you|could you)\s+",
        "",
        cleaned,
        flags=re.I,
    ).strip(" .,!?")
    if not cleaned:
        cleaned = text
    return _clip(cleaned[0].upper() + cleaned[1:] if cleaned else "New Booking")


def _extract_route(text: str) -> tuple[str, str] | None:
    m = _FROM_TO.search(text)
    if m:
        return _clean_place(m.group(1)), _clean_place(m.group(2))
    m = _FROM_TO_END.search(text)
    if m:
        dest = re.split(
            r"\s+for\s+\d+|\s+tomorrow|\s+at\s+\d|\s+with\s+",
            m.group(2),
            maxsplit=1,
        )[0]
        return _clean_place(m.group(1)), _clean_place(dest)
    return None


def _clean_place(raw: str) -> str:
    place = re.sub(r"\s+", " ", raw).strip(" .,!?")
    # Title-case short place labels lightly
    if place.isupper() or place.islower():
        place = place.title()
    return place


def _clip(title: str) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    if len(title) <= _MAX:
        return title
    return title[: _MAX - 1].rstrip() + "…"
