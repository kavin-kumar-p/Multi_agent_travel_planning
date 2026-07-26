"""Coordinator — Google ADK routing + A2A protocol orchestration.

Architecture:
  Coordinator (this module, powered by Google ADK LlmAgent)
  ├── Phase 1: ADK LlmAgent reads user query and decides which agents to invoke
  ├── Phase 2: discovers agents via GET /.well-known/agent.json + GET /health
  └── Phase 3: triggers all needed agents in PARALLEL via POST /send_message

A2A agent services (all fired simultaneously):
  Flight Agent      http://127.0.0.1:8001  (LangGraph)
  Attractions Agent http://127.0.0.1:8002  (LangGraph)
  Hotel Agent       http://127.0.0.1:8003  (CrewAI)
  Transport Agent   http://127.0.0.1:8004  (CrewAI)

Each agent is autonomous — it calls peer agents directly via A2A when it
needs enrichment data:
  Attractions → asks Flight      (confirmed dates)
  Hotel       → asks Attractions (cluster areas)
  Transport   → asks Flight + Attractions + Hotel in parallel

The coordinator passes only the original request fields + session_id.
No upstream results are passed between agents by the coordinator.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from google import genai as google_genai
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from src.a2a.client import A2AClient
from src.a2a.launch import all_agent_urls
from src.config.constants import DEFAULT_BUDGET_SPLIT, RETRY_CAP_FACTOR
from src.config.settings import settings
from src.coordinator.models import TravelRequest
from src.coordinator.session import TravelSession
from src.prompts import load_prompt
from src.utils.coordinator_helpers import (
    assemble,
    needed_agents,
    record_costs,
    split_budget,
    validate,
)

logger = logging.getLogger(__name__)

if settings.google_api_key:
    os.environ.setdefault("GOOGLE_API_KEY", settings.google_api_key)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trace(trace_sink, agent: str, status: str, **extra) -> None:
    if trace_sink is not None:
        trace_sink.append({"agent": agent, "status": status, "ts": _now(), **extra})


async def _llm_decide_agents(request: TravelRequest, agent_cards: str) -> dict[str, bool]:
    """
    Google ADK LlmAgent reads the discovered AgentCards + user query and decides
    which agents to invoke. Falls back to running all agents if the ADK call fails.
    """
    _p = load_prompt("coordinator_routing")
    system_instruction = _p["system"]
    user_message = _p["user_template"].format(
        agent_cards=agent_cards,
        user_query=request.user_query or "Plan my trip",
        origin=request.origin,
        destination=request.destination,
        start_date=request.start_date,
        end_date=request.end_date,
        total_budget=f"{request.total_budget:.0f}",
        interests=", ".join(request.interests) or "general sightseeing",
    )

    try:
        routing_agent = LlmAgent(
            name="coordinator_router",
            model=settings.coordinator_model,
            instruction=system_instruction,
            generate_content_config=genai_types.GenerateContentConfig(temperature=0.0),
        )
        session_service = InMemorySessionService()
        runner = Runner(
            agent=routing_agent,
            session_service=session_service,
            app_name="coordinator",
        )
        session = await session_service.create_session(
            app_name="coordinator", user_id="system"
        )

        final_text = ""
        async for event in runner.run_async(
            user_id="system",
            session_id=session.id,
            new_message=genai_types.Content(
                role="user",
                parts=[genai_types.Part(text=user_message)],
            ),
        ):
            if event.is_final_response() and event.content:
                for part in (event.content.parts or []):
                    if hasattr(part, "text") and part.text:
                        final_text += part.text

        content = final_text.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        decision = json.loads(content.strip())

        needed = {k: bool(decision.get(k, True))
                  for k in ("flights", "attractions", "hotel", "transport")}
        logger.info("ADK routing: %s | reason: %s", needed, decision.get("reasoning", ""))
        return needed

    except Exception as exc:
        logger.warning("ADK routing failed (%s) — running all agents", exc)
        return {"flights": True, "attractions": True, "hotel": True, "transport": True}


async def _discover_agents() -> tuple[dict[str, str], str]:
    """
    A2A Phase 1 — discover all agents via AgentCard and verify health.
    Returns (healthy_urls, agent_cards_text) where agent_cards_text is
    formatted card descriptions ready to inject into the routing LLM prompt.
    """
    urls = all_agent_urls()
    healthy: dict[str, str] = {}
    card_lines: list[str] = []

    async def _check(name: str, url: str) -> None:
        client = A2AClient(url)
        try:
            card = await client.get_agent_card()
            ok   = await client.health_check()
            status = "healthy" if ok else "unhealthy"
            logger.info("A2A discovery: %s (%s) — %s", card.name, url, status)
            if ok:
                healthy[name] = url
                card_lines.append(f"- {card.name} (key: {name}): {card.description}")
        except Exception as exc:
            logger.warning("A2A discovery failed for '%s' at %s: %s", name, url, exc)

    await asyncio.gather(*[_check(n, u) for n, u in urls.items()])
    agent_cards_text = "\n".join(card_lines) if card_lines else "No agents discovered."
    return healthy, agent_cards_text


# ── Main entry point ──────────────────────────────────────────────────────────

async def run(request: TravelRequest, rag_stores: dict, trace_sink=None) -> dict:
    """
    Orchestrate the full travel planning pipeline via A2A HTTP calls.

    rag_stores is accepted for interface compatibility (agent servers load
    their own RAG stores on startup; coordinator does not need them).
    """
    session = TravelSession(total_budget=request.total_budget)

    # ── A2A Phase 1: discover agents via AgentCard + health check ─────────────
    healthy, agent_cards = await _discover_agents()
    logger.info("Healthy A2A agents: %s", list(healthy))

    # ── A2A Phase 2: LLM reads discovered AgentCards to decide routing ────────
    needed  = await _llm_decide_agents(request, agent_cards)

    # Re-validate with routing context — origin not required if Flight Agent skipped
    issues = validate(request, needed)
    if issues:
        logger.warning("Validation failed (post-routing): %s", issues)
        return {"status": "incomplete", "missing_fields": issues}
    budget_split = request.budget_split or DEFAULT_BUDGET_SPLIT
    session.per_agent_caps = split_budget(request.total_budget, needed, budget_split)
    caps    = session.per_agent_caps

    logger.info(
        "Session %s | %s→%s | budget=$%.2f | active=%s | caps=%s",
        session.session_id, request.origin, request.destination,
        request.total_budget, [k for k, v in needed.items() if v], caps,
    )

    def _client(name: str) -> A2AClient:
        url = healthy.get(name) or all_agent_urls()[name]
        return A2AClient(url)

    session_id = session.session_id

    flight_result:    dict | None = None
    hotel_result:     dict | None = None
    transport_result: dict | None = None

    # ── A2A Phase 2: parallel task submission ────────────────────────────────
    # All needed agents are fired simultaneously. Each agent is autonomous —
    # it calls peer agents directly via A2A when it needs enrichment data.
    # The coordinator passes only original request fields + session_id.

    _base = {
        "origin":      request.origin,
        "destination": request.destination,
        "start_date":  request.start_date,
        "end_date":    request.end_date,
        "interests":   request.interests,
        "session_id":  session_id,
    }

    async def _call_flights() -> dict:
        _trace(trace_sink, "flights", "running")
        result = await _client("flights").send_message(
            text=(
                f"Search for the best flight from {request.origin} to "
                f"{request.destination} between {request.start_date} and "
                f"{request.end_date} within budget ${caps.get('flights', 0):.2f}."
            ),
            data={**_base, "budget_cap": caps.get("flights", 0)},
            session_id=session_id,
        )
        _trace(trace_sink, "flights", "done",
               cost=result.get("cost", 0),
               tokens=result.pop("_token_usage", 0),
               peer_calls=result.pop("_peer_calls", []))
        logger.info("Flight A2A done — cost=$%s", result.get("cost"))
        return result

    async def _call_attractions() -> dict:
        _trace(trace_sink, "attractions", "running")
        result = await _client("attractions").send_message(
            text=(
                f"Plan a day-by-day attractions itinerary for {request.destination} "
                f"matching interests: {', '.join(request.interests) or 'general sightseeing'}."
            ),
            data={**_base, "budget_cap": caps.get("attractions", 0)},
            session_id=session_id,
        )
        _trace(trace_sink, "attractions", "done",
               cost=result.get("total_cost", 0),
               clusters=len(result.get("clusters", [])),
               tokens=result.pop("_token_usage", 0),
               peer_calls=result.pop("_peer_calls", []))
        logger.info("Attractions A2A done — clusters=%d", len(result.get("clusters", [])))
        return result

    async def _call_hotel() -> dict:
        _trace(trace_sink, "hotel", "running")
        result = await _client("hotel").send_message(
            text=(
                f"Select the best hotel in {request.destination} "
                f"near the planned attraction clusters within budget "
                f"${caps.get('hotel', 0):.2f}."
            ),
            data={**_base, "budget_cap": caps.get("hotel", 0)},
            session_id=session_id,
        )
        hotel_cost = result.get("recommended_hotel", {}).get("total_cost", 0)
        _trace(trace_sink, "hotel", "done",
               cost=hotel_cost,
               name=result.get("recommended_hotel", {}).get("name", ""),
               tokens=result.pop("_token_usage", 0),
               peer_calls=result.pop("_peer_calls", []))
        logger.info("Hotel A2A done — %s",
                    result.get("recommended_hotel", {}).get("name"))
        return result

    async def _call_transport() -> dict:
        _trace(trace_sink, "transport", "running")
        result = await _client("transport").send_message(
            text=(
                f"Arrange airport transfers and daily transit for "
                f"{request.destination} within budget ${caps.get('transport', 0):.2f}."
            ),
            data={**_base, "budget_cap": caps.get("transport", 0)},
            session_id=session_id,
        )
        _trace(trace_sink, "transport", "done",
               cost=result.get("total_cost", 0),
               tokens=result.pop("_token_usage", 0),
               peer_calls=result.pop("_peer_calls", []))
        logger.info("Transport A2A done — cost=$%s", result.get("total_cost"))
        return result

    # Build the task list — skip pre-booked agents
    agent_tasks: list = []
    agent_names: list[str] = []

    if needed["flights"]:
        agent_tasks.append(_call_flights())
        agent_names.append("flights")

    agent_tasks.append(_call_attractions())
    agent_names.append("attractions")

    if needed["hotel"]:
        agent_tasks.append(_call_hotel())
        agent_names.append("hotel")

    if needed["transport"]:
        agent_tasks.append(_call_transport())
        agent_names.append("transport")

    # Fire all in parallel — agents call each other directly via A2A as needed
    task_outputs = await asyncio.gather(*agent_tasks)
    live_results = dict(zip(agent_names, task_outputs))

    flight_result      = live_results.get("flights",     flight_result)
    attractions_result = live_results.get("attractions")
    hotel_result       = live_results.get("hotel",       hotel_result)
    transport_result   = live_results.get("transport",   transport_result)

    # ── Collect results + budget accounting ───────────────────────────────────
    results: dict = {
        "flights":     flight_result,
        "attractions": attractions_result,
        "hotel":       hotel_result,
        "transport":   transport_result,
    }

    record_costs(session, results, needed)

    # Budget retry — re-run Flight Agent with tighter cap if over budget
    retries = 0
    while (
        not session.within_budget()
        and retries < settings.agent_max_retries
        and needed["flights"]
    ):
        retries += 1
        tighter = round(caps.get("flights", 0) * RETRY_CAP_FACTOR, 2)
        logger.warning(
            "Over budget ($%.2f/$%.2f) — retrying Flight Agent (attempt %d, cap=$%.2f)",
            session.spent, session.total_budget, retries, tighter,
        )
        caps["flights"] = tighter
        results["flights"] = await _client("flights").send_message(
            text=f"Retry: find a cheaper flight to {request.destination}, cap ${tighter:.2f}.",
            data={**_base, "budget_cap": tighter},
            session_id=session_id,
        )
        record_costs(session, results, needed)

    if not session.within_budget():
        logger.warning("Could not fit trip within budget after %d retries.", retries)

    _trace(trace_sink, "coordinator", "complete")

    return assemble(session, results, request)
