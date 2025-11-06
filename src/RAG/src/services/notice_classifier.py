"""Helpers for notice-category classification and reranking."""
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
    """Return the predicted notice category for the given query."""
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
    """Reorder notice hits so that rows matching the predicted category come first."""
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
    """Warm up the classifier at application startup."""
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
