"""MCP tool implementations — backed by the local data/ JSON files.

These are called by agents as their "live data" layer. In production,
replace the JSON file reads with real API calls (Amadeus, Booking.com, etc.).
"""
from __future__ import annotations

import json
from pathlib import Path

_DATA = Path(__file__).parent.parent.parent / "data"


def _load(filename: str) -> list[dict]:
    return json.loads((_DATA / filename).read_text(encoding="utf-8"))


def _dest_to_iata(destination: str) -> str:
    _mapping = {"paris": "CDG", "tokyo": "NRT", "new york": "JFK"}
    dest_lower = destination.lower()
    for key, code in _mapping.items():
        if key in dest_lower:
            return code
    return destination.upper()[:3]


def search_flights(
    origin: str,
    destination: str,
    start_date: str,
    end_date: str,
    budget_cap: float,
) -> list[dict]:
    dest_code = _dest_to_iata(destination)
    return [
        f for f in _load("flights.json")
        if f.get("destination") == dest_code and f.get("price", 9999) <= budget_cap
    ]


def search_hotels(destination: str, budget_per_night: float) -> list[dict]:
    return [
        h for h in _load("hotels.json")
        if destination.lower() in h.get("destination", "").lower()
        and h.get("price_per_night", 9999) <= budget_per_night
    ]


def search_transit(destination: str) -> list[dict]:
    return [
        t for t in _load("transit.json")
        if destination.lower() in t.get("destination", "").lower()
    ]
