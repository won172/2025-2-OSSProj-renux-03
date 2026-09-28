"""ChromaDB 영구 클라이언트를 가볍게 감싼 래퍼입니다."""
from __future__ import annotations

from functools import lru_cache
import threading
from typing import Callable, Iterable, Mapping, TypeVar

import chromadb

from src.config import CHROMA_DIR
from src.vectorstore.collection_pointer import resolve_collection_name


_CLIENT_CREATE_LOCK = threading.Lock()
_client_instance = None


def get_client() -> chromadb.PersistentClient:
    """Create the process-wide Chroma client exactly once across threads."""
    global _client_instance
    if _client_instance is None:
        with _CLIENT_CREATE_LOCK:
            if _client_instance is None:
                _client_instance = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return _client_instance


# 신규 컬렉션은 cosine 거리로 생성한다(미지정 시 Chroma 기본값은 L2 —
# 하이브리드 검색의 1-distance 유사도 변환이 cosine을 전제하므로 필수).
_COLLECTION_METADATA = {"hnsw:space": "cosine"}
_COLLECTION_CREATE_LOCK = threading.RLock()
_COLLECTION_RESULT = TypeVar("_COLLECTION_RESULT")


@lru_cache(maxsize=64)
def _get_physical_collection(name: str, create_if_missing: bool = False):
    client = get_client()
    try:
        return client.get_collection(name=name)
    except chromadb.errors.NotFoundError:
        if not create_if_missing:
            raise
        with _COLLECTION_CREATE_LOCK:
            try:
                return client.get_collection(name=name)
            except chromadb.errors.NotFoundError:
                return client.create_collection(name=name, metadata=_COLLECTION_METADATA)


def get_collection(name: str, create_if_missing: bool = False):
    """Resolve a logical collection pointer; only ingestion may create it.

    The cache is keyed by the resolved physical name.  An atomic pointer flip
    therefore sends the next request to the new build without invalidating
    in-flight requests that still hold the previous collection object.
    """
    physical_name = resolve_collection_name(name)
    return _get_physical_collection(physical_name, create_if_missing)


def _clear_collection_cache() -> None:
    _get_physical_collection.cache_clear()


# Preserve the cache_clear hook used by existing readiness tests and stale-id
# recovery code while keeping pointer resolution outside the LRU key.
get_collection.cache_clear = _clear_collection_cache  # type: ignore[attr-defined]


def _with_live_collection(
    name: str,
    operation: Callable[[object], _COLLECTION_RESULT],
    *,
    create_if_missing: bool = False,
) -> _COLLECTION_RESULT:
    """Run a collection operation and recover once from a stale handle.

    Chroma collection objects contain an internal collection id.  If another
    maintenance process atomically rebuilds a collection, a long-running API
    process can retain the old id even though the collection name exists again.
    Re-resolving the name once is safe for reads and keeps readiness/evaluation
    probes from failing until the whole service is restarted.
    """
    collection = get_collection(name, create_if_missing)
    try:
        return operation(collection)
    except chromadb.errors.NotFoundError:
        with _COLLECTION_CREATE_LOCK:
            get_collection.cache_clear()
            collection = get_collection(name, create_if_missing)
        return operation(collection)


def add_items(
    name: str,
    ids: Iterable[str],
    documents: Iterable[str],
    metadatas: Iterable[Mapping[str, object]],
    embeddings,
) -> None:
    """지정된 Chroma 컬렉션에 항목을 추가합니다."""
    ids_list = list(ids)
    documents_list = list(documents)
    metadatas_list = list(metadatas)
    embeddings_list = list(embeddings)
    
    batch_size = 5000
    total = len(ids_list)
    
    for i in range(0, total, batch_size):
        _with_live_collection(
            name,
            lambda collection, start=i: collection.add(
                ids=ids_list[start : start + batch_size],
                documents=documents_list[start : start + batch_size],
                metadatas=metadatas_list[start : start + batch_size],
                embeddings=embeddings_list[start : start + batch_size],
            ),
            create_if_missing=True,
        )

def upsert_items(
    name: str,
    ids: Iterable[str],
    documents: Iterable[str],
    metadatas: Iterable[Mapping[str, object]],
    embeddings,
) -> None:
    """지정된 Chroma 컬렉션에 항목을 추가하거나 업데이트합니다 (ID 기준)."""
    ids_list = list(ids)
    documents_list = list(documents)
    metadatas_list = list(metadatas)
    embeddings_list = list(embeddings)
    
    batch_size = 5000
    total = len(ids_list)
    
    for i in range(0, total, batch_size):
        _with_live_collection(
            name,
            lambda collection, start=i: collection.upsert(
                ids=ids_list[start : start + batch_size],
                documents=documents_list[start : start + batch_size],
                metadatas=metadatas_list[start : start + batch_size],
                embeddings=embeddings_list[start : start + batch_size],
            ),
            create_if_missing=True,
        )


def update_item_metadatas(
    name: str,
    ids: Iterable[str],
    metadatas: Iterable[Mapping[str, object]],
) -> None:
    """Update existing Chroma metadata without recomputing embeddings."""
    ids_list = list(ids)
    metadatas_list = list(metadatas)
    if len(ids_list) != len(metadatas_list):
        raise ValueError("ids and metadatas must have the same length")
    batch_size = 5000
    for i in range(0, len(ids_list), batch_size):
        _with_live_collection(
            name,
            lambda collection, start=i: collection.update(
                ids=ids_list[start : start + batch_size],
                metadatas=metadatas_list[start : start + batch_size],
            ),
        )

def delete_items(name: str, ids: Iterable[str]) -> None:
    """지정된 Chroma 컬렉션에서 항목을 삭제합니다."""
    ids_list = list(ids)
    _with_live_collection(name, lambda collection: collection.delete(ids=ids_list))


def get_all_ids(name: str) -> list[str]:
    """지정된 Chroma 컬렉션에 저장된 모든 문서의 ID를 반환합니다."""
    # include=[]를 전달하여 메타데이터나 문서를 가져오지 않고 ID만 빠르게 조회
    # limit=None을 명시해야 모든 ID를 가져옴 (기본값이 있을 수 있음)
    result = _with_live_collection(
        name,
        lambda collection: collection.get(include=[], limit=None),
    )
    return result.get("ids", [])


def get_items(name: str, ids: Iterable[str], *, include_embeddings: bool = False) -> dict:
    """Read requested entries in bounded batches without scanning the collection."""
    ids_list = list(ids)
    result = {"ids": [], "documents": [], "metadatas": []}
    if include_embeddings:
        result["embeddings"] = []
    for start in range(0, len(ids_list), 500):
        batch = _with_live_collection(
            name,
            lambda collection, offset=start: collection.get(
                ids=ids_list[offset : offset + 500],
                include=["documents", "metadatas", "embeddings"] if include_embeddings else ["documents", "metadatas"],
            ),
        )
        for field in result:
            values = batch.get(field)
            if values is None:
                raise RuntimeError(f"Chroma did not return {field} for embedding reuse")
            result[field].extend(values)
    return result


def get_existing_ids(name: str, ids: Iterable[str]) -> set[str]:
    """주어진 ID 목록 중 컬렉션에 이미 존재하는 ID들만 반환합니다."""
    if not ids:
        return set()
    
    # ids 필터로 조회하여 존재하는지 확인
    ids_list = list(ids)
    # ChromaDB get은 존재하지 않는 ID에 대해 에러를 내지 않고 존재하는 것만 반환함 (버전에 따라 다를 수 있으니 확인 필요)
    # 최신 버전에서는 get(ids=[...]) 시 존재하는 것만 반환됨.
    result = _with_live_collection(
        name,
        lambda collection: collection.get(ids=ids_list, include=[]),
    )
    return set(result.get("ids", []))


def query_items(name: str, *, collection=None, **kwargs):
    """Query a collection, retrying once if its cached id was rebuilt."""
    if collection is not None:
        try:
            return collection.query(**kwargs)
        except chromadb.errors.NotFoundError:
            with _COLLECTION_CREATE_LOCK:
                get_collection.cache_clear()
    return _with_live_collection(name, lambda collection: collection.query(**kwargs))


def count_items(name: str) -> int:
    """지정된 Chroma 컬렉션의 문서 수를 반환합니다."""
    return _with_live_collection(name, lambda collection: collection.count())


def reset_collection(name: str) -> None:
    """컬렉션을 삭제한 뒤 다시 만들어 재빌드에 활용합니다."""
    physical_name = resolve_collection_name(name)
    client = get_client()
    try:
        client.delete_collection(physical_name)
    except chromadb.errors.NotFoundError:
        pass
    client.create_collection(name=physical_name, metadata=_COLLECTION_METADATA)
    get_collection.cache_clear()


__all__ = [
    "get_client", "get_collection", "add_items", "upsert_items", "query_items",
    "update_item_metadatas", "delete_items", "get_all_ids", "get_items", "count_items", "reset_collection",
]
