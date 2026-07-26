"""LangChain wrapper around the google-genai v1 SDK with tool-calling support.

langchain_google_genai uses the legacy v1beta endpoint, which 404s on newer
models (gemini-3.1-flash-lite, etc.). This wrapper calls the v1 endpoint directly
via google.genai.Client, making all models available.

Key export: GoogleGenAIChatModel — BaseChatModel with bind_tools() support.
Preserves thought_signatures across multi-turn tool calls to avoid 400 errors.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from functools import lru_cache
from typing import Any, Sequence, Union

from google import genai
from google.genai import types as genai_types
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def _get_client(api_key: str) -> genai.Client:
    """Cached client — one httpx connection pool per API key."""
    return genai.Client(api_key=api_key)


def _extract_system(messages: list[BaseMessage]) -> str | None:
    for msg in messages:
        if isinstance(msg, SystemMessage):
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return None


def _to_contents(messages: list[BaseMessage]) -> list[genai_types.Content]:
    """Convert LangChain messages to google-genai Contents.

    Handles: HumanMessage, AIMessage (with/without tool_calls), ToolMessage.
    AIMessages that carry tool_calls store the original genai Content in
    additional_kwargs["_gemini_content"] so thought_signatures are preserved
    across multi-turn calls — without this, Gemini raises 400 INVALID_ARGUMENT.
    """
    contents = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            continue  # handled via system_instruction

        if isinstance(msg, HumanMessage):
            text = msg.content if isinstance(msg.content, str) else str(msg.content)
            contents.append(
                genai_types.Content(role="user", parts=[genai_types.Part(text=text)])
            )

        elif isinstance(msg, AIMessage):
            if msg.tool_calls:
                # Replay the original Content object to preserve thought_signatures.
                # If not cached (e.g. message created externally), fall back to reconstruction.
                raw = (msg.additional_kwargs or {}).get("_gemini_content")
                if raw is not None:
                    contents.append(raw)
                else:
                    parts = [
                        genai_types.Part.from_function_call(
                            name=tc["name"],
                            args=tc.get("args") or {},
                        )
                        for tc in msg.tool_calls
                    ]
                    contents.append(genai_types.Content(role="model", parts=parts))
            else:
                text = msg.content if isinstance(msg.content, str) else str(msg.content)
                contents.append(
                    genai_types.Content(role="model", parts=[genai_types.Part(text=text)])
                )

        elif isinstance(msg, ToolMessage):
            # Tool execution result — send back as function response
            result_text = msg.content if isinstance(msg.content, str) else str(msg.content)
            tool_name = getattr(msg, "name", None) or "tool"
            contents.append(
                genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part.from_function_response(
                            name=tool_name,
                            response={"result": result_text},
                        )
                    ],
                )
            )

    return contents


def _tools_to_declarations(
    tools: Sequence[Union[BaseTool, Any]],
) -> list[genai_types.FunctionDeclaration]:
    """Convert LangChain tools to google-genai FunctionDeclarations.

    Uses parametersJsonSchema (raw JSON Schema dict) to avoid manual type mapping.
    """
    declarations = []
    for t in tools:
        if not hasattr(t, "name"):
            continue
        # Get JSON Schema from args_schema (pydantic model)
        if hasattr(t, "args_schema") and t.args_schema is not None:
            try:
                schema = t.args_schema.model_json_schema()
            except Exception:
                try:
                    schema = t.args_schema.schema()
                except Exception:
                    schema = {"type": "object", "properties": {}}
        else:
            schema = {"type": "object", "properties": {}}

        # Drop $defs / $schema keys that google-genai rejects
        clean_schema = {
            k: v for k, v in schema.items()
            if k not in ("$defs", "$schema", "title")
        }

        fd = genai_types.FunctionDeclaration(
            name=t.name,
            description=getattr(t, "description", "") or "",
            parametersJsonSchema=clean_schema if clean_schema.get("properties") else None,
        )
        declarations.append(fd)
    return declarations


def _response_to_chat_result(response: Any) -> ChatResult:
    """Convert a google-genai GenerateContentResponse to a LangChain ChatResult.

    Handles both plain text responses and function-call responses.
    When the model issues tool calls, the original Content object (which carries
    thought_signatures) is stored in additional_kwargs["_gemini_content"] so
    _to_contents() can replay it verbatim on the next turn.
    """
    if not response.candidates:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=""))])

    candidate = response.candidates[0]
    parts = candidate.content.parts or []

    tool_calls = []
    text_parts: list[str] = []

    for part in parts:
        fc = getattr(part, "function_call", None)
        if fc and getattr(fc, "name", None):
            tool_calls.append({
                "name": fc.name,
                "args": dict(fc.args) if fc.args else {},
                "id": f"call_{fc.name}_{uuid.uuid4().hex[:8]}",
                "type": "tool_call",
            })
        elif getattr(part, "text", None):
            text_parts.append(part.text)

    # Extract token usage from response metadata
    um = getattr(response, "usage_metadata", None)
    usage = {
        "prompt_tokens":     getattr(um, "prompt_token_count",     0) or 0,
        "completion_tokens": getattr(um, "candidates_token_count", 0) or 0,
        "total_tokens":      getattr(um, "total_token_count",      0) or 0,
    } if um else {}

    if tool_calls:
        message = AIMessage(
            content="",
            tool_calls=tool_calls,
            additional_kwargs={"_gemini_content": candidate.content, "_usage": usage},
        )
    else:
        message = AIMessage(content="".join(text_parts), additional_kwargs={"_usage": usage})

    return ChatResult(generations=[ChatGeneration(message=message)])


def _build_config(
    system: str | None,
    temperature: float,
    declarations: list[genai_types.FunctionDeclaration],
) -> genai_types.GenerateContentConfig:
    kwargs: dict[str, Any] = {"temperature": temperature}
    if system:
        kwargs["system_instruction"] = system
    if declarations:
        kwargs["tools"] = [genai_types.Tool(function_declarations=declarations)]
        # Disable thinking when using tools — thought_signature conflict causes 400 errors
        kwargs["thinking_config"] = genai_types.ThinkingConfig(thinking_budget=0)
    return genai_types.GenerateContentConfig(**kwargs)


class GoogleGenAIChatModel(BaseChatModel):
    """ChatModel backed by google-genai v1 SDK — supports all AI Studio models.

    Implements bind_tools so create_react_agent and structured output work correctly.
    """

    model: str
    google_api_key: str
    temperature: float = 0.1
    # Bound function declarations — populated by bind_tools()
    bound_declarations: list = []

    @property
    def _llm_type(self) -> str:
        return "google-genai-v1"

    def _client(self) -> genai.Client:
        return _get_client(self.google_api_key)

    def bind_tools(
        self,
        tools: Sequence[Union[BaseTool, Any]],
        **kwargs: Any,
    ) -> "GoogleGenAIChatModel":
        """Return a copy of this model with the given tools bound for function calling."""
        declarations = _tools_to_declarations(tools)
        return self.model_copy(update={"bound_declarations": declarations})

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        response = self._client().models.generate_content(
            model=self.model,
            contents=_to_contents(messages),
            config=_build_config(
                _extract_system(messages), self.temperature, self.bound_declarations
            ),
        )
        return _response_to_chat_result(response)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        contents = _to_contents(messages)
        config   = _build_config(
            _extract_system(messages), self.temperature, self.bound_declarations
        )
        client = self._client()

        for attempt in range(5):
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=self.model,
                    contents=contents,
                    config=config,
                )
                return _response_to_chat_result(response)
            except Exception as exc:
                msg = str(exc)
                if "429" in msg and attempt < 4:
                    # Parse retryDelay from the error if present, else use backoff
                    import re
                    m = re.search(r"retry[^\d]*(\d+)", msg, re.IGNORECASE)
                    wait = int(m.group(1)) if m else 20 * (2 ** attempt)
                    logger.warning(
                        "Rate limited (429) — retrying in %ds (attempt %d/4)", wait, attempt + 1
                    )
                    await asyncio.sleep(wait)
                else:
                    raise
        raise RuntimeError("Exceeded max retries on 429 rate limit")
