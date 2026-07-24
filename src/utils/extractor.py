"""NLP extraction utilities — parse trip details from natural language."""
from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.llm.factory import get_llm
from src.utils.text import extract_text

_EXTRACT_PROMPT = """You are a travel planning assistant. Extract trip details from the user's message.

Return ONLY a JSON object (use null for anything not mentioned):
{
  "origin": "departure city or airport code",
  "destination": "destination city or country",
  "start_date": "YYYY-MM-DD or null",
  "end_date": "YYYY-MM-DD or null",
  "total_budget": number in USD or null,
  "interests": ["list", "of", "interests"] or [],
  "flight_booked": true ONLY if the user says their flights are ALREADY done/sorted/paid by them,
  "hotel_booked":  true ONLY if the user says their hotel is ALREADY booked/sorted/paid by them,
  "transport_booked": true ONLY if the user says local transport is ALREADY arranged by them
}

Rules:
- "next month" / "in December" → infer year 2026 if month has passed.
- Duration like "7 days" + start date → compute end_date.
- Month/season only → first day of month as start_date, +7 days as end_date.
- _booked fields: ONLY true when the user is saying something is already done (past tense / "already" / "sorted" / "I've booked").
  "Book flights for me" / "I need a hotel" / "arrange transport" → false (user is ASKING you to do it, not saying it's done).
  "I've booked my flights" / "hotel already sorted" / "flights are done" → true.
- Return valid JSON only — no explanation, no markdown fences."""

_MISSING_PROMPT = """The user wants to plan a trip but some details are missing.
Current info: {current}
Missing fields: {missing}

Ask ONE friendly follow-up question to get the missing info. Be conversational."""

_REQUIRED = ["origin", "destination", "start_date", "end_date", "total_budget"]


def ask_llm(system: str, user: str) -> str:
    llm = get_llm()
    response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    return extract_text(response.content).strip()


def extract_fields(message: str) -> dict:
    """Parse a natural-language trip request into structured fields."""
    raw = ask_llm(_EXTRACT_PROMPT, message)
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        return json.loads(raw[start:end]) if start != -1 else {}
    except Exception:
        return {}


def missing_fields(info: dict) -> list[str]:
    """Return required fields that haven't been filled yet."""
    return [f for f in _REQUIRED if not info.get(f)]


def follow_up(info: dict, missing: list[str]) -> str:
    """Generate a friendly single follow-up question for the missing fields."""
    current = {k: v for k, v in info.items() if v}
    return ask_llm(
        _MISSING_PROMPT.format(
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
