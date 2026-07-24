from functools import lru_cache

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from src.config.settings import settings


def get_llm(model_name: str | None = None, temperature: float = 0.1) -> BaseChatModel:
    """Return a configured chat model for the active provider.

    Each agent passes its own model name, e.g. get_llm(settings.flight_agent_model).
    """
    model = model_name or settings.coordinator_model

    if settings.llm_provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=settings.google_api_key,
            temperature=temperature,
        )

    if settings.llm_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model,
            api_key=settings.anthropic_api_key,
            temperature=temperature,
        )

    if settings.llm_provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            api_key=settings.openai_api_key,
            temperature=temperature,
        )

    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider!r}")


@lru_cache(maxsize=1)
def get_embeddings() -> Embeddings:
    """Return a cached embeddings instance (built once at startup).

    - gemini provider  → gemini-embedding-2-preview (same google_api_key)
    - anthropic/openai → OpenAI text-embedding-3-small (openai_api_key required)
    """
    if settings.llm_provider == "gemini":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        return GoogleGenerativeAIEmbeddings(
            model=settings.embedding_model,
            google_api_key=settings.google_api_key,
        )

    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(
        model="text-embedding-3-small",
        api_key=settings.openai_api_key,
    )
