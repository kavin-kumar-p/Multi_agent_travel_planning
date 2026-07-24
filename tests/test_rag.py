"""RAG retriever unit tests."""
from unittest.mock import MagicMock

import pytest

from src.rag.retriever import retrieve


def test_retrieve_unknown_collection_raises():
    stores = {"travel_policies": MagicMock()}
    with pytest.raises(KeyError, match="Unknown RAG collection"):
        retrieve(stores, "nonexistent", "query")


def test_retrieve_returns_page_content():
    mock_doc = MagicMock()
    mock_doc.page_content = "economy class policy text"
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = [mock_doc]

    result = retrieve({"travel_policies": mock_store}, "travel_policies", "flight policy", k=1)

    assert result == ["economy class policy text"]
    mock_store.similarity_search.assert_called_once_with("flight policy", k=1)


def test_retrieve_uses_settings_top_k_by_default():
    mock_store = MagicMock()
    mock_store.similarity_search.return_value = []

    retrieve({"travel_policies": mock_store}, "travel_policies", "query")

    args, kwargs = mock_store.similarity_search.call_args
    assert "k" in kwargs
