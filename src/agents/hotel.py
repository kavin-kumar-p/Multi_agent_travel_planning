"""Hotel Agent — CrewAI + A2A server on port 8003.

Framework: CrewAI (single-agent Crew)
A2A endpoints:
  GET  /.well-known/agent.json
  GET  /health
  POST /send_message  ← coordinator OR peer agents send requests here

Prompt:   prompts/hotel_agent.md   (system + autonomous + user_template)
Tools:    search_hotel_knowledge (RAG), search_available_hotels (MCP),
          call_peer_agent (generic A2A — LLM picks which peer to call)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from crewai import Agent as CrewAgent, Crew, LLM as CrewLLM, Task as CrewTask

from a2a.types import AgentCard, AgentCapabilities, AgentInterface
from src.a2a.server import create_agent_app
from src.a2a.registry import AgentRegistry
from src.config.constants import (
    ALL_AGENT_URLS,
    BUDGET_HOTEL_BUFFER,
    HOTEL_URL,
    RATE_LIMIT_BACKOFF_BASE,
    RATE_LIMIT_BASE_WAIT,
    RATE_LIMIT_MAX_ATTEMPTS,
    RATE_LIMIT_MAX_RETRIES,
)
from src.config.settings import settings
from src.llm.factory import get_embeddings
from src.prompts import load_prompt
from src.rag.bootstrap import load_or_build
from src.a2a.task_manager import TaskManager
from src.tools.agent_tools import (
    make_generic_peer_tool_crew,
    make_hotel_search_tool_crew,
    make_rag_tool_crew,
)
from src.utils import parse_agent_json

os.environ.setdefault("GOOGLE_API_KEY", settings.google_api_key or "")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "1")

logger = logging.getLogger(__name__)

_rag_stores: dict = {}
_llm: CrewLLM | None = None
_registry = AgentRegistry(ALL_AGENT_URLS)
_task_manager = TaskManager()

CARD = AgentCard(
    name="Hotel Agent",
    description=(
        "Selects the optimal hotel using a CrewAI single-agent Crew. "
        "Runs a full hotel search for every request — coordinator task or peer query. "
        "Calls peer agents via A2A when it needs context (e.g. attraction clusters from Attractions Agent). "
        "Tools: search_hotel_knowledge, search_available_hotels, call_peer_agent."
    ),
    supported_interfaces=[AgentInterface(url=HOTEL_URL)],
    version="1.0.0",
    capabilities=AgentCapabilities(),
    default_input_modes=["text", "data"],
    default_output_modes=["data"],
)


async def _startup() -> None:
    global _rag_stores, _llm
    logger.info("Hotel Agent: loading RAG stores…")
    _rag_stores = await load_or_build(get_embeddings())
    _llm = CrewLLM(
        model=f"gemini/{settings.hotel_agent_model}",
        api_key=settings.google_api_key,
        temperature=0.1,
    )
    logger.info("Hotel Agent: ready on :8003")


async def _handle(input_data: dict) -> dict:
    session_id      = input_data.get("session_id", "default")
    destination     = input_data.get("destination", "")
    start_date      = input_data.get("start_date", "")
    end_date        = input_data.get("end_date", "")
    budget_cap      = float(input_data.get("budget_cap", 0))
    skipped_agents  = input_data.get("skipped_agents", [])
    requested_hotel = input_data.get("requested_hotel", "")

    fallback: dict = {"recommended_hotel": {}, "over_budget": False, "policy_notes": ""}

    should_run, task = _task_manager.start(session_id)
    if not should_run:
        logger.info("Hotel Agent: task already %s for session %s", task.state, session_id)
        await task.wait()
        return task.result or fallback

    try:
        _p     = load_prompt("hotel_agent")
        system = _p["system"] + "\n\n" + _p["autonomous_decision_making"]

        if requested_hotel:
            system += (
                f"\n\nThe user specifically requested: '{requested_hotel}'. "
                f"You MUST search for this exact hotel first in {destination}. "
                f"If it exists in {destination} and is within budget, select it. "
                f"If the hotel name suggests a different city or country (e.g. 'Paris Marriott' when "
                f"destination is {destination}), set policy_notes to warn the user of the mismatch "
                "and fall back to the best available hotel in the actual destination."
            )

        if skipped_agents:
            system += (
                f"\n\nSkipped agents (DO NOT call these via call_peer_agent — "
                f"they were not invoked this session): {', '.join(skipped_agents)}. "
                "If attractions is skipped, select the best hotel by price, rating, "
                "and central location without proximity-to-cluster ranking."
            )

        text    = input_data.get("_text", "")
        context = {k: v for k, v in input_data.items() if k != "_text"}
        clusters_hint = (
            "[attractions agent skipped — select by price, rating, and central location]"
            if "attractions" in skipped_agents
            else "[will be fetched from Attractions Agent via A2A]"
        )
        hotel_hint = f"Specifically requested: {requested_hotel}" if requested_hotel else clusters_hint
        user_msg = _p["user_template"].format(
            destination=destination,
            start_date=start_date,
            end_date=end_date,
            attraction_clusters=hotel_hint,
            budget_cap=budget_cap,
        ) if destination else f"{text}\n\nRequest context: {json.dumps(context)}"

        llm = _llm or CrewLLM(
            model=f"gemini/{settings.hotel_agent_model}",
            api_key=settings.google_api_key,
            temperature=0.1,
        )

        await _registry.discover()
        peer_descriptions = _registry.agent_descriptions(exclude="hotel")
        system_with_peers = (
            system
            + f"\n\nAvailable peer agents (use call_peer_agent to contact them):\n{peer_descriptions}"
        )

        peer_call_log: list[str] = []
        tools = [
            make_rag_tool_crew(
                _rag_stores, "hotel_details",
                "search_hotel_knowledge",
                "Search the hotel knowledge base for hotel details, star ratings, "
                "amenities, and location information.",
            ),
            make_hotel_search_tool_crew(destination, BUDGET_HOTEL_BUFFER),
            make_generic_peer_tool_crew(
                _registry, session_id,
                extra_data={"destination": destination},
                peer_call_log=peer_call_log,
                caller=CARD.name,
            ),
        ]

        crew_agent = CrewAgent(
            role="Hotel Specialist",
            goal="Select the single best hotel for the trip within budget and near attraction clusters.",
            backstory=system_with_peers,
            llm=llm,
            tools=tools,
            verbose=False,
            allow_delegation=False,
        )

        crew_task = CrewTask(
            description=user_msg,
            expected_output=(
                "Valid JSON with keys: recommended_hotel (object), over_budget (bool), "
                "policy_notes (string). No markdown, no prose outside the JSON block."
            ),
            agent=crew_agent,
        )

        crew = Crew(agents=[crew_agent], tasks=[crew_task], verbose=False)
        import re as _re
        for _attempt in range(RATE_LIMIT_MAX_ATTEMPTS):
            try:
                crew_result = await asyncio.to_thread(crew.kickoff)
                break
            except Exception as _exc:
                _msg = str(_exc)
                if "429" in _msg and _attempt < RATE_LIMIT_MAX_RETRIES:
                    _m = _re.search(r"retry[^\d]*(\d+)", _msg, _re.IGNORECASE)
                    _wait = int(_m.group(1)) if _m else RATE_LIMIT_BASE_WAIT * (RATE_LIMIT_BACKOFF_BASE ** _attempt)
                    logger.warning("Hotel Agent rate limited — retrying in %ds (attempt %d/%d)", _wait, _attempt + 1, RATE_LIMIT_MAX_RETRIES)
                    await asyncio.sleep(_wait)
                else:
                    raise
        final_text = str(crew_result)

        result = parse_agent_json(final_text, fallback)
        result["_peer_calls"] = peer_call_log

        logger.info(
            "Hotel Agent done: %s peer_calls=%s (session=%s)",
            result.get("recommended_hotel", {}).get("name"), peer_call_log, session_id,
        )
        task.complete(result)
        return result
    except Exception:
        task.fail()
        raise


app = create_agent_app(CARD, _handle, startup=_startup)
