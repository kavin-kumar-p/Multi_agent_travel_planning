"""Text helpers — LangChain response normalisation and JSON extraction."""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def extract_text(content: str | list) -> str:
    """Normalize LangChain response content to a plain string.

    Newer Gemini models return content as a list of typed blocks
    e.g. [{"type": "text", "text": "..."}] instead of a plain string.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def parse_agent_json(content: str | list, fallback: dict) -> dict:
    """Extract the first JSON object from an LLM response."""
    text = extract_text(content)
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
    except Exception:
        logger.warning("Could not parse JSON from agent response; using fallback")
    return fallback
