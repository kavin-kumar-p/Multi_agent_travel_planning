"""Transit search tool — backed by data/transit.json.

In production, replace with a real API call (e.g. Rome2Rio, Google Maps).
"""
from __future__ import annotations

from src.tools._data import load_json


def search_transit(destination: str) -> list[dict]:
    return [
        t for t in load_json("transit.json")
        if destination.lower() in t.get("destination", "").lower()
    ]
