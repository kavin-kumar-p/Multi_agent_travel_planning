"""Load agent prompt files from the prompts/ directory.

Prompt files are Markdown with H1 section headers:

    # System
    <system prompt text>

    # User Template
    <user message template with {placeholder} variables>

Usage:
    p = load_prompt("flight_agent")
    p["system"]        # system prompt string
    p["user_template"] # user message template string
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


@lru_cache(maxsize=None)
def load_prompt(name: str) -> dict[str, str]:
    """Parse a prompt Markdown file into a dict of section_name → text."""
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")

    sections: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            if current_key is not None:
                sections[current_key] = "\n".join(current_lines).strip()
            current_key = line[2:].strip().lower().replace(" ", "_")
            current_lines = []
        else:
            current_lines.append(line)

    if current_key is not None:
        sections[current_key] = "\n".join(current_lines).strip()

    return sections
