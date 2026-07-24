"""Shared data loader for all tools."""
from __future__ import annotations

import json
from pathlib import Path

_DATA_DIR = Path(__file__).parent.parent.parent / "data"


def load_json(filename: str) -> list[dict]:
    return json.loads((_DATA_DIR / filename).read_text(encoding="utf-8"))
