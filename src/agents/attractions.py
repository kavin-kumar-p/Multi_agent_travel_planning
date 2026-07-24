"""Attractions Agent — LangGraph.

3-turn ReAct pattern:
  Turn 1: RAG → destinations, preferences, previous_itineraries
  Turn 2: LLM clusters attractions by area into daily schedules
  Turn 3: Returns structured JSON with day-by-day plan
"""
from __future__ import annotations

import json
import logging

from langchain_community.vectorstores import FAISS

from src.config.settings import settings
from src.llm.factory import get_llm
from src.prompts import load_prompt
from src.rag.retriever import retrieve
from src.utils import parse_agent_json

logger = logging.getLogger(__name__)


async def run_attractions_agent(
    rag_stores: dict[str, FAISS],
    destination: str,
    confirmed_dates: str,
    interests: list[str],
    budget_cap: float,
) -> dict:
    prompt = load_prompt("attractions_agent")
    llm = get_llm(settings.attractions_agent_model)

    interests_str = ", ".join(interests)
    query = f"{destination} attractions {interests_str}"

    dest_chunks = retrieve(rag_stores, "destinations", query)
    pref_chunks = retrieve(rag_stores, "preferences", interests_str)
    prev_chunks = retrieve(rag_stores, "previous_itineraries", f"{destination} itinerary highlights")

    context = (
        f"## Destination Info\n{chr(10).join(dest_chunks)}\n\n"
        f"## Traveler Preferences\n{chr(10).join(pref_chunks)}\n\n"
        f"## Previous Itineraries\n{chr(10).join(prev_chunks)}"
    )
    user_msg = prompt["user_template"].format(
        destination=destination,
        confirmed_dates=confirmed_dates,
        interests=interests_str,
        budget_cap=budget_cap,
    )
    full_prompt = f"{prompt['system']}\n\n{context}\n\n## Task\n{user_msg}"

    from langchain_core.messages import HumanMessage
    response = await llm.ainvoke([HumanMessage(content=full_prompt)])

    fallback = {"clusters": [], "total_cost": 0, "notes": "Fallback — agent output could not be parsed"}
    result = parse_agent_json(str(response.content), fallback)
    logger.info("Attractions agent result: total_cost=$%s, clusters=%d", result.get("total_cost"), len(result.get("clusters", [])))
    return result
