"""Hotel search tool — backed by data/hotels.json.

In production, replace with a real API call (e.g. Booking.com, Expedia).
"""
from __future__ import annotations

from src.tools._data import load_json


def search_hotels(destination: str, budget_per_night: float) -> list[dict]:
    return [
        h for h in load_json("hotels.json")
        if destination.lower() in h.get("destination", "").lower()
        and h.get("price_per_night", 9_999) <= budget_per_night
    ]
