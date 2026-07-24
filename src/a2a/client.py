"""A2AClient — HTTP client for calling A2A agent servers.

Usage:
    client = A2AClient("http://127.0.0.1:8001")
    card   = await client.get_agent_card()
    ok     = await client.health_check()
    result = await client.send_message("Search flights", data={...}, session_id="...")
"""
from __future__ import annotations

import logging
import uuid

import httpx

from src.config.constants import CARD_TIMEOUT, HEALTH_TIMEOUT, SEND_TIMEOUT
from src.a2a.models import (
    AgentCard,
    Artifact,
    DataPart,
    Message,
    MessageSendParams,
    SendMessageRequest,
    SendMessageResponse,
    Task,
    TaskStatus,
    TextPart,
)

logger = logging.getLogger(__name__)


class A2AClient:
    """Typed HTTP client implementing the Google A2A protocol."""

    def __init__(self, agent_url: str) -> None:
        self.agent_url = agent_url.rstrip("/")

    # ── Discovery & health ────────────────────────────────────────────────────

    async def get_agent_card(self) -> AgentCard:
        """Fetch the AgentCard — agent discovery step."""
        async with httpx.AsyncClient(timeout=CARD_TIMEOUT) as client:
            resp = await client.get(f"{self.agent_url}/.well-known/agent.json")
            resp.raise_for_status()
            return AgentCard(**resp.json())

    async def health_check(self) -> bool:
        """Return True if the agent is reachable and healthy."""
        try:
            async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT) as client:
                resp = await client.get(f"{self.agent_url}/health")
                return resp.status_code == 200
        except Exception:
            return False


    # ── Task submission ───────────────────────────────────────────────────────

    async def send_message(
        self,
        text: str,
        *,
        data: dict | None = None,
        session_id: str | None = None,
    ) -> dict:
        """
        Send a message/send JSON-RPC request to the agent.

        Builds a Message with a TextPart (human-readable task) and an optional
        DataPart (structured input payload). Returns the DataPart from the first
        artifact in the completed Task.
        """
        parts: list = [TextPart(text=text)]
        if data:
            parts.append(DataPart(data=data))

        request = SendMessageRequest(
            id=str(uuid.uuid4()),
            params=MessageSendParams(
                message=Message(role="user", parts=parts),
                session_id=session_id,
            ),
        )

        logger.info("A2A → %s  %s", self.agent_url, text[:80])

        async with httpx.AsyncClient(timeout=SEND_TIMEOUT) as client:
            resp = await client.post(
                f"{self.agent_url}/send_message",
                json=request.model_dump(mode="json"),
            )
            resp.raise_for_status()

        response = SendMessageResponse(**resp.json())

        if response.error:
            raise RuntimeError(f"A2A agent error from {self.agent_url}: {response.error}")

        task: Task = response.result
        if task.status == TaskStatus.FAILED:
            raise RuntimeError(f"A2A task {task.id} failed: {task.error}")

        # Extract the DataPart from the first artifact
        result = _extract_data(task)
        logger.info("A2A ← %s  task %s completed", self.agent_url, task.id)
        return result


def _extract_data(task: Task) -> dict:
    """Pull the DataPart payload out of a completed Task's first artifact."""
    for artifact in task.artifacts:
        for part in artifact.parts:
            if isinstance(part, DataPart):
                return part.data
    raise RuntimeError(f"Task {task.id} returned no DataPart artifact")
