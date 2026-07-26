"""Transport Agent — CrewAI + A2A server on port 8004.

Framework: CrewAI (single-agent Crew)
A2A endpoints:
  GET  /.well-known/agent.json
  GET  /health
  POST /send_message  ← coordinator sends task here

Prompt:   prompts/transport_agent.md  (system + autonomous + user_template)
Tools:    search_destination_transport (RAG), search_available_transit (MCP),
          call_peer_agent (generic A2A — LLM picks which peers to call)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from crewai import Agent as CrewAgent, Crew, LLM as CrewLLM, Task as CrewTask

from src.a2a.models import AgentCapabilities, AgentCard
from src.a2a.server import create_agent_app
from src.a2a.registry import AgentRegistry
from src.config.constants import (
    ALL_AGENT_URLS,
    TRANSPORT_URL,
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
    make_rag_tool_crew,
    make_transit_search_tool_crew,
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
    name="Transport Agent",
    description=(
        "Plans airport transfers and daily transit using a CrewAI single-agent Crew. "
        "Autonomously calls Flight, Attractions, and Hotel agents via A2A for context. "
        "Tools: search_destination_transport, search_available_transit, "
        "call_peer_agent."
    ),
    url=TRANSPORT_URL,
    capabilities=AgentCapabilities(),
)


async def _startup() -> None:
    global _rag_stores, _llm
    logger.info("Transport Agent: loading RAG stores…")
    _rag_stores = await load_or_build(get_embeddings())
    _llm = CrewLLM(
        model=f"gemini/{settings.transport_agent_model}",
        api_key=settings.google_api_key,
        temperature=0.1,
    )
    logger.info("Transport Agent: ready on :8004")


async def _handle(input_data: dict) -> dict:
    session_id  = input_data.get("session_id", "default")
    destination = input_data.get("destination", "")
    start_date  = input_data.get("start_date", "")
    end_date    = input_data.get("end_date", "")
    budget_cap  = float(input_data.get("budget_cap", 0))

    fallback: dict = {"airport_transfer": {}, "daily_transit": {}, "total_cost": 0, "tips": []}

    should_run, task = _task_manager.start(session_id)
    if not should_run:
        logger.info("Transport Agent: task already %s for session %s", task.state, session_id)
        await task.wait()
        return task.result or fallback

    try:
        _p     = load_prompt("transport_agent")
        system = (
            _p["system"]
            + "\n\n"
            + _p["autonomous_decision_making"].format(budget_cap=f"{budget_cap:.2f}")
        )

        text    = input_data.get("_text", "")
        context = {k: v for k, v in input_data.items() if k != "_text"}
        user_msg = _p["user_template"].format(
            destination=destination,
            hotel_location="[will be fetched from Hotel Agent via A2A]",
            attraction_clusters="[will be fetched from Attractions Agent via A2A]",
            confirmed_dates=f"{start_date} to {end_date}",
            budget_cap=budget_cap,
        ) if destination else f"{text}\n\nRequest context: {json.dumps(context)}"

        llm = _llm or CrewLLM(
            model=f"gemini/{settings.transport_agent_model}",
            api_key=settings.google_api_key,
            temperature=0.1,
        )

        await _registry.discover()
        peer_descriptions = _registry.agent_descriptions(exclude="transport")
        system_with_peers = (
            system
            + f"\n\nAvailable peer agents (use call_peer_agent to contact them):\n{peer_descriptions}"
        )

        peer_call_log: list[str] = []
        tools = [
            make_rag_tool_crew(
                _rag_stores, "destinations",
                "search_destination_transport",
                "Search the destinations knowledge base for transport infrastructure, "
                "transit systems, and logistics at the destination.",
            ),
            make_transit_search_tool_crew(),
            make_generic_peer_tool_crew(
                _registry, session_id,
                extra_data={"start_date": start_date, "end_date": end_date, "destination": destination},
                peer_call_log=peer_call_log,
            ),
        ]

        crew_agent = CrewAgent(
            role="Transport Specialist",
            goal="Plan all ground transport — airport transfers and daily in-city transit — within budget.",
            backstory=system_with_peers,
            llm=llm,
            tools=tools,
            verbose=False,
            allow_delegation=False,
        )

        crew_task = CrewTask(
            description=user_msg,
            expected_output=(
                "Valid JSON with keys: airport_transfer (object), daily_transit (object), "
                "key_routes (list), total_cost (number), tips (list of strings). "
                "No markdown, no prose outside the JSON block."
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
                    logger.warning("Transport Agent rate limited — retrying in %ds (attempt %d/%d)", _wait, _attempt + 1, RATE_LIMIT_MAX_RETRIES)
                    await asyncio.sleep(_wait)
                else:
                    raise
        final_text = str(crew_result)

        result = parse_agent_json(final_text, fallback)
        result["_peer_calls"] = peer_call_log

        logger.info(
            "Transport Agent done: total_cost=$%s peer_calls=%s (session=%s)",
            result.get("total_cost"), peer_call_log, session_id,
        )
        task.complete(result)
        return result
    except Exception:
        task.fail()
        raise


app = create_agent_app(CARD, _handle, startup=_startup)
