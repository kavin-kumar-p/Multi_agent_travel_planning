"""Build or load per-collection FAISS indexes from sample JSON data."""

import json
from pathlib import Path

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.config.settings import settings

DATA_DIR = Path(__file__).parent.parent.parent / "data"

# Maps collection name → JSON filename (under DATA_DIR)
COLLECTIONS: dict[str, str] = {
    "travel_policies": "policies.json",
    "destinations": "destinations.json",
    "hotel_details": "hotels.json",
    "preferences": "preferences.json",
    "previous_itineraries": "itineraries.json",
}


def _load_documents(json_file: str, collection: str) -> list[Document]:
    path = DATA_DIR / json_file
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        records = [records]

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    docs: list[Document] = []
    for i, record in enumerate(records):
        text = json.dumps(record, ensure_ascii=False)
        for chunk in splitter.split_text(text):
            docs.append(Document(page_content=chunk, metadata={"collection": collection, "source_idx": i}))
    return docs


def _index_path(collection: str) -> Path:
    return Path(settings.faiss_index_dir) / collection


async def load_or_build(embeddings: Embeddings) -> dict[str, FAISS]:
    """Return {collection_name: FAISS}.  Builds missing indexes, loads existing ones."""
    stores: dict[str, FAISS] = {}

    for collection, json_file in COLLECTIONS.items():
        idx_path = _index_path(collection)

        if idx_path.exists():
            stores[collection] = FAISS.load_local(
                str(idx_path),
                embeddings,
                allow_dangerous_deserialization=True,
            )
        else:
            docs = _load_documents(json_file, collection)
            store = await FAISS.afrom_documents(docs, embeddings)
            idx_path.mkdir(parents=True, exist_ok=True)
            store.save_local(str(idx_path))
            stores[collection] = store

    return stores
