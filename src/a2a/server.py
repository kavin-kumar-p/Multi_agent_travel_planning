"""A2A server — official a2a-sdk v1.1+ route injection pattern.

Routes added to the FastAPI app:
  GET  /.well-known/agent.json — AgentCard (SDK default path)
  POST /                        — JSON-RPC 2.0 message/send (SDK default)
  GET  /health                  — liveness probe (responds immediately)
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

from fastapi import FastAPI
from google.protobuf import json_format, struct_pb2

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCard, Part, Task, TaskState, TaskStatus

logger = logging.getLogger(__name__)


class NeedsInputError(Exception):
    """Raise from a handler to signal INPUT_REQUIRED (kept for interface compat)."""
    def __init__(self, question: str, context: dict | None = None) -> None:
        self.question = question
        self.context = context or {}
        super().__init__(question)


class _BridgeExecutor(AgentExecutor):
    """Adapts _handle(dict) -> dict to the SDK's AgentExecutor.execute interface."""

    def __init__(self, handler: Callable, ready: asyncio.Event) -> None:
        self._handler = handler
        self._ready = ready

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)

        # v1.1.2 requires a Task proto to be enqueued before any TaskStatusUpdateEvent
        await event_queue.enqueue_event(Task(
            id=context.task_id,
            context_id=context.context_id or "",
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        ))
        await updater.start_work()

        # Wait for startup (RAG loading) to finish before handling requests
        await self._ready.wait()

        input_data: dict[str, Any] = {}
        if context.message:
            for part in context.message.parts:
                if part.text:
                    input_data.setdefault("_text", part.text)
                else:
                    try:
                        if part.HasField("data"):
                            raw = json_format.MessageToDict(part.data)
                            if isinstance(raw, dict):
                                input_data.update(raw)
                    except ValueError:
                        pass
            input_data.setdefault("session_id", context.context_id or "")

        try:
            result = await self._handler(input_data)
            value = struct_pb2.Value()
            json_format.ParseDict(result, value)
            await updater.add_artifact([Part(data=value)], name="result")
            await updater.complete()
        except Exception:
            logger.exception("Agent handler failed")
            await updater.failed()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()


def create_agent_app(
    card: AgentCard,
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    startup: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    """Build a FastAPI app using the official a2a-sdk v1.1+ route injection pattern.

    /health responds immediately so the launcher health-poll passes quickly.
    The actual startup (RAG loading) runs as a background task; requests
    block inside _BridgeExecutor until the ready event is set.
    """
    ready = asyncio.Event()

    task_store = InMemoryTaskStore()
    request_handler = DefaultRequestHandler(
        agent_executor=_BridgeExecutor(handler, ready),
        task_store=task_store,
        agent_card=card,
    )

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        # uvicorn --log-level warning resets the root logger to WARNING,
        # which silences all INFO logs from src.* application code.
        # Re-enable them here so peer-call and tool logs appear in the terminal.
        logging.getLogger("src").setLevel(logging.INFO)

        async def _run_startup():
            try:
                if startup:
                    await startup()
            finally:
                ready.set()  # unblock requests even if startup fails

        asyncio.create_task(_run_startup())  # runs in background, /health responds immediately
        yield

    app = FastAPI(title=card.name, lifespan=_lifespan)

    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(request_handler, rpc_url="/"),
    )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "agent": card.name}

    return app
