"""Helpers for selecting datasets with a trained classifier."""
from __future__ import annotations

from threading import Lock
from typing import List

import numpy as np

from src.config import ROUTER_RULES
from src.models.router_classifier import (
    ROUTER_MODEL_PATH,
    load_router_classifier,
    predict_router_proba,
)

_router_model = None
_model_lock = Lock()


def _keyword_route(query: str) -> List[str]:
    lowered = query.lower()
    hits = [
        dataset
        for dataset, keywords in ROUTER_RULES.items()
        if any(keyword.lower() in lowered for keyword in keywords)
    ]
    return hits or ["notices"]


def _get_router_model():
    global _router_model
    with _model_lock:
        if _router_model is None and ROUTER_MODEL_PATH.exists():
            try:
                _router_model = load_router_classifier()
            except Exception as exc:  # noqa: BLE001
                print(f"⚠️ Failed to load router classifier: {exc}")
                _router_model = None
        return _router_model


def route_query(
    query: str,
    *,
    min_probability: float = 0.25,
    max_candidates: int = 2,
) -> List[str]:
    """Route a user query to the most relevant datasets."""
    query = (query or "").strip()
    if not query:
        return ["notices"]

    model = _get_router_model()
    if model is None:
        return _keyword_route(query)

    try:
        probabilities = predict_router_proba(model, [query])[0]
        classes = model.named_steps["clf"].classes_
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ Router classifier inference failed: {exc}")
        return _keyword_route(query)

    order = np.argsort(probabilities)[::-1]
    if not len(order):
        return _keyword_route(query)

    selected: List[str] = []
    top_prob = probabilities[order[0]]
    for idx in order[:max_candidates]:
        label = classes[idx]
        prob = probabilities[idx]
        if not selected:
            selected.append(label)
            continue
        if prob >= min_probability or prob >= top_prob * 0.6:
            selected.append(label)

    if not selected:
        selected = _keyword_route(query)

    # Ensure results are unique and valid.
    seen: set[str] = set()
    unique = []
    for label in selected:
        if label in ROUTER_RULES and label not in seen:
            unique.append(label)
            seen.add(label)

    return unique or _keyword_route(query)


def bootstrap_router() -> None:
    """Warm up the router model early (used at application startup)."""
    model = _get_router_model()
    if model is None:
        if ROUTER_MODEL_PATH.exists():
            print("⚠️ Router classifier exists but could not be loaded. Falling back to keyword routing.")
        else:
            print("ℹ️ Router classifier not found; keyword routing will be used.")


__all__ = ["route_query", "bootstrap_router"]
