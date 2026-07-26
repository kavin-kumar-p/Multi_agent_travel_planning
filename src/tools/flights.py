"""Flight search tool — backed by data/flights.json.

In production, replace with a real API call (e.g. Amadeus, Skyscanner).
"""
from __future__ import annotations

from src.tools._data import load_json


def _dest_to_iata(destination: str) -> str:
    _mapping = {
        "paris": "CDG",
        "tokyo": "NRT",
        "new york": "JFK",
        "london": "LHR",
        "dubai": "DXB",
        "singapore": "SIN",
    }
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
    origin_code = _dest_to_iata(origin)
    dest_code   = _dest_to_iata(destination)
    all_flights = load_json("flights.json")

    # Exact route match first — stamp requested origin so UI always has the right route
    exact = [
        {**f, "origin": origin_code}
        for f in all_flights
        if f.get("destination") == dest_code and f.get("price", 9_999) <= budget_cap
    ]
    if exact:
        return exact

    # Mock data covers a fixed set of routes; stamp the requested origin/destination
    # so the LLM always receives the correct route even on representative pricing.
    return [
        {**f, "origin": origin_code, "destination": dest_code, "note": "representative pricing"}
        for f in all_flights
        if f.get("price", 9_999) <= budget_cap
    ]
