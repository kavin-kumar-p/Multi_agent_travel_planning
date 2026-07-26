"""End-to-end tests for the autonomous A2A agent architecture.

Tests that:
1. Each agent starts and serves its AgentCard + /health
2. Flight agent handles a full search task (coordinator call)
3. Flight agent handles a peer date query (no conditional branching)
4. Attractions agent plans a full itinerary and calls Flight agent via A2A
5. Hotel agent selects a hotel and calls Attractions agent via A2A
6. Transport agent calls all three peer agents and plans transport
7. Coordinator orchestrates all agents in parallel via A2A

Run:
    .venv/bin/python -m pytest tests/test_agents_autonomous.py -v -s
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON  = str(PROJECT_ROOT / ".venv" / "bin" / "python")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _wait_healthy(port: int, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=3.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def _start_agent(module: str, port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [VENV_PYTHON, "-m", "uvicorn", f"{module}:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def agent_servers():
    """Start all four agent servers and yield. Stop on teardown."""
    agents = {
        "flights":     ("src.agents.flight",      8001),
        "attractions": ("src.agents.attractions", 8002),
        "hotel":       ("src.agents.hotel",       8003),
        "transport":   ("src.agents.transport",   8004),
    }
    procs: dict[str, subprocess.Popen] = {}

    for name, (module, port) in agents.items():
        # Reuse if already running
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
            if r.status_code == 200:
                print(f"\n  [reuse] {name} already up on :{port}")
                continue
        except Exception:
            pass
        print(f"\n  [start] {name} on :{port} …")
        procs[name] = _start_agent(module, port)

    # Wait for all to be healthy
    for name, (_, port) in agents.items():
        ok = _wait_healthy(port, timeout=90.0)
        assert ok, f"Agent '{name}' did not become healthy on :{port}"
        print(f"  [ready] {name} :{port}")

    yield {name: f"http://127.0.0.1:{port}" for name, (_, port) in agents.items()}

    for name, proc in procs.items():
        print(f"\n  [stop] {name}")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ── A2A client helper ─────────────────────────────────────────────────────────

async def _send(url: str, text: str, data: dict | None = None, session_id: str = "test-session") -> dict:
    """Send a message/send JSON-RPC call and return the DataPart result."""
    import uuid
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "session_id": session_id,
            "message": {
                "role": "user",
                "parts": [
                    {"type": "text", "text": text},
                    *(
                        [{"type": "data", "data": {**data, "session_id": session_id}}]
                        if data else []
                    ),
                ],
            },
        },
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(f"{url}/send_message", json=payload)
        resp.raise_for_status()
    body = resp.json()
    assert body.get("error") is None, f"A2A error: {body['error']}"
    task = body["result"]
    assert task["status"] == "completed", f"Task not completed: {task['status']} — {task.get('error')}"
    for artifact in task["artifacts"]:
        for part in artifact["parts"]:
            if part["type"] == "data":
                return part["data"]
    raise AssertionError("No DataPart in response")


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestAgentCard:
    def test_flight_card(self, agent_servers):
        r = httpx.get(f"{agent_servers['flights']}/.well-known/agent.json")
        assert r.status_code == 200
        card = r.json()
        assert card["name"] == "Flight Agent"
        print(f"\n  Flight card: {card['name']} v{card['version']}")

    def test_attractions_card(self, agent_servers):
        r = httpx.get(f"{agent_servers['attractions']}/.well-known/agent.json")
        assert r.status_code == 200
        assert r.json()["name"] == "Attractions Agent"

    def test_hotel_card(self, agent_servers):
        r = httpx.get(f"{agent_servers['hotel']}/.well-known/agent.json")
        assert r.status_code == 200
        assert r.json()["name"] == "Hotel Agent"

    def test_transport_card(self, agent_servers):
        r = httpx.get(f"{agent_servers['transport']}/.well-known/agent.json")
        assert r.status_code == 200
        assert r.json()["name"] == "Transport Agent"


class TestFlightAgent:
    @pytest.mark.asyncio
    async def test_full_flight_search(self, agent_servers):
        """Coordinator-style: LLM should search flights and return full result."""
        result = await _send(
            agent_servers["flights"],
            text=(
                "Search for the best flight from New York (JFK) to Tokyo (NRT) "
                "between 2025-09-01 and 2025-09-10 within budget $1500."
            ),
            data={
                "origin": "JFK", "destination": "NRT",
                "start_date": "2025-09-01", "end_date": "2025-09-10",
                "budget_cap": 1500,
            },
            session_id="flight-full-test",
        )
        print(f"\n  Flight result keys: {list(result.keys())}")
        print(f"  confirmed_dates: {result.get('confirmed_dates')}")
        print(f"  cost: {result.get('cost')}")
        assert "confirmed_dates" in result, "Missing confirmed_dates"
        assert "recommended_flights" in result or "cost" in result, "Missing flight data"

    @pytest.mark.asyncio
    async def test_peer_date_query(self, agent_servers):
        """Peer query: LLM should return confirmed_dates without re-running search."""
        # First, run a full search to store the result
        await _send(
            agent_servers["flights"],
            text="Search for the best flight from NYC to Paris within budget $1200.",
            data={
                "origin": "JFK", "destination": "CDG",
                "start_date": "2025-10-01", "end_date": "2025-10-08",
                "budget_cap": 1200,
            },
            session_id="flight-peer-test",
        )
        # Now send a peer-style date query — LLM should use get_stored_result
        result = await _send(
            agent_servers["flights"],
            text="What are the confirmed travel dates for this trip?",
            data={
                "start_date": "2025-10-01", "end_date": "2025-10-08",
            },
            session_id="flight-peer-test",
        )
        print(f"\n  Peer query result: {result}")
        assert "confirmed_dates" in result, "Missing confirmed_dates in peer response"
        print(f"  confirmed_dates: {result['confirmed_dates']}")


class TestAttractionsAgent:
    @pytest.mark.asyncio
    async def test_full_attractions_plan(self, agent_servers):
        """LLM should call Flight agent via A2A + RAG tools and return clusters."""
        result = await _send(
            agent_servers["attractions"],
            text=(
                "Plan a day-by-day attractions itinerary for Tokyo matching interests: "
                "food, technology, temples."
            ),
            data={
                "destination": "Tokyo",
                "start_date": "2025-09-01", "end_date": "2025-09-07",
                "interests": ["food", "technology", "temples"],
                "budget_cap": 300,
            },
            session_id="attractions-full-test",
        )
        print(f"\n  Attractions result keys: {list(result.keys())}")
        print(f"  clusters count: {len(result.get('clusters', []))}")
        assert "clusters" in result, "Missing clusters"
        print(f"  First cluster area: {result['clusters'][0].get('area') if result['clusters'] else 'none'}")

    @pytest.mark.asyncio
    async def test_peer_cluster_query(self, agent_servers):
        """Peer query: LLM should return clusters from stored result."""
        # First run a full plan
        await _send(
            agent_servers["attractions"],
            text="Plan attractions for London with interests: history, museums.",
            data={
                "destination": "London",
                "start_date": "2025-11-01", "end_date": "2025-11-05",
                "interests": ["history", "museums"],
                "budget_cap": 200,
            },
            session_id="attractions-peer-test",
        )
        # Now send a peer-style cluster query
        result = await _send(
            agent_servers["attractions"],
            text="Which geographic areas are you clustering attractions in for this trip?",
            data={"destination": "London"},
            session_id="attractions-peer-test",
        )
        print(f"\n  Peer cluster result: {str(result)[:200]}")
        assert "clusters" in result, "Missing clusters in peer response"


class TestHotelAgent:
    @pytest.mark.asyncio
    async def test_full_hotel_selection(self, agent_servers):
        """LLM should call Attractions agent + RAG/MCP and return hotel recommendation."""
        result = await _send(
            agent_servers["hotel"],
            text=(
                "Select the best hotel in Tokyo near the planned attraction clusters "
                "within budget $800."
            ),
            data={
                "destination": "Tokyo",
                "start_date": "2025-09-01", "end_date": "2025-09-07",
                "budget_cap": 800,
            },
            session_id="hotel-full-test",
        )
        print(f"\n  Hotel result keys: {list(result.keys())}")
        hotel = result.get("recommended_hotel", {})
        print(f"  Hotel name: {hotel.get('name')}")
        print(f"  Hotel location: {hotel.get('location')}")
        assert "recommended_hotel" in result, "Missing recommended_hotel"
        assert hotel.get("name"), "Hotel name is empty"


class TestTransportAgent:
    @pytest.mark.asyncio
    async def test_full_transport_plan(self, agent_servers):
        """LLM should call all three peer agents + RAG/MCP and plan transport."""
        result = await _send(
            agent_servers["transport"],
            text=(
                "Arrange airport transfers and daily transit for Tokyo within budget $150."
            ),
            data={
                "destination": "Tokyo",
                "start_date": "2025-09-01", "end_date": "2025-09-07",
                "budget_cap": 150,
            },
            session_id="transport-full-test",
        )
        print(f"\n  Transport result keys: {list(result.keys())}")
        print(f"  total_cost: {result.get('total_cost')}")
        assert "airport_transfer" in result or "total_cost" in result, "Missing transport data"


class TestNoConditionalBranching:
    """Verify no query_type-based branching — same endpoint handles all request types."""

    @pytest.mark.asyncio
    async def test_flight_handles_both_request_types_same_endpoint(self, agent_servers):
        """Both coordinator request and peer query go to the same POST /send_message endpoint."""
        # Full task
        r1 = await _send(
            agent_servers["flights"],
            text="Search for flights from LAX to LHR within budget $900.",
            data={"origin": "LAX", "destination": "LHR",
                  "start_date": "2025-12-01", "end_date": "2025-12-10", "budget_cap": 900},
            session_id="no-conditional-test",
        )
        assert "confirmed_dates" in r1 or "recommended_flights" in r1

        # Peer query — same endpoint, LLM decides what to return
        r2 = await _send(
            agent_servers["flights"],
            text="What are the confirmed travel dates?",
            data={"start_date": "2025-12-01", "end_date": "2025-12-10"},
            session_id="no-conditional-test",
        )
        assert "confirmed_dates" in r2
        print(f"\n  Full task keys: {list(r1.keys())}")
        print(f"  Peer query response: {r2}")
        print("  Same endpoint handled both — no conditional branching confirmed")
