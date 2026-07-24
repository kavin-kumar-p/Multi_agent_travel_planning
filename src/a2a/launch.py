"""AgentServerManager — start and stop A2A agent servers as subprocesses.

Each agent runs as an independent uvicorn process on its own port.
The manager polls /health until all servers are ready before returning.
Already-running servers (from a previous Streamlit session) are reused.
"""
from __future__ import annotations

import atexit
import logging
import subprocess
import sys
import time
from pathlib import Path

import httpx

from src.config.constants import (
    AGENT_HOST,
    ATTRACTIONS_PORT,
    FLIGHT_PORT,
    HEALTH_TIMEOUT,
    HOTEL_PORT,
    STARTUP_TIMEOUT,
    TRANSPORT_PORT,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Agent name → (uvicorn module path, port)
_AGENTS: dict[str, tuple[str, int]] = {
    "flights":     ("src.agents.flight",      FLIGHT_PORT),
    "attractions": ("src.agents.attractions", ATTRACTIONS_PORT),
    "hotel":       ("src.agents.hotel",       HOTEL_PORT),
    "transport":   ("src.agents.transport",   TRANSPORT_PORT),
}


def agent_url(name: str) -> str:
    _, port = _AGENTS[name]
    return f"http://{AGENT_HOST}:{port}"


def all_agent_urls() -> dict[str, str]:
    return {name: agent_url(name) for name in _AGENTS}


class AgentServerManager:
    """Lifecycle manager for all four A2A agent HTTP servers."""

    def __init__(self) -> None:
        self._procs: dict[str, subprocess.Popen] = {}

    def start_all(self, startup_timeout: float = STARTUP_TIMEOUT) -> None:
        """
        Start any agent servers not already running, then wait until all are healthy.
        Servers already responding on their port are reused without restart.
        """
        for name, (module, port) in _AGENTS.items():
            if _is_healthy(port):
                logger.info("A2A agent '%s' already up on :%d — reusing", name, port)
                continue

            logger.info("Starting A2A agent '%s' on :%d …", name, port)
            proc = subprocess.Popen(
                [
                    sys.executable, "-m", "uvicorn",
                    f"{module}:app",
                    "--host", AGENT_HOST,
                    "--port", str(port),
                    "--log-level", "warning",
                ],
                cwd=str(_PROJECT_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._procs[name] = proc

        self._wait_healthy(startup_timeout)

    def stop_all(self) -> None:
        """Terminate all agent server subprocesses started by this manager."""
        for name, proc in self._procs.items():
            logger.info("Stopping A2A agent '%s' (pid=%d)", name, proc.pid)
            proc.terminate()
        for proc in self._procs.values():
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._procs.clear()

    def _wait_healthy(self, timeout: float) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            ready = [n for n, (_, p) in _AGENTS.items() if _is_healthy(p)]
            if len(ready) == len(_AGENTS):
                logger.info("All %d A2A agent servers healthy", len(_AGENTS))
                return
            time.sleep(1.0)

        not_ready = [n for n, (_, p) in _AGENTS.items() if not _is_healthy(p)]
        raise TimeoutError(
            f"A2A agent servers did not become healthy in {timeout}s: {not_ready}"
        )


def _is_healthy(port: int) -> bool:
    try:
        httpx.get(f"http://{AGENT_HOST}:{port}/health", timeout=HEALTH_TIMEOUT)
        return True
    except Exception:
        return False
