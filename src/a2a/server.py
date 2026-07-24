"""Base FastAPI factory — wires standard A2A endpoints onto any agent handler.

Usage in each agent server:
    app = create_agent_app(card, handler, startup=_startup)

Where:
    card     — AgentCard served at /.well-known/agent.json
    handler  — async fn(input_data: dict) -> dict
    startup  — optional async fn() called once at uvicorn startup
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

from fastapi import FastAPI

from src.a2a.models import (
    AgentCard,
    Artifact,
    DataPart,
    SendMessageRequest,
    SendMessageResponse,
    Task,
    TaskStatus,
)

logger = logging.getLogger(__name__)


def create_agent_app(
    card: AgentCard,
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    startup: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    """
    Create a FastAPI app implementing the A2A protocol.

    Endpoints:
      GET  /.well-known/agent.json  — AgentCard
      GET  /health                  — liveness probe
      POST /send_message            — JSON-RPC 2.0 task submission
    """

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        if startup:
            await startup()
        yield

    app = FastAPI(title=card.name, lifespan=_lifespan)

    # ── Discovery ─────────────────────────────────────────────────────────────

    @app.get("/.well-known/agent.json", response_model=AgentCard)
    async def agent_card() -> AgentCard:
        return card

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "agent": card.name}

    # ── Task submission ───────────────────────────────────────────────────────

    @app.post("/send_message", response_model=SendMessageResponse)
    async def send_message(request: SendMessageRequest) -> SendMessageResponse:
        task = Task(
            id=str(uuid.uuid4()),
            session_id=request.params.session_id,
            status=TaskStatus.WORKING,
            messages=[request.params.message],
        )
        try:
            # Flatten all parts into a single input_data dict
            input_data: dict[str, Any] = {}
            for part in request.params.message.parts:
                if hasattr(part, "data"):
                    input_data.update(part.data)
                elif hasattr(part, "text"):
                    input_data.setdefault("_text", part.text)

            result = await handler(input_data)

            task.artifacts = [
                Artifact(
                    name="result",
                    parts=[DataPart(data=result)],
                )
            ]
            task.status = TaskStatus.COMPLETED

        except Exception as exc:
            logger.exception("Task failed in agent '%s'", card.name)
            task.status = TaskStatus.FAILED
            task.error  = str(exc)

        return SendMessageResponse(id=request.id, result=task)

    return app
