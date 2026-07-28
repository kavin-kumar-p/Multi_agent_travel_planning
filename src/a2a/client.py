"""A2A client — official a2a-sdk v1.1+ (create_client + minimal_agent_card).

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
from google.protobuf import json_format, struct_pb2

from a2a.client import A2ACardResolver
from a2a.client.client import ClientCallContext, ClientConfig
from a2a.client.client_factory import create_client, minimal_agent_card
from a2a.types import (
    AgentCard,
    Message,
    Part,
    Role,
    SendMessageRequest,
)
from a2a.utils.constants import PROTOCOL_VERSION_1_0, VERSION_HEADER, TransportProtocol

from src.config.constants import CARD_TIMEOUT, HEALTH_TIMEOUT, SEND_TIMEOUT

logger = logging.getLogger(__name__)

# v1.0 protocol version context — required by DefaultRequestHandlerV2's validate_version decorator
_V1_CONTEXT = ClientCallContext(service_parameters={VERSION_HEADER: PROTOCOL_VERSION_1_0})


class A2AClient:
    """A2A client using the official SDK create_client + minimal_agent_card."""

    def __init__(self, agent_url: str) -> None:
        self.agent_url = agent_url.rstrip("/")

    async def get_agent_card(self) -> AgentCard:
        """Fetch and return the SDK AgentCard from /.well-known/agent.json."""
        async with httpx.AsyncClient(timeout=CARD_TIMEOUT) as http:
            resolver = A2ACardResolver(http, self.agent_url)
            return await resolver.get_agent_card()

    async def health_check(self) -> bool:
        """Return True if the agent is reachable and healthy."""
        try:
            async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT) as http:
                resp = await http.get(f"{self.agent_url}/health")
                return resp.status_code == 200
        except Exception:
            return False

    async def send_message(
        self,
        text: str,
        *,
        data: dict | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Send a message and return the DataPart result dict."""
        parts: list[Part] = [Part(text=text)]
        if data:
            value = struct_pb2.Value()
            json_format.ParseDict(data, value)
            parts.append(Part(data=value))

        request = SendMessageRequest(
            message=Message(
                message_id=str(uuid.uuid4()),
                context_id=session_id or str(uuid.uuid4()),
                role=Role.ROLE_USER,
                parts=parts,
            )
        )

        logger.info("A2A → %s  %s", self.agent_url, text[:80])

        config = ClientConfig(
            httpx_client=httpx.AsyncClient(timeout=SEND_TIMEOUT),
            streaming=False,
        )
        sdk_client = await create_client(
            minimal_agent_card(url=self.agent_url, transports=[TransportProtocol.JSONRPC]),
            client_config=config,
        )

        task = None
        async for stream_response in sdk_client.send_message(request, context=_V1_CONTEXT):
            if stream_response.HasField("task"):
                task = stream_response.task

        if task is None:
            raise RuntimeError(f"No task in response from {self.agent_url}")

        result = _extract_data(task)
        logger.info("A2A ← %s  task %s completed", self.agent_url, task.id)
        return result


def _extract_data(task) -> dict:
    """Pull the data payload from the first artifact of a completed Task."""
    for artifact in task.artifacts:
        for part in artifact.parts:
            try:
                if part.HasField("data"):
                    raw = json_format.MessageToDict(part.data)
                    if isinstance(raw, dict):
                        return raw
            except ValueError:
                pass
    raise RuntimeError(f"Task {task.id} returned no data artifact")
