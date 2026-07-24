"""Thin wrapper around FAISS so agents call one function."""

from langchain_community.vectorstores import FAISS

from src.config.settings import settings


def retrieve(
    stores: dict[str, FAISS],
    collection: str,
    query: str,
    k: int | None = None,
) -> list[str]:
    """Retrieve top-k text chunks from a named collection.

    Returns plain strings so the LLM can consume them directly.
    """
    top_k = k or settings.rag_top_k
    if collection not in stores:
        raise KeyError(f"Unknown RAG collection: {collection!r}. Available: {list(stores)}")

    docs = stores[collection].similarity_search(query, k=top_k)
    return [doc.page_content for doc in docs]
