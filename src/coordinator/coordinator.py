"""Coordinator Agent — Google ADK orchestration layer.

Responsibilities:
- Split the user's budget into per-agent caps.
- Dispatch agents in order: Flight → Attractions → Hotel → Transport.
- Track spend via TravelSession.
- Assemble and return the final itinerary.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.coordinator.session import TravelSession

logger = logging.getLogger(__name__)

_BUDGET_SPLIT: dict[str, float] = {
    "flights": 0.35,
    "attractions": 0.15,
    "hotel": 0.30,
    "transport": 0.10,
    # 0.10 held as buffer
}


@dataclass
class TravelRequest:
    origin: str
    destination: str
    start_date: str
    end_date: str
    total_budget: float
    interests: list[str] = field(default_factory=list)


def _split_budget(total: float) -> dict[str, float]:
    return {agent: round(total * ratio, 2) for agent, ratio in _BUDGET_SPLIT.items()}


async def run(request: TravelRequest, rag_stores: dict) -> dict:
    """Orchestrate all agents and return the assembled itinerary."""
    session = TravelSession(total_budget=request.total_budget)
    session.per_agent_caps = _split_budget(request.total_budget)
    caps = session.per_agent_caps

    logger.info(
        "Session %s | destination=%s | budget=$%.2f | caps=%s",
        session.session_id, request.destination, request.total_budget, caps,
    )

    # ── 1. Flight Agent ───────────────────────────────────────────────────────
    from src.agents.flight import run_flight_agent
    flight_result = await run_flight_agent(
        rag_stores=rag_stores,
        origin=request.origin,
        destination=request.destination,
        start_date=request.start_date,
        end_date=request.end_date,
        budget_cap=caps["flights"],
    )
    session.record_cost("flights", flight_result.get("cost", 0))
    session.agent_results["flights"] = flight_result

    confirmed_dates = flight_result.get("confirmed_dates", f"{request.start_date} to {request.end_date}")

    # ── 2. Attractions Agent ──────────────────────────────────────────────────
    from src.agents.attractions import run_attractions_agent
    attractions_result = await run_attractions_agent(
        rag_stores=rag_stores,
        destination=request.destination,
        confirmed_dates=confirmed_dates,
        interests=request.interests,
        budget_cap=caps["attractions"],
    )
    session.record_cost("attractions", attractions_result.get("total_cost", 0))
    session.agent_results["attractions"] = attractions_result

    # ── 3. Hotel Agent ────────────────────────────────────────────────────────
    from src.agents.hotel import run_hotel_agent
    hotel_result = await run_hotel_agent(
        rag_stores=rag_stores,
        destination=request.destination,
        start_date=request.start_date,
        end_date=request.end_date,
        attraction_clusters=attractions_result.get("clusters", []),
        budget_cap=caps["hotel"],
    )
    hotel_cost = hotel_result.get("recommended_hotel", {}).get("total_cost", 0)
    session.record_cost("hotel", hotel_cost)
    session.agent_results["hotel"] = hotel_result

    hotel_location = hotel_result.get("recommended_hotel", {}).get("location", request.destination)

    # ── 4. Transport Agent ────────────────────────────────────────────────────
    from src.agents.transport import run_transport_agent
    transport_result = await run_transport_agent(
        rag_stores=rag_stores,
        destination=request.destination,
        hotel_location=hotel_location,
        attraction_clusters=attractions_result.get("clusters", []),
        confirmed_dates=confirmed_dates,
        budget_cap=caps["transport"],
    )
    session.record_cost("transport", transport_result.get("total_cost", 0))
    session.agent_results["transport"] = transport_result

    if not session.within_budget():
        logger.warning(
            "Session %s over budget: spent=$%.2f / total=$%.2f",
            session.session_id, session.spent, session.total_budget,
        )

    return _assemble(session, request)


def _assemble(session: TravelSession, request: TravelRequest) -> dict:
    return {
        "session_id": session.session_id,
        "destination": request.destination,
        "dates": f"{request.start_date} to {request.end_date}",
        "total_budget": request.total_budget,
        "total_spent": session.spent,
        "within_budget": session.within_budget(),
        "budget_breakdown": {
            agent: session.agent_results.get(agent, {}).get("cost", 0)
            for agent in ("flights", "attractions", "hotel", "transport")
        },
        "flights": session.agent_results.get("flights"),
        "attractions": session.agent_results.get("attractions"),
        "hotel": session.agent_results.get("hotel"),
        "transport": session.agent_results.get("transport"),
    }
