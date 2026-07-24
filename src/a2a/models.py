"""Google A2A protocol types — Task, Message, Part, Artifact, AgentCard.

Follows the Agent2Agent Protocol specification:
  https://google.github.io/A2A/specification/
"""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


# ── Agent identity ────────────────────────────────────────────────────────────

class AgentCapabilities(BaseModel):
    streaming: bool = False
    push_notifications: bool = False
    state_transition_history: bool = False


class AgentCard(BaseModel):
    """Agent's 'digital business card' — served at /.well-known/agent.json."""
    name: str
    description: str
    url: str
    version: str = "1.0.0"
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    input_modes: list[str] = ["text", "data"]
    output_modes: list[str] = ["data"]


# ── Task lifecycle ────────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    SUBMITTED = "submitted"
    WORKING   = "working"
    COMPLETED = "completed"
    FAILED    = "failed"
    CANCELED  = "canceled"


# ── Message content parts ─────────────────────────────────────────────────────

class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class DataPart(BaseModel):
    type: Literal["data"] = "data"
    data: dict[str, Any]


# Discriminated union — Pydantic resolves the correct subtype from "type" field
Part = Annotated[Union[TextPart, DataPart], Field(discriminator="type")]


# ── Core A2A objects ──────────────────────────────────────────────────────────

class Message(BaseModel):
    role: Literal["user", "agent"]
    parts: list[Part]
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class Artifact(BaseModel):
    """Output produced by an agent task."""
    artifact_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str | None = None
    parts: list[Part]
    index: int = 0


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str | None = None
    status: TaskStatus = TaskStatus.SUBMITTED
    messages: list[Message] = []
    artifacts: list[Artifact] = []
    error: str | None = None


# ── JSON-RPC 2.0 request/response ─────────────────────────────────────────────

class MessageSendParams(BaseModel):
    message: Message
    session_id: str | None = None
    metadata: dict[str, Any] = {}


class SendMessageRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    method: Literal["message/send"] = "message/send"
    params: MessageSendParams


class SendMessageResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: str
    result: Task | None = None
    error: dict[str, Any] | None = None
