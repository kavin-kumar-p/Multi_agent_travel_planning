"""NLP extraction utilities — parse trip details from natural language."""
from __future__ import annotations

import json
from datetime import date

from langchain_core.messages import HumanMessage, SystemMessage

from src.llm.factory import get_llm
from src.prompts import load_prompt
from src.utils.text import extract_text


def _today() -> str:
    return date.today().isoformat()

_REQUIRED = ["origin", "destination", "start_date", "end_date", "total_budget"]


def ask_llm(system: str, user: str) -> str:
    llm = get_llm()
    response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    return extract_text(response.content).strip()


def extract_fields(message: str) -> dict:
    """Parse a natural-language trip request into structured fields."""
    p = load_prompt("extractor")
    raw = ask_llm(p["extract"].replace("{today}", _today()), message)
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        return json.loads(raw[start:end]) if start != -1 else {}
    except Exception:
        return {}


def is_on_topic(message: str) -> bool:
    """Return True if the message contains any travel-related information."""
    p = load_prompt("extractor")
    prompt = p["on_topic"].format(message=message)
    result = ask_llm(prompt, "").strip().lower()
    return result.startswith("yes")


def missing_fields(info: dict, confirmed_booked: dict | None = None) -> list[str]:
    """Return required fields that haven't been filled yet.

    origin is optional when flights are already confirmed booked — the
    Flight Agent won't run so we don't need to know the departure city.
    """
    required = _REQUIRED
    if confirmed_booked and confirmed_booked.get("flights"):
        required = [f for f in _REQUIRED if f != "origin"]
    return [f for f in required if not info.get(f)]


def follow_up(info: dict, missing: list[str]) -> str:
    """Generate a friendly single follow-up question for the missing fields."""
    p = load_prompt("extractor")
    current = {k: v for k, v in info.items() if v}
    return ask_llm(
        p["missing"].format(
            current=json.dumps(current, ensure_ascii=False),
            missing=", ".join(missing),
        ),
        "Generate a follow-up question.",
    )


def merge_info(base: dict, update: dict) -> dict:
    """Merge extracted fields, keeping existing values when update is empty/null."""
    merged = dict(base)
    for k, v in update.items():
        if v is not None and v != [] and v != "":
            merged[k] = v
    return merged
