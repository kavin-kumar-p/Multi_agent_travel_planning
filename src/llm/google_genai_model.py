"""LangChain wrapper around the google-genai v1 SDK.

langchain_google_genai uses the legacy v1beta endpoint, which 404s on newer
models (gemini-3.1-flash-lite, etc.). This wrapper calls the v1 endpoint directly
via google.genai.Client, making all models available.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

from google import genai
from google.genai import types as genai_types
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult


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
    contents = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            continue
        role = "user" if msg.type == "human" else "model"
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
        contents.append(genai_types.Content(role=role, parts=[genai_types.Part(text=text)]))
    return contents


def _build_config(system: str | None, temperature: float) -> genai_types.GenerateContentConfig:
    kwargs: dict[str, Any] = {"temperature": temperature}
    if system:
        kwargs["system_instruction"] = system
    return genai_types.GenerateContentConfig(**kwargs)


class GoogleGenAIChatModel(BaseChatModel):
    """ChatModel backed by google-genai v1 SDK — supports all AI Studio models."""

    model: str
    google_api_key: str
    temperature: float = 0.1

    @property
    def _llm_type(self) -> str:
        return "google-genai-v1"

    def _client(self) -> genai.Client:
        return _get_client(self.google_api_key)

    @staticmethod
    def _text(response: Any) -> str:
        """Extract text, handling safety blocks (response.text raises on SAFETY finish)."""
        try:
            return response.text or ""
        except Exception:
            return ""

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
            config=_build_config(_extract_system(messages), self.temperature),
        )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self._text(response)))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        contents = _to_contents(messages)
        config   = _build_config(_extract_system(messages), self.temperature)
        client   = self._client()

        # Run sync SDK call in thread pool — avoids event loop conflicts inside ADK
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=self.model,
            contents=contents,
            config=config,
        )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self._text(response)))])
