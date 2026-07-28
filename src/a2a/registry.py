"""Dynamic peer-agent discovery via AgentCard.

Each agent calls discover() once on its first incoming request.
By that point the coordinator has already health-checked all four agents,
so every candidate URL is guaranteed to be reachable.

Usage inside any agent's _handle():
    await _registry.discover()
    url = _registry.find("flight")   # → "http://127.0.0.1:8001"
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Discovers peer A2A agents by fetching their AgentCards.

    Discovery is lazy — runs once on the first call to discover(), then the
    results are cached for the lifetime of the process. Subsequent calls to
    discover() return immediately without making any HTTP requests.

    find(keyword) matches the keyword against each discovered agent's name
    (case-insensitive substring match) and returns the URL.
    """

    def __init__(self, candidate_urls: list[str]) -> None:
        self._candidates = candidate_urls
        self._cards: dict[str, str] = {}   # lowercased agent name → url
        self._discovered = False
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def discover(self) -> None:
        """Fetch AgentCards from all candidate URLs and cache the results."""
        async with self._get_lock():
            if self._discovered:
                return
            from src.a2a.client import A2AClient
            for url in self._candidates:
                try:
                    card = await A2AClient(url).get_agent_card()
                    self._cards[card.name.lower()] = (url, card.description)
                    logger.info("AgentRegistry: discovered '%s' at %s", card.name, url)
                except Exception as exc:
                    logger.debug("AgentRegistry: no response at %s: %s", url, exc)
            self._discovered = True

    def find(self, agent_name: str) -> str:
        """Return the URL for the agent whose name contains agent_name (case-insensitive).

        Raises RuntimeError if not found — surfaces as FAILED task in the Streamlit UI.
        """
        kw = agent_name.lower()
        for name, (url, _) in self._cards.items():
            if kw in name:
                return url
        discovered = list(self._cards.keys()) or ["none"]
        raise RuntimeError(
            f"Agent discovery failed: no agent matching '{agent_name}' found. "
            f"Discovered: {discovered}. Check that all agent servers are running."
        )

    def agent_descriptions(self, exclude: str = "") -> str:
        """Return a formatted list of all discovered agents and their descriptions.

        Injected into each agent's system prompt so the LLM can autonomously
        decide which peer agents are relevant to call for a given task.

        exclude: skip the agent with this name (an agent shouldn't list itself).
        """
        lines = []
        skip = exclude.lower()
        for name, (url, description) in self._cards.items():
            if skip and skip in name:
                continue
            lines.append(f'- "{name.title()}": {description}')
        return "\n".join(lines) if lines else "No peer agents discovered."
