"""LLM factory unit tests."""
from unittest.mock import patch

import pytest

from src.config.settings import settings


def test_get_llm_raises_on_unknown_provider():
    from src.llm.factory import get_llm

    with patch.object(settings, "llm_provider", "unknown"):
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            get_llm()


def test_get_llm_uses_coordinator_model_as_default():
    from src.llm.factory import get_llm
    from langchain_anthropic import ChatAnthropic

    with patch.object(settings, "llm_provider", "anthropic"), \
         patch.object(settings, "anthropic_api_key", "sk-ant-test"):
        llm = get_llm()
        assert isinstance(llm, ChatAnthropic)
