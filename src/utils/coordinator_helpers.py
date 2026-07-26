"""Pure coordinator helper functions — validation, budget, cost tracking, assembly."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ── Validation ────────────────────────────────────────────────────────────────

def validate(request, needed: dict | None = None) -> list[str]:
    issues = []
    if not (request.destination or "").strip():
        issues.append("destination is missing")
    flights_needed = (needed or {}).get("flights", True)
    if flights_needed and not (request.origin or "").strip():
        issues.append("origin is missing")
    if not request.start_date:
        issues.append("start_date is missing")
    if not request.end_date:
        issues.append("end_date is missing")
    if request.start_date and request.end_date and request.start_date >= request.end_date:
        issues.append("start_date must be before end_date")
    if request.total_budget <= 0:
        issues.append("total_budget must be greater than zero")
    return issues


# ── Agent selection ───────────────────────────────────────────────────────────

def needed_agents(request) -> dict[str, bool]:
    return {"flights": True, "attractions": True, "hotel": True, "transport": True}


# ── Budget split ──────────────────────────────────────────────────────────────

def split_budget(total: float, needed: dict[str, bool], budget_split: dict[str, float]) -> dict[str, float]:
    """Distribute the full budget among active agents only."""
    agent_keys = ("flights", "attractions", "hotel", "transport")
    active = {k: v for k, v in budget_split.items() if k in agent_keys and needed.get(k, True)}
    scale = sum(active.values()) or 1.0
    return {k: round(total * r / scale, 2) for k, r in active.items()}


# ── Cost accounting ───────────────────────────────────────────────────────────

def record_costs(session, results: dict, needed: dict[str, bool]) -> None:
    """Tally agent costs from the results dict returned by coordinator."""
    session.spent = 0.0
    session.agent_results.clear()
    if needed["flights"] and results.get("flights"):
        session.record_cost("flights", results["flights"].get("cost", 0))
    if results.get("attractions"):
        session.record_cost("attractions", results["attractions"].get("total_cost", 0))
    if needed["hotel"] and results.get("hotel"):
        session.record_cost(
            "hotel",
            results["hotel"].get("recommended_hotel", {}).get("total_cost", 0),
        )
    if needed["transport"] and results.get("transport"):
        session.record_cost("transport", results["transport"].get("total_cost", 0))


# ── Final assembly ────────────────────────────────────────────────────────────

def assemble(session, results: dict, request) -> dict:
    """Build the final itinerary dict from session accounting and agent results."""
    return {
        "session_id":    session.session_id,
        "destination":   request.destination,
        "dates":         f"{request.start_date} to {request.end_date}",
        "total_budget":  request.total_budget,
        "total_spent":   session.spent,
        "within_budget": session.within_budget(),
        "budget_breakdown": {
            k: session.agent_results.get(k, {}).get("cost", 0)
            for k in ("flights", "attractions", "hotel", "transport")
        },
        "budget_caps": dict(session.per_agent_caps),
        "flights":     results.get("flights"),
        "attractions": results.get("attractions"),
        "hotel":       results.get("hotel"),
        "transport":   results.get("transport"),
    }
