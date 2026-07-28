"""Per-session task manager — mirrors the blog pattern of task lifecycle states.

Each agent holds one TaskManager instance. Lifecycle per session_id:
  working → completed (or failed)

When the coordinator fires all agents in parallel AND a peer agent calls the
same agent for the same session_id, the second caller:
  - Gets the stored result instantly if already completed  (fast path)
  - Awaits the asyncio.Event if still working             (wait path)

This eliminates redundant full-pipeline re-runs while keeping agents stateless
at the HTTP boundary. In production, swap the dict for Redis with a TTL.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AgentTask:
    state: str = "working"          # "working" | "completed" | "failed"
    result: dict | None = None
    _event: asyncio.Event = field(default_factory=asyncio.Event)

    async def wait(self) -> None:
        await self._event.wait()

    def complete(self, result: dict) -> None:
        self.result = result
        self.state = "completed"
        self._event.set()

    def fail(self) -> None:
        self.state = "failed"
        self._event.set()


class TaskManager:
    """Session-scoped task registry for one agent server."""

    def __init__(self) -> None:
        self._tasks: dict[str, AgentTask] = {}

    def start(self, session_id: str) -> tuple[bool, AgentTask]:
        """Atomically check-and-register a task.

        Returns (True, new_task)  — caller owns the pipeline run.
        Returns (False, existing) — caller should wait or read stored result.

        Safe under asyncio cooperative multitasking: no await between the
        dict lookup and dict set, so no other coroutine can interleave.
        """
        existing = self._tasks.get(session_id)
        if existing is None:
            task = AgentTask()
            self._tasks[session_id] = task
            return True, task
        return False, existing
