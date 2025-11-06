"""Utility functions for the notice category classifier."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Tuple

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, confusion_matrix

from src.config import MODEL_DIR

CLASSIFIER_PATH = MODEL_DIR / "notice_category_classifier.pkl"


def train_classifier(embeddings: np.ndarray, labels: Iterable[str]) -> LogisticRegression:
    model = LogisticRegression(max_iter=2000)
    model.fit(embeddings, list(labels))
    return model


def evaluate_classifier(model: LogisticRegression, embeddings: np.ndarray, labels: Iterable[str]) -> Tuple[str, np.ndarray]:
    X_train, X_test, y_train, y_test = train_test_split(
        embeddings,
        list(labels),
        test_size=0.2,
        stratify=list(labels),
        random_state=42,
    )
    clf = LogisticRegression(max_iter=2000)
    clf.fit(X_train, y_train)
    y_pred = clf.predict(X_test)
    report = classification_report(y_test, y_pred)
    matrix = confusion_matrix(y_test, y_pred)
    return report, matrix


def save_classifier(model: LogisticRegression, path: Path = CLASSIFIER_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)


def load_classifier(path: Path = CLASSIFIER_PATH) -> LogisticRegression:
    return joblib.load(path)


def predict_category(model: LogisticRegression, embeddings: np.ndarray) -> np.ndarray:
    return model.predict(embeddings)


__all__ = [
    "CLASSIFIER_PATH",
    "train_classifier",
    "evaluate_classifier",
    "save_classifier",
    "load_classifier",
    "predict_category",
]
