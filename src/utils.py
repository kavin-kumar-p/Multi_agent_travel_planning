"""Shared helpers used across all agents."""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def parse_agent_json(text: str, fallback: dict) -> dict:
    """Extract the first JSON object from an LLM response string."""
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
    except Exception:
        logger.warning("Could not parse JSON from agent response; using fallback")
    return fallback
