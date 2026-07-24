from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM Provider ──────────────────────────────────────────────────────────
    llm_provider: Literal["anthropic", "openai", "gemini"] = "gemini"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""

    # ── Model selection per agent ──────────────────────────────────────────────
    coordinator_model: str = "gemini-3.1-flash-lite"
    flight_agent_model: str = "gemini-3.1-flash-lite"
    attractions_agent_model: str = "gemini-3.1-flash-lite"
    hotel_agent_model: str = "gemini-3.1-flash-lite"
    transport_agent_model: str = "gemini-3.1-flash-lite"

    # ── Embeddings ────────────────────────────────────────────────────────────
    # gemini provider → Google text-embedding-004 (no extra key needed)
    # anthropic/openai → OpenAI text-embedding-3-small (requires openai_api_key)
    embedding_model: str = "gemini-embedding-2-preview"

    # ── RAG ───────────────────────────────────────────────────────────────────
    faiss_index_dir: str = "data/faiss"
    rag_top_k: int = 3
    chunk_size: int = 512
    chunk_overlap: int = 64

    # ── Budget ────────────────────────────────────────────────────────────────
    default_total_budget: float = 3000.0

    # ── ADK Session Store ─────────────────────────────────────────────────────
    # Uses aiosqlite for async SQLite; swap to postgresql+asyncpg://... for prod
    adk_session_db_url: str = "sqlite+aiosqlite:///data/sessions.db"

    # ── Rate limiting ─────────────────────────────────────────────────────────
    # Seconds each tool sleeps after the specialist finishes before returning to
    # the ADK coordinator. Keeps 9 total LLM calls within the 5 RPM free-tier.
    # Set to 0 if you have a paid quota with higher RPM.
    rpm_throttle_seconds: float = 4.0

    # ── Agent Retry ───────────────────────────────────────────────────────────
    agent_max_retries: int = 3


settings = Settings()
