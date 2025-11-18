"""노트북 설정에 맞춘 문장 임베딩 헬퍼입니다."""
from __future__ import annotations

from functools import lru_cache
from typing import Iterable, List

import numpy as np
from sentence_transformers import SentenceTransformer

from src.config import EMBED_BATCH_SIZE, EMBED_DEVICE, EMBED_MODEL_NAME


@lru_cache(maxsize=1)
def get_embedder() -> SentenceTransformer:
    """캐시에 담긴 SentenceTransformer 인스턴스를 반환합니다."""
    model = SentenceTransformer(
        EMBED_MODEL_NAME,
        trust_remote_code=True,
        device=EMBED_DEVICE,
    )
    return model


def encode_texts(texts: Iterable[str], normalize: bool = True) -> np.ndarray:
    """프로젝트 기본 설정으로 텍스트 목록을 밀집 벡터로 변환합니다."""
    embedder = get_embedder()
    vectors = embedder.encode(
        list(texts),
        batch_size=EMBED_BATCH_SIZE,
        convert_to_numpy=True,
        normalize_embeddings=normalize,
        show_progress_bar=False,
    )
    return vectors


__all__ = ["get_embedder", "encode_texts"]
