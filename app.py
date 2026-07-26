"""Streamlit entry point — Travel Planner AI."""
from __future__ import annotations

import asyncio
import atexit
import logging
import os
import sys
import threading

# Disable CrewAI/OpenTelemetry telemetry before any crewai imports
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "1")

import streamlit as st


def _configure_logging() -> None:
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)

    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(handler)
    root.setLevel(logging.INFO)

    # Silence noisy third-party loggers
    for noisy in ("httpx", "httpcore", "urllib3", "uvicorn.access", "watchdog"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_configure_logging()

from src.ui.state import init_state
from src.ui.components import render_traces, render_session
from src.ui.chat_stages import render_chat

st.set_page_config(
    page_title="Travel Planner AI",
    page_icon=":material/travel_explore:",
    layout="wide",
    initial_sidebar_state="collapsed",
)


@st.cache_resource(show_spinner="Loading knowledge base…")
def _get_rag_stores():
    """Build/load FAISS indexes once per process. Must run before agent servers start."""
    from src.llm.factory import get_embeddings
    from src.rag.bootstrap import load_or_build

    result: dict = {}
    error:  dict = {}

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result["stores"] = loop.run_until_complete(load_or_build(get_embeddings()))
        except Exception as exc:
            error["err"] = exc
        finally:
            loop.close()

    t = threading.Thread(target=_run)
    t.start()
    t.join()

    if "err" in error:
        raise error["err"]
    return result["stores"]


@st.cache_resource(show_spinner="Starting A2A agent servers…")
def _get_server_manager():
    """
    Start the four A2A agent HTTP servers (ports 8001–8004) once per process.
    FAISS indexes must already be on disk (call _get_rag_stores first).
    Each server loads its own RAG stores on uvicorn startup.
    """
    from src.a2a.launch import AgentServerManager

    manager = AgentServerManager()
    manager.start_all(startup_timeout=120.0)
    atexit.register(manager.stop_all)
    return manager


def main() -> None:
    init_state()

    # Load RAG stores first so FAISS indexes exist on disk before agent servers start
    rag_stores = _get_rag_stores()

    # Start A2A agent servers (reused across Streamlit reruns via cache_resource)
    _get_server_manager()

    st.title("Travel Planner AI")
    st.caption("Powered by A2A Protocol · LangGraph · CrewAI")

    tab_chat, tab_traces, tab_session = st.tabs(["Chat", "Agent Traces", "Session"])

    with tab_chat:
        render_chat(rag_stores)

    with tab_traces:
        render_traces()

    with tab_session:
        render_session()


if __name__ == "__main__":
    main()
