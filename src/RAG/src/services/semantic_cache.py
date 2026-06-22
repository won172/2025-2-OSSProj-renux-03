"""RAG 답변용 인프로세스 의미 캐시."""
from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np

from src.config import (
    RAG_SEMANTIC_CACHE_MAX,
    RAG_SEMANTIC_CACHE_THRESHOLD,
    RAG_SEMANTIC_CACHE_TTL_SECONDS,
)
from src.models.embedding import encode_queries

_LOCK = threading.Lock()
_STORE: dict[str, list[dict[str, Any]]] = {}
_HITS = 0
_MISSES = 0


def _normalized_query_vec(question: str) -> np.ndarray:
    vec = np.asarray(encode_queries([question])[0], dtype=np.float32)
    norm = np.linalg.norm(vec)
    if norm <= 0:
        raise ValueError("empty query vector")
    return vec / norm


def get(question: str, namespace: str) -> dict | None:
    """질문과 의미적으로 가장 가까운 캐시 답변을 반환한다."""
    global _HITS, _MISSES
    try:
        vec = _normalized_query_vec(question)
        now = time.time()
        with _LOCK:
            entries = [entry for entry in _STORE.get(namespace, []) if entry.get("expires_at", 0) > now]
            _STORE[namespace] = entries
            best_entry = None
            best_cosine = -1.0
            for entry in entries:
                cosine = float(np.dot(vec, entry["vec"]))
                if cosine > best_cosine:
                    best_cosine = cosine
                    best_entry = entry
            if best_entry is not None and best_cosine >= RAG_SEMANTIC_CACHE_THRESHOLD:
                _HITS += 1
                return best_entry["payload"]
            _MISSES += 1
            return None
    except Exception:  # noqa: BLE001
        try:
            with _LOCK:
                _MISSES += 1
        except Exception:
            pass
        return None


def put(question: str, namespace: str, payload: dict) -> None:
    """질문 임베딩과 답변 payload를 캐시에 저장한다."""
    try:
        vec = _normalized_query_vec(question)
        now = time.time()
        entry = {
            "vec": vec,
            "question": question,
            "payload": payload,
            "expires_at": now + RAG_SEMANTIC_CACHE_TTL_SECONDS,
        }
        with _LOCK:
            entries = [item for item in _STORE.get(namespace, []) if item.get("expires_at", 0) > now]
            entries.append(entry)
            max_entries = max(0, RAG_SEMANTIC_CACHE_MAX)
            if len(entries) > max_entries:
                entries = entries[-max_entries:] if max_entries else []
            _STORE[namespace] = entries
    except Exception:  # noqa: BLE001
        return


def stats() -> dict:
    """캐시 상태를 best-effort로 반환한다."""
    try:
        with _LOCK:
            return {
                "namespaces": len(_STORE),
                "size": sum(len(entries) for entries in _STORE.values()),
                "hits": _HITS,
                "misses": _MISSES,
            }
    except Exception:  # noqa: BLE001
        return {"namespaces": 0, "size": 0, "hits": 0, "misses": 0}
