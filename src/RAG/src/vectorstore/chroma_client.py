"""Lightweight wrapper around the ChromaDB persistent client."""
from __future__ import annotations

from functools import lru_cache
from typing import Iterable, Mapping

import chromadb

from src.config import CHROMA_DIR


@lru_cache(maxsize=1)
def get_client() -> chromadb.PersistentClient:
    """Create (or reuse) the persistent ChromaDB client."""
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


def get_collection(name: str):
    """Return an existing collection or create it on demand."""
    client = get_client()
    return client.get_or_create_collection(name=name)


def add_items(
    name: str,
    ids: Iterable[str],
    documents: Iterable[str],
    metadatas: Iterable[Mapping[str, object]],
    embeddings,
) -> None:
    """Push items into the given Chroma collection."""
    collection = get_collection(name)
    collection.add(
        ids=list(ids),
        documents=list(documents),
        metadatas=list(metadatas),
        embeddings=list(embeddings),
    )


def reset_collection(name: str) -> None:
    """Drop and recreate a collection (useful for rebuilds)."""
    client = get_client()
    try:
        client.delete_collection(name)
    except chromadb.errors.NotFoundError:
        pass
    client.create_collection(name=name)


__all__ = ["get_client", "get_collection", "add_items", "reset_collection"]
