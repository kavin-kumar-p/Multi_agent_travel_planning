"""MCP server — exposes the travel search tools over the MCP protocol.

Run standalone:
    python -m src.mcp.server

Agents connect to this server and call tools via the MCP client.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from src.mcp.tools import search_flights, search_hotels, search_transit

mcp = FastMCP("travel-planning")


@mcp.tool()
def find_flights(
    origin: str,
    destination: str,
    start_date: str,
    end_date: str,
    budget_cap: float,
) -> list[dict]:
    """Search available flights within a budget cap."""
    return search_flights(origin, destination, start_date, end_date, budget_cap)


@mcp.tool()
def find_hotels(destination: str, budget_per_night: float) -> list[dict]:
    """Search hotels at a destination within a nightly budget."""
    return search_hotels(destination, budget_per_night)


@mcp.tool()
def find_transit(destination: str) -> list[dict]:
    """Get local transit options for a destination."""
    return search_transit(destination)


if __name__ == "__main__":
    mcp.run()
