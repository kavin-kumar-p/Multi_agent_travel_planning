"""Entry point — run the multi-agent travel planning system."""
from __future__ import annotations

import asyncio
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s — %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    from src.coordinator.coordinator import TravelRequest, run as coordinator_run
    from src.llm.factory import get_embeddings
    from src.rag.bootstrap import load_or_build

    logger.info("Bootstrapping RAG indexes...")
    embeddings = get_embeddings()
    rag_stores = await load_or_build(embeddings)
    logger.info("RAG ready — %d collections loaded", len(rag_stores))

    request = TravelRequest(
        origin="JFK",
        destination="Paris, France",
        start_date="2026-10-10",
        end_date="2026-10-15",
        total_budget=3000.0,
        interests=["art", "history", "food"],
    )

    logger.info("Starting coordinator for %s...", request.destination)
    itinerary = await coordinator_run(request, rag_stores)

    print("\n" + "=" * 60)
    print("TRAVEL ITINERARY")
    print("=" * 60)
    print(json.dumps(itinerary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
