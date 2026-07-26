"""Tool factories for agent frameworks (LangGraph + CrewAI).

LangGraph agents (Flight, Attractions) use the LangChain StructuredTool variants
returned by make_*() factories — these are async-native.

CrewAI agents (Hotel, Transport) use the CrewAI BaseTool subclasses returned by
make_*_crew() factories — CrewAI's BaseTool.run() auto-handles coroutines via
asyncio.run(), which works because crew.kickoff() runs in asyncio.to_thread().

All business logic is delegated to src/tools/ (search_flights, search_hotels,
search_transit) and src/rag/retriever.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from crewai.tools import BaseTool as CrewBaseTool
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from src import tools as mcp_tools
from src.rag.retriever import retrieve

logger = logging.getLogger(__name__)


# ── Shared argument schemas ───────────────────────────────────────────────────

class _QueryInput(BaseModel):
    query: str = Field(description="Search query string")


class _PeerCallInput(BaseModel):
    agent_name: str = Field(
        description="Name of the peer agent to call, e.g. 'Flight Agent' or 'Attractions Agent'"
    )
    question: str = Field(description="Natural-language question to ask the agent")


class _FlightSearchInput(BaseModel):
    origin: str       = Field(description="Departure airport code or city (e.g. JFK)")
    destination: str  = Field(description="Arrival airport code or city (e.g. NRT)")
    start_date: str   = Field(description="Departure date YYYY-MM-DD")
    end_date: str     = Field(description="Return date YYYY-MM-DD")
    budget_cap: float = Field(description="Maximum total price in USD")


class _HotelSearchInput(BaseModel):
    budget_per_night: float = Field(description="Maximum price per night in USD")


class _TransitSearchInput(BaseModel):
    dest: str = Field(description="Destination city or country")


# ── LangGraph tool factories (async StructuredTool) ───────────────────────────
# Used by Flight Agent and Attractions Agent.

def make_rag_tool(
    rag_stores: dict,
    collection: str,
    name: str,
    description: str,
) -> StructuredTool:
    """Searches a specific FAISS RAG collection."""
    def _search(query: str) -> str:
        chunks = retrieve(rag_stores, collection, query)
        return "\n".join(chunks) or f"No results found in {collection}."

    return StructuredTool.from_function(
        func=_search,
        name=name,
        description=description,
        args_schema=_QueryInput,
    )


def make_flight_search_tool(session_id: str, raw_cache: dict) -> StructuredTool:
    """Searches the MCP flights inventory. Stores raw results as fallback."""
    async def _search(
        origin: str,
        destination: str,
        start_date: str,
        end_date: str,
        budget_cap: float,
    ) -> str:
        flights = await asyncio.to_thread(
            mcp_tools.search_flights, origin, destination, start_date, end_date, budget_cap
        )
        raw_cache[session_id] = flights
        logger.info("search_available_flights: %d options for %s→%s", len(flights), origin, destination)
        return json.dumps(flights)

    return StructuredTool.from_function(
        coroutine=_search,
        name="search_available_flights",
        description=(
            "Search for available flights. Returns ALL matching flights from the database — "
            "the origin/destination in results may differ from the request (mock data). "
            "Always select the best option from what is returned; never return empty results "
            "if flights exist. Fields: flight_id, airline, class, price, layovers, duration_hours."
        ),
        args_schema=_FlightSearchInput,
    )


# ── CrewAI tool classes (sync-compatible BaseTool) ────────────────────────────
# Used by Hotel Agent and Transport Agent.
# CrewAI's BaseTool.run() detects coroutines and calls asyncio.run() on them,
# which works because crew.kickoff() runs inside asyncio.to_thread() (a plain thread).

class _CrewRAGTool(CrewBaseTool):
    """Searches a FAISS RAG collection (for CrewAI)."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str = "search_rag"
    description: str = "Search RAG knowledge base"
    args_schema: type[BaseModel] = _QueryInput
    _rag_stores: dict = PrivateAttr()
    _collection: str = PrivateAttr()

    def __init__(
        self, rag_stores: dict, collection: str, name: str, description: str, **data: Any
    ) -> None:
        super().__init__(name=name, description=description, **data)
        self._rag_stores = rag_stores
        self._collection = collection

    def _run(self, query: str) -> str:
        chunks = retrieve(self._rag_stores, self._collection, query)
        return "\n".join(chunks) or f"No results found in {self._collection}."


class _CrewHotelSearchTool(CrewBaseTool):
    """Searches the MCP hotels inventory (for CrewAI)."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str = "search_available_hotels"
    description: str = (
        "Search for available hotels in the destination within the per-night budget. "
        "Returns hotel options with name, location, price_per_night, amenities, review_score."
    )
    args_schema: type[BaseModel] = _HotelSearchInput
    _destination: str = PrivateAttr()
    _buffer: float = PrivateAttr()

    def __init__(self, destination: str, buffer: float, **data: Any) -> None:
        super().__init__(**data)
        self._destination = destination
        self._buffer = buffer

    def _run(self, budget_per_night: float) -> str:
        hotels = mcp_tools.search_hotels(self._destination, budget_per_night * self._buffer)
        logger.info("search_available_hotels: %d options in %s", len(hotels), self._destination)
        return json.dumps(hotels)


class _CrewTransitSearchTool(CrewBaseTool):
    """Searches the MCP transit inventory (for CrewAI)."""
    name: str = "search_available_transit"
    description: str = (
        "Search for available transit options at the destination including airport transfers, "
        "metro, bus, day passes, and taxi/ride-share pricing."
    )
    args_schema: type[BaseModel] = _TransitSearchInput

    def _run(self, dest: str) -> str:
        options = mcp_tools.search_transit(dest)
        logger.info("search_available_transit: %d options for %s", len(options), dest)
        return json.dumps(options)


# ── CrewAI factory helpers ─────────────────────────────────────────────────────

def make_rag_tool_crew(
    rag_stores: dict, collection: str, name: str, description: str,
) -> _CrewRAGTool:
    return _CrewRAGTool(rag_stores=rag_stores, collection=collection, name=name, description=description)


def make_hotel_search_tool_crew(destination: str, budget_hotel_buffer: float) -> _CrewHotelSearchTool:
    return _CrewHotelSearchTool(destination=destination, buffer=budget_hotel_buffer)


def make_transit_search_tool_crew() -> _CrewTransitSearchTool:
    return _CrewTransitSearchTool()


# ── Generic peer tool — LLM decides which agent to call ───────────────────────
# The LLM reads agent_descriptions injected into the system prompt and picks
# the right peer autonomously. No developer-wired per-agent tool needed.


def make_generic_peer_tool(
    registry: Any,
    session_id: str,
    extra_data: dict | None = None,
    peer_call_log: list | None = None,
) -> StructuredTool:
    """LangGraph: one tool that can call ANY discovered peer agent.

    The LLM chooses agent_name from the descriptions injected into the
    system prompt, then calls this tool with the chosen name + question.
    peer_call_log — if provided, each called agent_name is appended to it.
    """
    from src.a2a.client import A2AClient
    _extra = extra_data or {}

    async def _call(agent_name: str, question: str) -> str:
        if peer_call_log is not None:
            peer_call_log.append(agent_name)
        try:
            url = registry.find(agent_name)
            result = await A2AClient(url).send_message(
                text=question,
                data={"session_id": session_id, **_extra},
            )
            logger.info("call_peer_agent '%s' responded: %s", agent_name, str(result)[:120])
            return json.dumps(result)
        except Exception as exc:
            logger.warning("call_peer_agent '%s' failed: %s", agent_name, exc)
            return json.dumps({"error": str(exc)})

    return StructuredTool.from_function(
        coroutine=_call,
        name="call_peer_agent",
        description=(
            "Call a peer agent by name to get information needed for your task. "
            "agent_name must match one of the available peer agents listed above."
        ),
        args_schema=_PeerCallInput,
    )


class _CrewGenericPeerTool(CrewBaseTool):
    """CrewAI: one tool that can call ANY discovered peer agent."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str = "call_peer_agent"
    description: str = (
        "Call a peer agent by name to get information needed for your task. "
        "agent_name must match one of the available peer agents listed above."
    )
    args_schema: type[BaseModel] = _PeerCallInput
    _registry: Any = PrivateAttr()
    _session_id: str = PrivateAttr()
    _extra: dict = PrivateAttr()
    _log: Any = PrivateAttr(default=None)

    def __init__(
        self,
        registry: Any,
        session_id: str,
        extra_data: dict | None = None,
        peer_call_log: list | None = None,
        **data: Any,
    ) -> None:
        super().__init__(**data)
        self._registry = registry
        self._session_id = session_id
        self._extra = extra_data or {}
        self._log = peer_call_log

    async def _run(self, agent_name: str, question: str) -> str:
        from src.a2a.client import A2AClient
        if self._log is not None:
            self._log.append(agent_name)
        try:
            url = self._registry.find(agent_name)
            result = await A2AClient(url).send_message(
                text=question,
                data={"session_id": self._session_id, **self._extra},
            )
            logger.info("call_peer_agent '%s' responded: %s", agent_name, str(result)[:120])
            return json.dumps(result)
        except Exception as exc:
            logger.warning("call_peer_agent '%s' failed: %s", agent_name, exc)
            return json.dumps({"error": str(exc)})


def make_generic_peer_tool_crew(
    registry: Any,
    session_id: str,
    extra_data: dict | None = None,
    peer_call_log: list | None = None,
) -> _CrewGenericPeerTool:
    return _CrewGenericPeerTool(
        registry=registry,
        session_id=session_id,
        extra_data=extra_data,
        peer_call_log=peer_call_log,
    )
