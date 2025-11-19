"""데이터셋 라우팅 분류기를 학습하고 불러오는 유틸리티입니다."""
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
    """TF-IDF와 로지스틱 회귀를 결합한 파이프라인을 생성합니다."""
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
    """주어진 말뭉치로 라우터 분류기를 학습합니다."""
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
    """홀드아웃 분할을 사용해 분류 리포트를 반환합니다."""
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
    """학습된 파이프라인을 디스크에 저장합니다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)


def load_router_classifier(path: Path = ROUTER_MODEL_PATH) -> Pipeline:
    """이미 학습된 라우팅 파이프라인을 불러옵니다."""
    return joblib.load(path)


def predict_router_proba(model: Pipeline, queries: Iterable[str]) -> np.ndarray:
    """각 질의에 대한 클래스 확률을 반환합니다."""
    return model.predict_proba(list(queries))


__all__ = [
    "ROUTER_MODEL_PATH",
    "train_router_classifier",
    "evaluate_router_classifier",
    "save_router_classifier",
    "load_router_classifier",
    "predict_router_proba",
]
