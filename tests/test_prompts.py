"""Prompt loader unit tests."""
import pytest

from src.prompts import load_prompt


def test_load_prompt_returns_system_and_user_template():
    p = load_prompt("flight_agent")
    assert "system" in p
    assert "user_template" in p


def test_load_prompt_system_not_empty():
    p = load_prompt("coordinator")
    assert len(p["system"]) > 0


def test_load_prompt_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_prompt("nonexistent_agent")
