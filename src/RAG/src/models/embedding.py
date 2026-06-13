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
    """캐시에 담긴 SentenceTransformer 인스턴스를 반환합니다.

    trust_remote_code는 기본 비활성(MODEL_TRUST_REMOTE_CODE)으로, 신뢰할 수 없는
    HF 모델이 로드 중 임의 코드를 실행하는 공급망 위험을 차단한다. 커스텀 코드가
    필요한 모델은 고정 리비전(EMBED_MODEL_REVISION)과 함께 명시적으로 켜야 한다.
    """
    model = SentenceTransformer(
        EMBED_MODEL_NAME,
        trust_remote_code=MODEL_TRUST_REMOTE_CODE,
        revision=EMBED_MODEL_REVISION,
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


def encode_queries(texts: Iterable[str], normalize: bool = True) -> np.ndarray:
    """검색 질의 텍스트 목록을 밀집 벡터로 변환합니다(질의 프리픽스 적용)."""
    embedder = get_embedder()
    vectors = embedder.encode(
        _apply_prefix(texts, EMBED_QUERY_PREFIX),
        batch_size=EMBED_BATCH_SIZE,
        convert_to_numpy=True,
        normalize_embeddings=normalize,
        show_progress_bar=False,
    )
    return vectors


__all__ = ["get_embedder", "encode_texts", "encode_queries"]
