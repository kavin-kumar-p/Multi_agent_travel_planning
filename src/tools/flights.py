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
    dest_code = _dest_to_iata(destination)
    all_flights = load_json("flights.json")

    # Exact route match first
    exact = [
        f for f in all_flights
        if f.get("destination") == dest_code and f.get("price", 9_999) <= budget_cap
    ]
    if exact:
        return exact

    # Mock data covers a fixed set of routes; return budget-matched flights so the
    # LLM always has real pricing to reason about instead of an empty list.
    return [
        {**f, "destination": dest_code, "note": "representative pricing"}
        for f in all_flights
        if f.get("price", 9_999) <= budget_cap
    ]
