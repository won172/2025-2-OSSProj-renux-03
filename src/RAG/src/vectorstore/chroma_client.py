"""ChromaDB 영구 클라이언트를 가볍게 감싼 래퍼입니다."""
from __future__ import annotations

from functools import lru_cache
from typing import Iterable, Mapping

import chromadb

from src.config import CHROMA_DIR


@lru_cache(maxsize=1)
def get_client() -> chromadb.PersistentClient:
    """ChromaDB 영구 클라이언트를 생성하거나 재사용합니다."""
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


def get_collection(name: str):
    """기존 컬렉션을 반환하거나 필요하면 새로 만듭니다."""
    client = get_client()
    return client.get_or_create_collection(name=name)


def add_items(
    name: str,
    ids: Iterable[str],
    documents: Iterable[str],
    metadatas: Iterable[Mapping[str, object]],
    embeddings,
) -> None:
    """지정된 Chroma 컬렉션에 항목을 추가합니다."""
    collection = get_collection(name)
    collection.add(
        ids=list(ids),
        documents=list(documents),
        metadatas=list(metadatas),
        embeddings=list(embeddings),
    )

def upsert_items(
    name: str,
    ids: Iterable[str],
    documents: Iterable[str],
    metadatas: Iterable[Mapping[str, object]],
    embeddings,
) -> None:
    """지정된 Chroma 컬렉션에 항목을 추가하거나 업데이트합니다 (ID 기준)."""
    collection = get_collection(name)
    collection.upsert(
        ids=list(ids),
        documents=list(documents),
        metadatas=list(metadatas),
        embeddings=list(embeddings),
    )

def delete_items(name: str, ids: Iterable[str]) -> None:
    """지정된 Chroma 컬렉션에서 항목을 삭제합니다."""
    collection = get_collection(name)
    collection.delete(ids=list(ids))


def get_all_ids(name: str) -> list[str]:
    """지정된 Chroma 컬렉션에 저장된 모든 문서의 ID를 반환합니다."""
    collection = get_collection(name)
    # include=[]를 전달하여 메타데이터나 문서를 가져오지 않고 ID만 빠르게 조회
    result = collection.get(include=[]) 
    return result.get("ids", [])


def reset_collection(name: str) -> None:
    """컬렉션을 삭제한 뒤 다시 만들어 재빌드에 활용합니다."""
    client = get_client()
    try:
        client.delete_collection(name)
    except chromadb.errors.NotFoundError:
        pass
    client.create_collection(name=name)


__all__ = ["get_client", "get_collection", "add_items", "upsert_items", "delete_items", "get_all_ids", "reset_collection"]
