"""Transport Agent — CrewAI + A2A server on port 8004.

A2A endpoints:
  GET  /.well-known/agent.json
  GET  /health
  POST /send_message  ← coordinator sends task here

A2A flow:
  Asks Flight, Attractions, and Hotel agents directly in parallel via
  POST /send_message before running its own CrewAI pipeline:
    - Flight (query_type="date_confirmation") → confirmed_dates
    - Attractions (query_type="cluster_areas") → attraction_clusters
    - Hotel (query_type="hotel_location") → hotel_location

Phase 1: parallel RAG (destinations) + MCP (search_transit)
Phase 2: CrewAI Transport Planning Specialist reasons over combined data

Output DataPart keys:
  airport_transfer (mode, cost, duration), daily_transit (mode, daily_pass_cost),
  total_cost, tips (list)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from crewai import Agent, Crew, LLM, Process, Task

from src import tools
from src.a2a.client import A2AClient
from src.a2a.models import AgentCapabilities, AgentCard
from src.a2a.server import create_agent_app
from src.config.constants import ATTRACTIONS_URL, FLIGHT_URL, HOTEL_URL, TRANSPORT_URL
from src.config.settings import settings
from src.llm.factory import get_embeddings
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
    name="Transport Agent",
    description=(
        "Plans airport transfers and daily transit using a CrewAI Crew. "
        "Calls Flight, Attractions, and Hotel agents directly via A2A in parallel "
        "to gather confirmed dates, attraction clusters, and hotel location. "
        "Phase 1: parallel RAG (destinations) + MCP (search_transit). "
        "Phase 2: CrewAI Transport Planning Specialist. Framework: CrewAI."
    ),
    url=TRANSPORT_URL,
    capabilities=AgentCapabilities(),
)


# ── A2A server lifecycle ──────────────────────────────────────────────────────

async def _startup() -> None:
    global _rag_stores
    logger.info("Transport Agent: loading RAG stores…")
    _rag_stores = await load_or_build(get_embeddings())
    logger.info("Transport Agent: ready on :8004")


async def _handle(input_data: dict) -> dict:
    session_id  = input_data.get("session_id", "default")
    destination = input_data["destination"]
    budget_cap  = input_data["budget_cap"]

    # Transport is last — no peer queries to handle.
    # Ask all three upstream agents directly in parallel (all should be done by now).

    async def _ask_flight() -> str | None:
        try:
            r = await A2AClient(FLIGHT_URL).send_message(
                text="What are the confirmed travel dates?",
                data={
                    "query_type": "date_confirmation",
                    "session_id": session_id,
                    "start_date": input_data["start_date"],
                    "end_date":   input_data["end_date"],
                },
            )
            return r.get("confirmed_dates")
        except Exception as exc:
            logger.warning("Transport: Flight Agent unavailable (%s)", exc)
            return None

    async def _ask_attractions() -> list:
        try:
            r = await A2AClient(ATTRACTIONS_URL).send_message(
                text="Which geographic areas are you clustering attractions in?",
                data={
                    "query_type":  "cluster_areas",
                    "session_id":  session_id,
                    "destination": destination,
                },
            )
            return r.get("clusters", [])
        except Exception as exc:
            logger.warning("Transport: Attractions Agent unavailable (%s)", exc)
            return []

    async def _ask_hotel() -> str:
        try:
            r = await A2AClient(HOTEL_URL).send_message(
                text="Where is the selected hotel located?",
                data={
                    "query_type":  "hotel_location",
                    "session_id":  session_id,
                    "destination": destination,
                },
            )
            return r.get("hotel_location") or destination
        except Exception as exc:
            logger.warning("Transport: Hotel Agent unavailable (%s)", exc)
            return destination

    confirmed_dates, attraction_clusters, hotel_location = await asyncio.gather(
        _ask_flight(), _ask_attractions(), _ask_hotel()
    )
    confirmed_dates = (
        confirmed_dates
        or f"{input_data['start_date']} to {input_data['end_date']}"
    )

    # Phase 1: parallel RAG + MCP
    dest_chunks, transit_options = await asyncio.gather(
        asyncio.to_thread(
            retrieve, _rag_stores, "destinations",
            f"transport transit {destination}",
        ),
        asyncio.to_thread(tools.search_transit, destination),
    )

    prompt        = load_prompt("transport_agent")
    clusters_json = json.dumps(attraction_clusters, ensure_ascii=False)
    task_description = (
        f"{prompt['system']}\n\n"
        f"## Destination Transport Info\n{chr(10).join(dest_chunks)}\n\n"
        f"## Live Transit Options\n{json.dumps(transit_options, indent=2, ensure_ascii=False)}\n\n"
        f"## Task\n"
        + prompt["user_template"].format(
            destination=destination,
            hotel_location=hotel_location,
            attraction_clusters=clusters_json,
            confirmed_dates=confirmed_dates,
            budget_cap=budget_cap,
        )
    )

    # Phase 2: CrewAI crew
    crewai_llm = LLM(
        model=f"gemini/{settings.transport_agent_model}",
        api_key=settings.google_api_key,
    )
    planner = Agent(
        role="Transport Planning Specialist",
        goal=(
            f"Arrange optimal airport transfers and daily transit for {destination}, "
            f"staying at {hotel_location}, within ${budget_cap:.2f}."
        ),
        backstory=(
            "Expert in local transit systems and airport logistics. "
            "Balances convenience, cost, and coverage for every leg of the trip."
        ),
        llm=crewai_llm, verbose=False, allow_delegation=False,
    )
    planning_task = Task(
        description=task_description,
        expected_output=(
            "JSON object with keys: airport_transfer (mode, cost, duration), "
            "daily_transit (mode, daily_pass_cost), total_cost (number), tips (list)."
        ),
        agent=planner,
    )
    crew       = Crew(agents=[planner], tasks=[planning_task],
                      process=Process.sequential, verbose=False)
    crew_output = await asyncio.to_thread(crew.kickoff)
    raw_text    = crew_output.raw if hasattr(crew_output, "raw") else str(crew_output)

    fallback: dict = {
        "airport_transfer": {}, "daily_transit": {}, "total_cost": 0, "tips": [],
    }
    if transit_options:
        opt = transit_options[0]
        fallback = {
            "airport_transfer": opt.get("airport_transfer", {}),
            "daily_transit":    opt.get("daily_pass", {}),
            "total_cost": 50,
            "tips": opt.get("tips", []),
        }

    result = parse_agent_json(raw_text, fallback)
    _session_results[session_id] = result
    logger.info(
        "Transport Agent done: total_cost=$%s (session=%s)",
        result.get("total_cost"), session_id,
    )
    return result


app = create_agent_app(CARD, _handle, startup=_startup)
