"""Attractions Agent — LangGraph ReAct agent + A2A server on port 8002.

Framework: LangGraph (create_react_agent)
A2A endpoints:
  GET  /.well-known/agent.json
  GET  /health
  POST /send_message  ← coordinator OR peer agents send requests here

Prompt:   prompts/attractions_agent.md  (system + autonomous + user_template)
Tools:    search_destinations/preferences/itineraries (RAG),
          call_peer_agent (generic A2A — LLM picks which peer to call)
"""
from __future__ import annotations

import json
import logging
import os

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from a2a.types import AgentCard, AgentCapabilities, AgentInterface
from src.a2a.server import create_agent_app
from src.config.constants import ALL_AGENT_URLS, ATTRACTIONS_URL
from src.a2a.registry import AgentRegistry
from src.config.settings import settings
from src.llm.factory import get_embeddings
from src.llm.google_genai_model import GoogleGenAIChatModel
from src.prompts import load_prompt
from src.rag.bootstrap import load_or_build
from src.a2a.task_manager import TaskManager
from src.tools.agent_tools import make_generic_peer_tool, make_rag_tool
from src.utils import parse_agent_json

os.environ.setdefault("GOOGLE_API_KEY", settings.google_api_key or "")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "1")

logger = logging.getLogger(__name__)

_rag_stores: dict = {}
_model: GoogleGenAIChatModel | None = None
_registry = AgentRegistry(ALL_AGENT_URLS)
_task_manager = TaskManager()

CARD = AgentCard(
    name="Attractions Agent",
    description=(
        "Plans day-by-day attractions using a LangGraph ReAct agent. "
        "Runs a full attractions plan for every request — coordinator task or peer query. "
        "Calls peer agents via A2A when it needs context (e.g. confirmed dates from Flight Agent). "
        "Tools: search_destinations, search_preferences, search_previous_itineraries, call_peer_agent."
    ),
    supported_interfaces=[AgentInterface(url=ATTRACTIONS_URL)],
    version="1.0.0",
    capabilities=AgentCapabilities(),
    default_input_modes=["text", "data"],
    default_output_modes=["data"],
)


async def _startup() -> None:
    global _rag_stores, _model
    logger.info("Attractions Agent: loading RAG stores…")
    _rag_stores = await load_or_build(get_embeddings())
    _model = GoogleGenAIChatModel(
        model=settings.attractions_agent_model,
        google_api_key=settings.google_api_key,
        temperature=0.1,
    )
    logger.info("Attractions Agent: ready on :8002")


async def _handle(input_data: dict) -> dict:
    session_id  = input_data.get("session_id", "default")
    destination = input_data.get("destination", "")
    start_date  = input_data.get("start_date", "")
    end_date    = input_data.get("end_date", "")
    interests   = ", ".join(input_data.get("interests", []))

    fallback: dict = {"clusters": [], "total_cost": 0, "notes": "Fallback"}

    should_run, task = _task_manager.start(session_id)
    if not should_run:
        logger.info("Attractions Agent: task already %s for session %s", task.state, session_id)
        await task.wait()
        return task.result or fallback

    try:
        _p     = load_prompt("attractions_agent")
        system = _p["system"] + "\n\n" + _p["autonomous_decision_making"]

        text    = input_data.get("_text", "")
        context = {k: v for k, v in input_data.items() if k != "_text"}
        user_msg = _p["user_template"].format(
            destination=destination,
            confirmed_dates=f"{start_date} to {end_date}",
            num_days=input_data.get("num_days", ""),
            interests=interests or "general sightseeing",
            budget_cap=context.get("budget_cap", 0),
        ) if destination else f"{text}\n\nRequest context: {json.dumps(context)}"

        model = _model or GoogleGenAIChatModel(
            model=settings.attractions_agent_model,
            google_api_key=settings.google_api_key,
            temperature=0.1,
        )

        await _registry.discover()
        peer_descriptions = _registry.agent_descriptions(exclude="attractions")
        system_with_peers = (
            system
            + f"\n\nAvailable peer agents (use call_peer_agent to contact them):\n{peer_descriptions}"
        )

        peer_call_log: list[str] = []
        tools = [
            make_rag_tool(
                _rag_stores, "destinations",
                "search_destinations",
                "Search the destinations knowledge base for attractions, landmarks, "
                "and highlights at the destination.",
            ),
            make_rag_tool(
                _rag_stores, "preferences",
                "search_preferences",
                "Search traveler preferences to align attraction recommendations "
                "with stated interests (art, food, adventure, history, etc.).",
            ),
            make_rag_tool(
                _rag_stores, "previous_itineraries",
                "search_previous_itineraries",
                "Search previous itineraries for proven attraction picks and "
                "known pitfalls at this destination.",
            ),
            make_generic_peer_tool(
                _registry, session_id,
                extra_data={"start_date": start_date, "end_date": end_date},
                peer_call_log=peer_call_log,
                caller=CARD.name,
            ),
        ]

        agent_executor = create_react_agent(model, tools)
        response = await agent_executor.ainvoke({
            "messages": [SystemMessage(content=system_with_peers), HumanMessage(content=user_msg)]
        })
        final_text   = response["messages"][-1].content
        total_tokens = sum(
            (getattr(m, "additional_kwargs", {}) or {}).get("_usage", {}).get("total_tokens", 0)
            for m in response["messages"]
        )

        result = parse_agent_json(final_text, fallback)
        result["_peer_calls"]  = peer_call_log
        result["_token_usage"] = total_tokens

        logger.info(
            "Attractions Agent done: clusters=%d tokens=%d peer_calls=%s (session=%s)",
            len(result.get("clusters", [])), total_tokens, peer_call_log, session_id,
        )
        task.complete(result)
        return result
    except Exception:
        task.fail()
        raise


app = create_agent_app(CARD, _handle, startup=_startup)
