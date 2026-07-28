"""Flight Agent — LangGraph ReAct agent + A2A server on port 8001.

Framework: LangGraph (create_react_agent)
A2A endpoints:
  GET  /.well-known/agent.json
  GET  /health
  POST /send_message  ← coordinator OR peer agents send requests here

Prompt:   prompts/flight_agent.md   (system + autonomous + user_template)
Tools:    search_travel_policies (RAG), search_available_flights (MCP)
"""
from __future__ import annotations

import json
import logging
import os

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from a2a.types import AgentCard, AgentCapabilities, AgentInterface
from src.a2a.server import create_agent_app
from src.config.constants import FLIGHT_URL
from src.config.settings import settings
from src.llm.factory import get_embeddings
from src.llm.google_genai_model import GoogleGenAIChatModel
from src.prompts import load_prompt
from src.rag.bootstrap import load_or_build
from src.a2a.task_manager import TaskManager
from src.tools.agent_tools import make_flight_search_tool, make_rag_tool
from src.utils import parse_agent_json

os.environ.setdefault("GOOGLE_API_KEY", settings.google_api_key or "")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "1")

logger = logging.getLogger(__name__)

_rag_stores: dict = {}
_raw_search: dict[str, list] = {}
_model: GoogleGenAIChatModel | None = None
_task_manager = TaskManager()

CARD = AgentCard(
    name="Flight Agent",
    description=(
        "Searches and selects optimal flights using a LangGraph ReAct agent. "
        "Runs a full flight search for every request — coordinator task or peer query. "
        "Tools: search_travel_policies, search_available_flights."
    ),
    supported_interfaces=[AgentInterface(url=FLIGHT_URL)],
    version="1.0.0",
    capabilities=AgentCapabilities(),
    default_input_modes=["text", "data"],
    default_output_modes=["data"],
)


async def _startup() -> None:
    global _rag_stores, _model
    logger.info("Flight Agent: loading RAG stores…")
    _rag_stores = await load_or_build(get_embeddings())
    _model = GoogleGenAIChatModel(
        model=settings.flight_agent_model,
        google_api_key=settings.google_api_key,
        temperature=0.1,
    )
    logger.info("Flight Agent: ready on :8001")


async def _handle(input_data: dict) -> dict:
    session_id = input_data.get("session_id", "default")

    fallback: dict = {
        "recommended_flights": [],
        "cost": 0,
        "confirmed_dates": f"{input_data.get('start_date', '')} to {input_data.get('end_date', '')}",
        "over_budget": False,
        "policy_notes": "",
    }

    should_run, task = _task_manager.start(session_id)
    if not should_run:
        logger.info("Flight Agent: task already %s for session %s", task.state, session_id)
        await task.wait()
        return task.result or fallback

    try:
        _p    = load_prompt("flight_agent")
        system = _p["system"] + "\n\n" + _p["autonomous_decision_making"]

        text    = input_data.get("_text", "")
        context = {k: v for k, v in input_data.items() if k != "_text"}
        user_msg = _p["user_template"].format(
            origin=context.get("origin", ""),
            destination=context.get("destination", ""),
            start_date=context.get("start_date", ""),
            end_date=context.get("end_date", ""),
            budget_cap=context.get("budget_cap", 0),
        ) if context.get("origin") else f"{text}\n\nRequest context: {json.dumps(context)}"

        model = _model or GoogleGenAIChatModel(
            model=settings.flight_agent_model,
            google_api_key=settings.google_api_key,
            temperature=0.1,
        )
        tools = [
            make_rag_tool(
                _rag_stores, "travel_policies",
                "search_travel_policies",
                "Search the travel policies knowledge base for flight booking rules "
                "and class restrictions.",
            ),
            make_flight_search_tool(session_id, _raw_search),
        ]

        agent_executor = create_react_agent(model, tools)
        response = await agent_executor.ainvoke({
            "messages": [SystemMessage(content=system), HumanMessage(content=user_msg)]
        })
        final_text = response["messages"][-1].content
        total_tokens = sum(
            (getattr(m, "additional_kwargs", {}) or {}).get("_usage", {}).get("total_tokens", 0)
            for m in response["messages"]
        )

        result = parse_agent_json(final_text, fallback)

        if not result.get("confirmed_dates"):
            result["confirmed_dates"] = (
                f"{input_data.get('start_date', '')} to {input_data.get('end_date', '')}"
            )

        raw_flights = _raw_search.get(session_id, [])
        if not result.get("recommended_flights") and raw_flights:
            result["recommended_flights"] = raw_flights[:1]
            logger.info("Flight Agent: patched empty recommended_flights from raw search")
        if not result.get("cost") and result.get("recommended_flights"):
            result["cost"] = result["recommended_flights"][0].get("price", 0)

        recs = result.get("recommended_flights", [])
        if recs and raw_flights:
            raw_by_id = {f.get("flight_id"): f for f in raw_flights}
            for i, rec in enumerate(recs):
                if not rec.get("origin") or not rec.get("destination"):
                    src = raw_by_id.get(rec.get("flight_id")) or raw_flights[0]
                    recs[i] = {**rec, "origin": src.get("origin", "?"), "destination": src.get("destination", "?")}
            result["recommended_flights"] = recs

        result["_token_usage"] = total_tokens
        logger.info(
            "Flight Agent done: cost=$%s flights=%d tokens=%d (session=%s)",
            result.get("cost"), len(result.get("recommended_flights", [])), total_tokens, session_id,
        )
        task.complete(result)
        return result
    except Exception:
        task.fail()
        raise


app = create_agent_app(CARD, _handle, startup=_startup)
