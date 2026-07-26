"""Shared dataclass models for the coordinator.

Extracted to break the coordinator ↔ agent circular import:
  coordinator.py imports TravelRequest from here.
  Neither side imports the other.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TravelRequest:
    origin: str
    destination: str
    start_date: str
    end_date: str
    total_budget: float
    interests: list[str] = field(default_factory=list)
    user_query: str = ""
    budget_split: dict | None = None
