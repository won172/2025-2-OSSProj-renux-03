"""Utilities for training and loading the dataset routing classifier."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence, Tuple

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from src.config import MODEL_DIR

ROUTER_MODEL_PATH = MODEL_DIR / "router_classifier.joblib"


def _build_pipeline(
    max_features: int | None = 6000,
    ngram_range: Tuple[int, int] = (1, 2),
) -> Pipeline:
    """Create a TF-IDF + Logistic Regression pipeline."""
    return Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    max_features=max_features,
                    ngram_range=ngram_range,
                    lowercase=True,
                    strip_accents="unicode",
                ),
            ),
            (
                "clf",
                LogisticRegression(
                    max_iter=2000,
                    multi_class="auto",
                ),
            ),
        ]
    )


def train_router_classifier(
    texts: Sequence[str],
    labels: Sequence[str],
    max_features: int | None = 6000,
    ngram_range: Tuple[int, int] = (1, 2),
) -> Pipeline:
    """Train the router classifier on the provided corpus."""
    pipeline = _build_pipeline(max_features=max_features, ngram_range=ngram_range)
    pipeline.fit(texts, labels)
    return pipeline


def evaluate_router_classifier(
    texts: Sequence[str],
    labels: Sequence[str],
    test_size: float = 0.2,
    random_state: int = 42,
    max_features: int | None = 6000,
    ngram_range: Tuple[int, int] = (1, 2),
) -> str:
    """Return a classification report using a hold-out split."""
    X_train, X_test, y_train, y_test = train_test_split(
        list(texts),
        list(labels),
        test_size=test_size,
        stratify=list(labels),
        random_state=random_state,
    )
    pipeline = _build_pipeline(max_features=max_features, ngram_range=ngram_range)
    pipeline.fit(X_train, y_train)
    predictions = pipeline.predict(X_test)
    return classification_report(y_test, predictions)


def save_router_classifier(model: Pipeline, path: Path = ROUTER_MODEL_PATH) -> None:
    """Persist the trained pipeline to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)


def load_router_classifier(path: Path = ROUTER_MODEL_PATH) -> Pipeline:
    """Load a previously trained routing pipeline."""
    return joblib.load(path)


def predict_router_proba(model: Pipeline, queries: Iterable[str]) -> np.ndarray:
    """Return class probabilities for each query."""
    return model.predict_proba(list(queries))


__all__ = [
    "ROUTER_MODEL_PATH",
    "train_router_classifier",
    "evaluate_router_classifier",
    "save_router_classifier",
    "load_router_classifier",
    "predict_router_proba",
]
