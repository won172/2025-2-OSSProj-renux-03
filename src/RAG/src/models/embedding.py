"""노트북 설정에 맞춘 문장 임베딩 헬퍼입니다."""
from __future__ import annotations

from functools import lru_cache
from typing import Iterable, List

import numpy as np
from sentence_transformers import SentenceTransformer

from src.config import (
    EMBED_BATCH_SIZE,
    EMBED_DEVICE,
    EMBED_MODEL_NAME,
    EMBED_MODEL_REVISION,
    EMBED_PASSAGE_PREFIX,
    EMBED_QUERY_PREFIX,
    MODEL_TRUST_REMOTE_CODE,
)


@lru_cache(maxsize=1)
def get_embedder() -> SentenceTransformer:
    """캐시에 담긴 SentenceTransformer 인스턴스를 반환합니다."""
    import os
    from pathlib import Path
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    cache_base = Path(__file__).resolve().parents[2] / "huggingface_cache"
    if cache_base.exists():
        os.environ["HF_HOME"] = str(cache_base)

    target_model_name = EMBED_MODEL_NAME
    snapshots_dir = cache_base / "hub" / f"models--{EMBED_MODEL_NAME.replace('/', '--')}" / "snapshots"
    if snapshots_dir.exists():
        snapshots = [p for p in snapshots_dir.iterdir() if p.is_dir()]
        if snapshots:
            target_model_name = str(snapshots[0])

    model = SentenceTransformer(
        target_model_name,
        trust_remote_code=MODEL_TRUST_REMOTE_CODE,
        revision=EMBED_MODEL_REVISION if target_model_name == EMBED_MODEL_NAME else None,
        device=EMBED_DEVICE,
    )
    return model


def _apply_prefix(texts: Iterable[str], prefix: str) -> List[str]:
    items = [t if isinstance(t, str) else str(t) for t in texts]
    if not prefix:
        return items
    return [f"{prefix}{t}" for t in items]


def encode_texts(texts: Iterable[str], normalize: bool = True) -> np.ndarray:
    """문서(passage) 텍스트 목록을 밀집 벡터로 변환합니다.

    E5 계열처럼 문서 프리픽스를 요구하는 모델은 EMBED_PASSAGE_PREFIX로 지원.
    KURE-v1/BGE-M3(기본 모델)는 프리픽스가 비어 있어 기존과 동일하게 동작한다.
    """
    embedder = get_embedder()
    vectors = embedder.encode(
        _apply_prefix(texts, EMBED_PASSAGE_PREFIX),
        batch_size=EMBED_BATCH_SIZE,
        convert_to_numpy=True,
        normalize_embeddings=normalize,
        show_progress_bar=False,
    )
    return vectors


@lru_cache(maxsize=256)
def _encode_single_query(text: str, normalize: bool) -> tuple[float, ...]:
    """Cache one query vector so six routerless corpus searches encode once."""
    embedder = get_embedder()
    vector = embedder.encode(
        [text],
        batch_size=EMBED_BATCH_SIZE,
        convert_to_numpy=True,
        normalize_embeddings=normalize,
        show_progress_bar=False,
    )[0]
    return tuple(float(value) for value in vector)


def encode_queries(texts: Iterable[str], normalize: bool = True) -> np.ndarray:
    """검색 질의 텍스트 목록을 밀집 벡터로 변환합니다(질의 프리픽스 적용)."""
    prepared = _apply_prefix(texts, EMBED_QUERY_PREFIX)
    if not prepared:
        return np.empty((0, 0), dtype=np.float32)
    return np.asarray(
        [_encode_single_query(text, normalize) for text in prepared],
        dtype=np.float32,
    )


def clear_query_embedding_cache() -> None:
    """Clear process-local query vectors after an embedder configuration swap."""
    _encode_single_query.cache_clear()


__all__ = ["get_embedder", "encode_texts", "encode_queries", "clear_query_embedding_cache"]
