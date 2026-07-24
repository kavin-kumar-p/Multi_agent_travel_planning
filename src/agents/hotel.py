"""Hotel Agent — CrewAI.

Pattern:
  Step 1: RAG → hotel_details
  Step 2: MCP → search_hotels (filtered by destination + nightly budget)
  Step 3: LLM applies policy and selects the best option → structured JSON
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from langchain_community.vectorstores import FAISS

from src.config.settings import settings
from src.llm.factory import get_llm
from src.mcp import tools as mcp
from src.prompts import load_prompt
from src.rag.retriever import retrieve
from src.utils import parse_agent_json

logger = logging.getLogger(__name__)


def _nights(start_date: str, end_date: str) -> int:
    try:
        return max(1, (datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")).days)
    except ValueError:
        return 5


async def run_hotel_agent(
    rag_stores: dict[str, FAISS],
    destination: str,
    start_date: str,
    end_date: str,
    attraction_clusters: list[dict],
    budget_cap: float,
) -> dict:
    prompt = load_prompt("hotel_agent")
    llm = get_llm(settings.hotel_agent_model)

    num_nights = _nights(start_date, end_date)
    budget_per_night = budget_cap / num_nights

    hotel_chunks = retrieve(rag_stores, "hotel_details", f"hotel {destination} 3 star 4 star")
    hotels = mcp.search_hotels(destination, budget_per_night * 1.2)

    context = (
        f"## Hotel Knowledge Base\n{chr(10).join(hotel_chunks)}\n\n"
        f"## Available Hotels (filtered by destination)\n{json.dumps(hotels, indent=2, ensure_ascii=False)}"
    )
    user_msg = prompt["user_template"].format(
        destination=destination,
        start_date=start_date,
        end_date=end_date,
        attraction_clusters=json.dumps(attraction_clusters, ensure_ascii=False),
        budget_cap=budget_cap,
    )
    full_prompt = f"{prompt['system']}\n\n{context}\n\n## Task\n{user_msg}"

    from langchain_core.messages import HumanMessage
    response = await llm.ainvoke([HumanMessage(content=full_prompt)])

    fallback: dict = {"recommended_hotel": {}, "over_budget": False, "policy_notes": ""}
    if hotels:
        h = hotels[0]
        fallback["recommended_hotel"] = {**h, "total_cost": h.get("price_per_night", 0) * num_nights}

    result = parse_agent_json(str(response.content), fallback)
    logger.info("Hotel agent result: %s", result.get("recommended_hotel", {}).get("name"))
    return result
