"""Transport Agent — CrewAI.

Pattern:
  Step 1: RAG → destinations (transport section)
  Step 2: MCP → search_transit
  Step 3: LLM plans airport transfers and daily transit → structured JSON
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


async def run_transport_agent(
    rag_stores: dict[str, FAISS],
    destination: str,
    hotel_location: str,
    attraction_clusters: list[dict],
    confirmed_dates: str,
    budget_cap: float,
) -> dict:
    prompt = load_prompt("transport_agent")
    llm = get_llm(settings.transport_agent_model)

    dest_chunks = retrieve(rag_stores, "destinations", f"transport transit {destination}")
    transit_options = mcp.search_transit(destination)

    context = (
        f"## Destination Transport Info\n{chr(10).join(dest_chunks)}\n\n"
        f"## Live Transit Options\n{json.dumps(transit_options, indent=2, ensure_ascii=False)}"
    )
    user_msg = prompt["user_template"].format(
        destination=destination,
        hotel_location=hotel_location,
        attraction_clusters=json.dumps(attraction_clusters, ensure_ascii=False),
        confirmed_dates=confirmed_dates,
        budget_cap=budget_cap,
    )
    full_prompt = f"{prompt['system']}\n\n{context}\n\n## Task\n{user_msg}"

    from langchain_core.messages import HumanMessage
    response = await llm.ainvoke([HumanMessage(content=full_prompt)])

    fallback: dict = {"airport_transfer": {}, "daily_transit": {}, "total_cost": 0, "tips": []}
    if transit_options:
        opt = transit_options[0]
        fallback = {
            "airport_transfer": opt.get("airport_transfer", {}),
            "daily_transit": opt.get("daily_pass", {}),
            "total_cost": 50,
            "tips": opt.get("tips", []),
        }

    result = parse_agent_json(str(response.content), fallback)
    logger.info("Transport agent result: total_cost=$%s", result.get("total_cost"))
    return result
