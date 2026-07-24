"""Flight Agent — LangGraph.

3-turn ReAct pattern:
  Turn 1: RAG → travel_policies
  Turn 2: MCP → search_flights
  Turn 3: LLM reasons over both → structured JSON output
"""
from __future__ import annotations

import json
import logging

from langchain_community.vectorstores import FAISS

from src.config.settings import settings
from src.llm.factory import get_llm
from src.mcp import tools as mcp
from src.prompts import load_prompt
from src.rag.retriever import retrieve
from src.utils import parse_agent_json

logger = logging.getLogger(__name__)


async def run_flight_agent(
    rag_stores: dict[str, FAISS],
    origin: str,
    destination: str,
    start_date: str,
    end_date: str,
    budget_cap: float,
) -> dict:
    prompt = load_prompt("flight_agent")
    llm = get_llm(settings.flight_agent_model)

    policy_chunks = retrieve(rag_stores, "travel_policies", f"flight booking policy {destination}")
    policy_text = "\n\n".join(policy_chunks)

    flights = mcp.search_flights(origin, destination, start_date, end_date, budget_cap * 1.2)
    flights_text = json.dumps(flights, indent=2, ensure_ascii=False)

    user_msg = prompt["user_template"].format(
        origin=origin,
        destination=destination,
        start_date=start_date,
        end_date=end_date,
        budget_cap=budget_cap,
    )
    full_prompt = (
        f"{prompt['system']}\n\n"
        f"## Travel Policies\n{policy_text}\n\n"
        f"## Available Flights\n{flights_text}\n\n"
        f"## Task\n{user_msg}"
    )

    from langchain_core.messages import HumanMessage
    response = await llm.ainvoke([HumanMessage(content=full_prompt)])

    fallback = {
        "recommended_flights": flights[:1] if flights else [],
        "cost": flights[0]["price"] if flights else 0,
        "confirmed_dates": f"{start_date} to {end_date}",
        "over_budget": False,
        "policy_notes": "",
    }
    result = parse_agent_json(str(response.content), fallback)
    logger.info("Flight agent result: cost=$%s, over_budget=%s", result.get("cost"), result.get("over_budget"))
    return result
