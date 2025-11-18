"""공지 분류와 재순위 지정을 돕는 헬퍼입니다."""
from __future__ import annotations

from threading import Lock
from typing import Optional

import pandas as pd

from src.models.category_classifier import (
    CLASSIFIER_PATH,
    load_classifier,
    predict_category,
)
from src.models.embedding import encode_texts

_classifier = None
_classifier_lock = Lock()


def _get_classifier():
    global _classifier
    with _classifier_lock:
        if _classifier is None and CLASSIFIER_PATH.exists():
            try:
                _classifier = load_classifier()
            except Exception as exc:  # noqa: BLE001
                print(f"⚠️ Failed to load notice classifier: {exc}")
                _classifier = None
        return _classifier


def classify_notice_query(query: str) -> Optional[str]:
    """주어진 질의에 대해 예측된 공지 카테고리를 반환합니다."""
    query = (query or "").strip()
    if not query:
        return None

    classifier = _get_classifier()
    if classifier is None:
        return None

    try:
        embedding = encode_texts([query])
        prediction = predict_category(classifier, embedding)[0]
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ Notice classifier inference failed: {exc}")
        return None
    return str(prediction)


def prioritize_notice_hits(hits: pd.DataFrame, category: str | None) -> pd.DataFrame:
    """예측된 카테고리와 일치하는 행이 먼저 오도록 공지 결과를 재정렬합니다."""
    if hits.empty or not category:
        return hits

    topics = hits.get("topics")
    if topics is None:
        return hits

    matched = topics.fillna("").astype(str).str.contains(category, case=False, na=False)
    if not matched.any():
        return hits

    prioritized = pd.concat([hits[matched], hits[~matched]], ignore_index=True)
    return prioritized


def bootstrap_notice_classifier() -> None:
    """애플리케이션이 시작될 때 분류기를 미리 불러옵니다."""
    classifier = _get_classifier()
    if classifier is None:
        if CLASSIFIER_PATH.exists():
            print("⚠️ Notice classifier exists but could not be loaded.")
        else:
            print("ℹ️ Notice classifier artifact not found; skipping category routing.")


__all__ = [
    "classify_notice_query",
    "prioritize_notice_hits",
    "bootstrap_notice_classifier",
]
