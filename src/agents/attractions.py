"""Attractions Agent — LangGraph StateGraph + A2A server on port 8002.

A2A endpoints:
  GET  /.well-known/agent.json
  GET  /health
  POST /send_message  ← coordinator OR peer agents send requests here

Graph topology:
  START → gather_rag → plan_attractions → END

  gather_rag       — parallel RAG: destinations, preferences, previous_itineraries
  plan_attractions — LLM clusters attractions into a day-by-day itinerary

Output DataPart keys:
  clusters (list), total_cost, notes

A2A flow:
  1. Asks Flight Agent directly for confirmed_dates via POST /send_message
     (query_type="date_confirmation")
  2. Runs LangGraph pipeline with those dates

Peer query (A2A from Hotel):
  query_type == "cluster_areas" → returns {"clusters": [...]}
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph

from src.a2a.client import A2AClient
from src.a2a.models import AgentCapabilities, AgentCard
from src.a2a.server import create_agent_app
from src.config.constants import ATTRACTIONS_URL, FLIGHT_URL
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
    name="Attractions Agent",
    description=(
        "Plans day-by-day attractions using a LangGraph StateGraph. "
        "Calls Flight Agent directly via A2A to get confirmed travel dates. "
        "Phase 1: parallel RAG across destinations, preferences, previous_itineraries. "
        "Phase 2: LLM clusters attractions in plan_attractions node. "
        "Handles peer queries: query_type='cluster_areas' → clusters list. "
        "Framework: LangGraph."
    ),
    url=ATTRACTIONS_URL,
    capabilities=AgentCapabilities(),
)


# ── LangGraph graph ───────────────────────────────────────────────────────────

class _AttractionsState(TypedDict):
    rag_stores: dict
    destination: str
    confirmed_dates: str
    interests: str
    budget_cap: float
    dest_chunks: list[str]
    pref_chunks: list[str]
    prev_chunks: list[str]
    result: dict


async def _gather_rag(state: _AttractionsState) -> dict:
    dest_chunks, pref_chunks, prev_chunks = await asyncio.gather(
        asyncio.to_thread(
            retrieve, state["rag_stores"], "destinations",
            f"{state['destination']} attractions {state['interests']}",
        ),
        asyncio.to_thread(retrieve, state["rag_stores"], "preferences", state["interests"]),
        asyncio.to_thread(
            retrieve, state["rag_stores"], "previous_itineraries",
            f"{state['destination']} itinerary highlights",
        ),
    )
    return {"dest_chunks": dest_chunks, "pref_chunks": pref_chunks, "prev_chunks": prev_chunks}


async def _plan_attractions(state: _AttractionsState) -> dict:
    prompt = load_prompt("attractions_agent")
    llm    = get_llm(settings.attractions_agent_model)
    context = (
        f"## Destination Info\n{chr(10).join(state['dest_chunks'])}\n\n"
        f"## Traveler Preferences\n{chr(10).join(state['pref_chunks'])}\n\n"
        f"## Previous Itineraries\n{chr(10).join(state['prev_chunks'])}"
    )
    full_prompt = (
        f"{prompt['system']}\n\n{context}\n\n## Task\n"
        + prompt["user_template"].format(
            destination=state["destination"],
            confirmed_dates=state["confirmed_dates"],
            interests=state["interests"],
            budget_cap=state["budget_cap"],
        )
    )
    response = await llm.ainvoke([HumanMessage(content=full_prompt)])
    fallback: dict = {
        "clusters": [],
        "total_cost": 0,
        "notes": "Fallback — could not parse agent output",
    }
    result = parse_agent_json(response.content, fallback)
    logger.info(
        "Attractions plan: total_cost=$%s clusters=%d",
        result.get("total_cost"), len(result.get("clusters", [])),
    )
    return {"result": result}


def _build_graph():
    wf = StateGraph(_AttractionsState)
    wf.add_node("gather_rag",       _gather_rag)
    wf.add_node("plan_attractions", _plan_attractions)
    wf.set_entry_point("gather_rag")
    wf.add_edge("gather_rag",       "plan_attractions")
    wf.add_edge("plan_attractions", END)
    return wf.compile()


_graph = _build_graph()


# ── A2A server lifecycle ──────────────────────────────────────────────────────

async def _startup() -> None:
    global _rag_stores
    logger.info("Attractions Agent: loading RAG stores…")
    _rag_stores = await load_or_build(get_embeddings())
    logger.info("Attractions Agent: ready on :8002")


async def _handle(input_data: dict) -> dict:
    session_id = input_data.get("session_id", "default")

    # Peer live query from Hotel
    if input_data.get("query_type") == "cluster_areas":
        result = _session_results.get(session_id)
        if result:
            logger.info(
                "Attractions Agent: answering cluster_areas query (session=%s)", session_id
            )
            return {"clusters": result.get("clusters", [])}
        return {"clusters": []}

    # Step 1: Ask Flight Agent directly for confirmed_dates (live A2A call)
    try:
        flight_response = await A2AClient(FLIGHT_URL).send_message(
            text="What are the confirmed travel dates for this trip?",
            data={
                "query_type": "date_confirmation",
                "session_id": session_id,
                "start_date": input_data["start_date"],
                "end_date":   input_data["end_date"],
            },
        )
        confirmed_dates = flight_response.get("confirmed_dates")
        logger.info("Attractions: received confirmed_dates from Flight Agent via A2A")
    except Exception as exc:
        logger.warning(
            "Attractions: Flight Agent unavailable (%s) — using request dates", exc
        )
        confirmed_dates = None

    confirmed_dates = (
        confirmed_dates
        or f"{input_data['start_date']} to {input_data['end_date']}"
    )

    # Step 2: Run LangGraph pipeline with confirmed_dates
    initial: _AttractionsState = {
        "rag_stores":      _rag_stores,
        "destination":     input_data["destination"],
        "confirmed_dates": confirmed_dates,
        "interests":       ", ".join(input_data.get("interests", [])),
        "budget_cap":      input_data["budget_cap"],
        "dest_chunks": [], "pref_chunks": [], "prev_chunks": [], "result": {},
    }
    final  = await _graph.ainvoke(initial)
    result = final["result"]
    _session_results[session_id] = result
    logger.info(
        "Attractions Agent done: clusters=%d (session=%s)",
        len(result.get("clusters", [])), session_id,
    )
    return result


app = create_agent_app(CARD, _handle, startup=_startup)
