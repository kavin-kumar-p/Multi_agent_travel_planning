"""Session state — mirrors Google ADK's DatabaseSessionService contract.

Holds per-request budget accounting and agent results. In production this
would be persisted via ADK's DatabaseSessionService (SQLite or Postgres).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TravelSession:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    total_budget: float = 0.0
    spent: float = 0.0
    per_agent_caps: dict[str, float] = field(default_factory=dict)
    agent_results: dict[str, Any] = field(default_factory=dict)

    @property
    def remaining(self) -> float:
        return self.total_budget - self.spent

    def within_budget(self) -> bool:
        return self.spent <= self.total_budget

    def record_cost(self, agent: str, cost: float) -> None:
        self.spent = round(self.spent + cost, 2)
        self.agent_results.setdefault(agent, {})["cost"] = cost
