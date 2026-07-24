"""Hotel Agent — CrewAI + A2A server on port 8003.

A2A endpoints:
  GET  /.well-known/agent.json
  GET  /health
  POST /send_message  ← coordinator OR peer agents send requests here

A2A flow:
  1. Asks Attractions Agent directly for cluster_areas via POST /send_message
     (query_type="cluster_areas")
  2. Runs CrewAI pipeline with those clusters

Phase 1: parallel RAG (hotel_details) + MCP (search_hotels)
Phase 2: CrewAI Hotel Selection Specialist reasons over combined data

Output DataPart keys:
  recommended_hotel (name, location, price_per_night, total_cost, amenities, review_score),
  over_budget, policy_notes

Peer query (A2A from Transport):
  query_type == "hotel_location" → returns {"hotel_location": "...", "hotel_name": "..."}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime

from crewai import Agent, Crew, LLM, Process, Task

from src import tools
from src.a2a.client import A2AClient
from src.a2a.models import AgentCapabilities, AgentCard
from src.a2a.server import create_agent_app
from src.config.constants import ATTRACTIONS_URL, BUDGET_HOTEL_BUFFER, HOTEL_URL, NIGHT_FALLBACK
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
    name="Hotel Agent",
    description=(
        "Selects the optimal hotel using a CrewAI Crew. "
        "Calls Attractions Agent directly via A2A to get cluster areas. "
        "Phase 1: parallel RAG (hotel_details) + MCP (search_hotels). "
        "Phase 2: CrewAI Hotel Selection Specialist. "
        "Handles peer queries: query_type='hotel_location' → hotel location + name. "
        "Framework: CrewAI."
    ),
    url=HOTEL_URL,
    capabilities=AgentCapabilities(),
)


def _nights(start_date: str, end_date: str) -> int:
    try:
        return max(1, (
            datetime.strptime(end_date, "%Y-%m-%d") -
            datetime.strptime(start_date, "%Y-%m-%d")
        ).days)
    except ValueError:
        return NIGHT_FALLBACK


# ── A2A server lifecycle ──────────────────────────────────────────────────────

async def _startup() -> None:
    global _rag_stores
    logger.info("Hotel Agent: loading RAG stores…")
    _rag_stores = await load_or_build(get_embeddings())
    logger.info("Hotel Agent: ready on :8003")


async def _handle(input_data: dict) -> dict:
    session_id = input_data.get("session_id", "default")

    # Peer live query from Transport
    if input_data.get("query_type") == "hotel_location":
        result = _session_results.get(session_id)
        if result:
            h = result.get("recommended_hotel", {})
            return {
                "hotel_location": (
                    h.get("location") or h.get("name") or input_data.get("destination", "")
                ),
                "hotel_name": h.get("name", ""),
            }
        return {
            "hotel_location": input_data.get("destination", ""),
            "hotel_name": "",
        }

    # Step 1: Ask Attractions Agent directly for cluster areas (live A2A call)
    try:
        attractions_response = await A2AClient(ATTRACTIONS_URL).send_message(
            text="Which geographic areas are you clustering attractions in for this trip?",
            data={
                "query_type":  "cluster_areas",
                "session_id":  session_id,
                "destination": input_data["destination"],
            },
        )
        attraction_clusters = attractions_response.get("clusters", [])
        logger.info("Hotel: received cluster_areas from Attractions Agent via A2A")
    except Exception as exc:
        logger.warning("Hotel: Attractions Agent unavailable (%s) — no clusters", exc)
        attraction_clusters = []

    destination      = input_data["destination"]
    start_date       = input_data["start_date"]
    end_date         = input_data["end_date"]
    budget_cap       = input_data["budget_cap"]
    num_nights       = _nights(start_date, end_date)
    budget_per_night = budget_cap / num_nights

    # Phase 1: parallel RAG + MCP
    hotel_chunks, hotels = await asyncio.gather(
        asyncio.to_thread(
            retrieve, _rag_stores, "hotel_details",
            f"hotel {destination} 3 star 4 star",
        ),
        asyncio.to_thread(
            tools.search_hotels, destination, budget_per_night * BUDGET_HOTEL_BUFFER,
        ),
    )

    prompt        = load_prompt("hotel_agent")
    clusters_json = json.dumps(attraction_clusters, ensure_ascii=False)
    task_description = (
        f"{prompt['system']}\n\n"
        f"## Hotel Knowledge Base\n{chr(10).join(hotel_chunks)}\n\n"
        f"## Available Hotels\n{json.dumps(hotels, indent=2, ensure_ascii=False)}\n\n"
        f"## Attraction Clusters\n{clusters_json}\n\n## Task\n"
        + prompt["user_template"].format(
            destination=destination,
            start_date=start_date,
            end_date=end_date,
            attraction_clusters=clusters_json,
            budget_cap=budget_cap,
        )
    )

    # Phase 2: CrewAI crew
    crewai_llm = LLM(
        model=f"gemini/{settings.hotel_agent_model}",
        api_key=settings.google_api_key,
    )
    researcher = Agent(
        role="Hotel Selection Specialist",
        goal=(
            f"Select the best hotel in {destination} near attraction clusters "
            f"within ${budget_cap:.2f} for {num_nights} nights."
        ),
        backstory=(
            "Expert travel consultant specialising in hotel selection. "
            "Weighs location proximity to attractions, amenities, reviews, and value."
        ),
        llm=crewai_llm, verbose=False, allow_delegation=False,
    )
    selection_task = Task(
        description=task_description,
        expected_output=(
            "JSON object with keys: recommended_hotel (name, location, price_per_night, "
            "total_cost, amenities, review_score), over_budget (bool), policy_notes (string)."
        ),
        agent=researcher,
    )
    crew       = Crew(agents=[researcher], tasks=[selection_task],
                      process=Process.sequential, verbose=False)
    crew_output = await asyncio.to_thread(crew.kickoff)
    raw_text    = crew_output.raw if hasattr(crew_output, "raw") else str(crew_output)

    fallback: dict = {"recommended_hotel": {}, "over_budget": False, "policy_notes": ""}
    if hotels:
        h = hotels[0]
        fallback["recommended_hotel"] = {
            **h,
            "total_cost": h.get("price_per_night", 0) * num_nights,
        }

    result = parse_agent_json(raw_text, fallback)
    _session_results[session_id] = result
    logger.info(
        "Hotel Agent done: %s (session=%s)",
        result.get("recommended_hotel", {}).get("name"), session_id,
    )
    return result


app = create_agent_app(CARD, _handle, startup=_startup)
