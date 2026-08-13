"""Location aliases and informal UK place-name normalisation."""

from __future__ import annotations

import re

# Lowercase alias → preferred display / match string
LOCATION_ALIASES: dict[str, str] = {
    "brum": "Birmingham",
    "birmingham": "Birmingham",
    "birmingham city centre": "Birmingham City Centre",
    "birmingham centre": "Birmingham City Centre",
    "bham": "Birmingham",
    "manchester airport": "Manchester Airport",
    "man airport": "Manchester Airport",
    "manc airport": "Manchester Airport",
    "heathrow": "Heathrow",
    "heathrow airport": "Heathrow",
    "lhr": "Heathrow",
    "gatwick": "Gatwick",
    "lgw": "Gatwick",
    "westminster": "Westminster",
    "central london": "Westminster",
    "london city centre": "City of London",
    "city of london": "City of London",
    "manchester city centre": "Manchester City Centre",
    "manchester centre": "Manchester City Centre",
    "manchester": "Manchester",
    "didsbury": "Didsbury",
    "salford": "Salford",
    "trafford": "Trafford",
    "camden": "Camden",
    "canary wharf": "Canary Wharf",
    "king's cross": "King's Cross",
    "kings cross": "King's Cross",
    "paddington": "Paddington",
    "stratford": "Stratford",
    "leeds bradford airport": "Leeds Bradford Airport",
    "birmingham airport": "Birmingham Airport",
    "liverpool airport": "Liverpool Airport",
}

CITY_ALIASES: dict[str, str] = {
    "brum": "Birmingham",
    "bham": "Birmingham",
    "manc": "Manchester",
    "london": "London",
    "manchester": "Manchester",
    "birmingham": "Birmingham",
    "leeds": "Leeds",
    "liverpool": "Liverpool",
}

VEHICLE_ALIASES: dict[str, str] = {
    "executive": "EXECUTIVE",
    "exec": "EXECUTIVE",
    "executive car": "EXECUTIVE",
    "saloon": "SEDAN",
    "sedan": "SEDAN",
    "standard sedan": "SEDAN",
    "standard": "SEDAN",
    "suv": "SUV",
    "black cab": "SEDAN",
    "taxi": "SEDAN",
    "cab": "SEDAN",
    "mpv": "XL",
    "xl": "XL",
    "7 seater": "XL",
    "7-seater": "XL",
    "seven seater": "XL",
    "6 seater": "XL",
    "6-seater": "XL",
    "six seater": "XL",
    "people carrier": "XL",
    "accessible": "ACCESSIBLE",
    "wheelchair": "ACCESSIBLE",
    "wheelchair accessible": "ACCESSIBLE",
}

# Too generic for auto vehicle picks ("book a taxi", "standard journey").
# Still used when the user is answering a vehicle_type clarification.
WEAK_VEHICLE_ALIASES: frozenset[str] = frozenset(
    {"taxi", "cab", "standard", "black cab"}
)


def normalize_text(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"[^\w\s'/-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def apply_location_alias(raw: str) -> str:
    key = normalize_text(raw)
    return LOCATION_ALIASES.get(key, raw.strip())
