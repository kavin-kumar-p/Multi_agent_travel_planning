"""Shared dataclass models — imported by coordinator and all specialist agents.

Extracted to break the coordinator ↔ agent circular import:
  coordinator.py  imports models.py  (for TravelRequest, AgentContext)
  agents/*.py     import models.py   (for type hints)
  Neither side imports the other.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.coordinator.session import TravelSession


@dataclass
class TravelRequest:
    origin: str
    destination: str
    start_date: str
    end_date: str
    total_budget: float
    interests: list[str] = field(default_factory=list)
    confirmed_flight: dict | None = None
    confirmed_hotel: dict | None = None
    confirmed_transport: dict | None = None
    user_query: str = ""
    budget_split: dict | None = None


@dataclass
class AgentContext:
    """Shared mutable context threaded through every specialist agent (A2A pull pattern).

    Attractions reads flight_result  for confirmed_dates.
    Hotel       reads attractions_result for clusters.
    Transport   reads hotel_result   for hotel_location.
    """
    rag_stores: dict
    request: TravelRequest
    session: TravelSession
    flight_result: dict | None = None
    attractions_result: dict | None = None
    hotel_result: dict | None = None
    transport_result: dict | None = None
