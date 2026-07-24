"""Flight Agent — LangGraph StateGraph + A2A server on port 8001.

A2A endpoints:
  GET  /.well-known/agent.json
  GET  /health
  POST /send_message  ← coordinator OR peer agents send requests here

Graph topology:
  START → gather_data → select_flight → END

  gather_data   — parallel RAG (travel_policies) + MCP (search_flights)
  select_flight — LLM picks best compliant option within budget cap

Output DataPart keys:
  recommended_flights (list), cost, confirmed_dates, over_budget, policy_notes

Peer query (A2A from Attractions or Transport):
  query_type == "date_confirmation" → returns {"confirmed_dates": "..."}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph

from src import tools
from src.a2a.models import AgentCapabilities, AgentCard
from src.a2a.server import create_agent_app
from src.config.constants import FLIGHT_URL
from src.config.settings import settings
from src.llm.factory import get_embeddings, get_llm
from src.prompts import load_prompt
from src.rag.bootstrap import load_or_build
from src.rag.retriever import retrieve
from src.utils import parse_agent_json

os.environ.setdefault("GOOGLE_API_KEY", settings.google_api_key or "")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "1")

logger = logging.getLogger(__name__)

_rag_stores: dict = {}

# Session store — keyed by session_id, holds completed results
_session_results: dict[str, dict] = {}

CARD = AgentCard(
    name="Flight Agent",
    description=(
        "Searches and selects optimal flights using a LangGraph StateGraph. "
        "Phase 1: parallel RAG (travel_policies) + MCP (search_flights). "
        "Phase 2: LLM selects the best compliant flight within the budget cap. "
        "Handles peer queries: query_type='date_confirmation' → confirmed_dates. "
        "Framework: LangGraph."
    ),
    url=FLIGHT_URL,
    capabilities=AgentCapabilities(),
)


# ── LangGraph graph ───────────────────────────────────────────────────────────

class _FlightState(TypedDict):
    rag_stores: dict
    origin: str
    destination: str
    start_date: str
    end_date: str
    budget_cap: float
    policy_chunks: list[str]
    flight_options: list[dict]
    result: dict


async def _gather_data(state: _FlightState) -> dict:
    policy_chunks, flights = await asyncio.gather(
        asyncio.to_thread(
            retrieve, state["rag_stores"], "travel_policies",
            f"flight booking policy {state['destination']}",
        ),
        asyncio.to_thread(
            tools.search_flights,
            state["origin"], state["destination"],
            state["start_date"], state["end_date"],
            state["budget_cap"],
        ),
    )
    return {"policy_chunks": policy_chunks, "flight_options": flights}


async def _select_flight(state: _FlightState) -> dict:
    prompt = load_prompt("flight_agent")
    llm    = get_llm(settings.flight_agent_model)
    full_prompt = (
        f"{prompt['system']}\n\n"
        f"## Travel Policies\n{chr(10).join(state['policy_chunks'])}\n\n"
        f"## Available Flights\n{json.dumps(state['flight_options'], indent=2)}\n\n"
        f"## Task\n"
        + prompt["user_template"].format(
            origin=state["origin"], destination=state["destination"],
            start_date=state["start_date"], end_date=state["end_date"],
            budget_cap=state["budget_cap"],
        )
    )
    response = await llm.ainvoke([HumanMessage(content=full_prompt)])
    flights  = state["flight_options"]
    fallback: dict = {
        "recommended_flights": flights[:1] if flights else [],
        "cost":            flights[0].get("price", 0) if flights else 0,
        "confirmed_dates": f"{state['start_date']} to {state['end_date']}",
        "over_budget": False,
        "policy_notes": "",
    }
    result = parse_agent_json(response.content, fallback)
    if not result.get("confirmed_dates"):
        result["confirmed_dates"] = f"{state['start_date']} to {state['end_date']}"
    # Patch: LLM sometimes returns empty list or cost=0 — fill from raw search results
    if not result.get("recommended_flights") and flights:
        result["recommended_flights"] = flights[:1]
    if not result.get("cost") and result.get("recommended_flights"):
        result["cost"] = result["recommended_flights"][0].get("price", 0)
    logger.info(
        "Flight select: cost=$%s over_budget=%s",
        result.get("cost"), result.get("over_budget"),
    )
    return {"result": result}


def _build_graph():
    wf = StateGraph(_FlightState)
    wf.add_node("gather_data",   _gather_data)
    wf.add_node("select_flight", _select_flight)
    wf.set_entry_point("gather_data")
    wf.add_edge("gather_data",   "select_flight")
    wf.add_edge("select_flight", END)
    return wf.compile()


_graph = _build_graph()


# ── A2A server lifecycle ──────────────────────────────────────────────────────

async def _startup() -> None:
    global _rag_stores
    logger.info("Flight Agent: loading RAG stores…")
    _rag_stores = await load_or_build(get_embeddings())
    logger.info("Flight Agent: ready on :8001")


async def _handle(input_data: dict) -> dict:
    session_id = input_data.get("session_id", "default")

    # Peer live query from Attractions or Transport
    if input_data.get("query_type") == "date_confirmation":
        result = _session_results.get(session_id)
        if result:
            logger.info(
                "Flight Agent: answering date_confirmation query (session=%s)", session_id
            )
            return {"confirmed_dates": result.get("confirmed_dates")}
        # Flight was pre-booked (skipped) — return original request dates as fallback
        return {
            "confirmed_dates": (
                f"{input_data.get('start_date', '')} to {input_data.get('end_date', '')}"
            )
        }

    # Full task from coordinator — run LangGraph pipeline
    initial: _FlightState = {
        "rag_stores":     _rag_stores,
        "origin":         input_data["origin"],
        "destination":    input_data["destination"],
        "start_date":     input_data["start_date"],
        "end_date":       input_data["end_date"],
        "budget_cap":     input_data["budget_cap"],
        "policy_chunks":  [],
        "flight_options": [],
        "result":         {},
    }
    final  = await _graph.ainvoke(initial)
    result = final["result"]
    _session_results[session_id] = result
    logger.info(
        "Flight Agent done: cost=$%s (session=%s)", result.get("cost"), session_id
    )
    return result


app = create_agent_app(CARD, _handle, startup=_startup)
