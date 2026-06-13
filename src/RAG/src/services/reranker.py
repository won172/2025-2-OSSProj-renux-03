"""Cross-encoder 기반 정밀 재정렬(rerank) 서비스입니다.

하이브리드(벡터+TF-IDF) 검색은 질의와 문서를 따로 인코딩하므로 미묘한 관련성을
놓칠 수 있다. Cross-encoder는 (질의, 문서) 쌍을 함께 인코딩해 훨씬 정확한
관련도 점수를 내므로, 상위 후보 N개만 재정렬하는 용도로 사용한다.

RERANKER_ENABLED=1 일 때만 모델이 로드된다(기본 비활성 — 모델 ~2GB, CPU 추론 지연).
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import List, Sequence

from src.config import (
    EMBED_DEVICE,
    MODEL_TRUST_REMOTE_CODE,
    RERANKER_ENABLED,
    RERANKER_MODEL,
    RERANKER_MODEL_REVISION,
)

logger = logging.getLogger(__name__)


def is_reranker_enabled() -> bool:
    return RERANKER_ENABLED


@lru_cache(maxsize=1)
def get_reranker():
    """CrossEncoder 인스턴스를 lazy 로드합니다(활성화 시 최초 1회)."""
    from sentence_transformers import CrossEncoder

    logger.info("Loading cross-encoder reranker: %s", RERANKER_MODEL)
    # trust_remote_code는 기본 비활성(공급망 위험 차단). 커스텀 코드 모델만
    # 고정 리비전(RERANKER_MODEL_REVISION)과 함께 MODEL_TRUST_REMOTE_CODE=1로 켠다.
    return CrossEncoder(
        RERANKER_MODEL,
        device=EMBED_DEVICE,
        trust_remote_code=MODEL_TRUST_REMOTE_CODE,
        revision=RERANKER_MODEL_REVISION,
    )


def rerank_scores(query: str, texts: Sequence[str]) -> List[float] | None:
    """(질의, 문서) 쌍의 관련도 점수 목록을 반환합니다. 실패 시 None(호출측은 무시).

    반환 점수는 모델 raw 출력(logit)이며, 호출측에서 후보 내 min-max 정규화해 사용한다.
    """
    if not texts:
        return []
    try:
        model = get_reranker()
        pairs = [(query, text) for text in texts]
        scores = model.predict(pairs, show_progress_bar=False)
        return [float(s) for s in scores]
    except Exception as exc:  # noqa: BLE001 — 리랭커 실패가 검색 자체를 막으면 안 됨
        logger.warning("Cross-encoder rerank failed (falling back to hybrid order): %s", exc)
        return None


__all__ = ["is_reranker_enabled", "get_reranker", "rerank_scores"]
