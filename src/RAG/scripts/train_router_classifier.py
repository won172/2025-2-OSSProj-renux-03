"""Train or refresh the dataset routing classifier."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, Tuple

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.config import ROUTER_RULES
from src.models.router_classifier import (
    ROUTER_MODEL_PATH,
    evaluate_router_classifier,
    predict_router_proba,
    save_router_classifier,
    train_router_classifier,
)
from src.pipelines.ingest import DATASET_ARTIFACTS


def _load_chunks(key: str) -> pd.DataFrame:
    artifact = DATASET_ARTIFACTS[key]
    path = artifact.chunk_path
    if not path.exists():
        csv_path = artifact.csv_path
        if csv_path.exists():
            path = csv_path
        else:
            raise FileNotFoundError(
                f"Chunk artifact for '{key}' not found at {artifact.chunk_path} or {artifact.csv_path}. "
                "Run scripts/build_indices.py first."
            )
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def _synthetic_queries(key: str) -> Iterable[str]:
    for kw in ROUTER_RULES.get(key, []):
        root = kw.strip()
        if not root:
            continue
        yield root
        yield f"{root} 알려줘"
        yield f"{root} 안내 부탁해"
        yield f"{root} 일정 알려줘"
        yield f"{root} 관련 정보"


def build_training_corpus(
    max_samples_per_dataset: int | None = 1500,
    include_keywords: bool = True,
) -> Tuple[list[str], list[str]]:
    texts: list[str] = []
    labels: list[str] = []

    for key in DATASET_ARTIFACTS:
        df = _load_chunks(key)
        if df.empty:
            continue
        if max_samples_per_dataset is not None and len(df) > max_samples_per_dataset:
            df = df.sample(n=max_samples_per_dataset, random_state=42)

        for _, row in df.iterrows():
            title = str(row.get("title", "")).strip()
            chunk_text = str(row.get("chunk_text", "")).strip()
            if not chunk_text and not title:
                continue
            combined = " ".join(part for part in (title, chunk_text) if part)
            texts.append(combined)
            labels.append(key)

        if include_keywords:
            for query in _synthetic_queries(key):
                texts.append(query)
                labels.append(key)

    if not texts:
        raise ValueError("No training samples collected. Ensure artifacts are built.")

    return texts, labels


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the dataset routing classifier.")
    parser.add_argument(
        "--max-samples",
        type=int,
        default=1500,
        help="Maximum chunk samples per dataset (default: 1500, set <=0 for all).",
    )
    parser.add_argument(
        "--skip-keywords",
        action="store_true",
        help="Exclude synthetic keyword-based queries from training.",
    )
    args = parser.parse_args()

    max_samples = args.max_samples if args.max_samples and args.max_samples > 0 else None
    texts, labels = build_training_corpus(
        max_samples_per_dataset=max_samples,
        include_keywords=not args.skip_keywords,
    )

    print(f"📚 Collected {len(texts)} training samples across {len(set(labels))} datasets.")

    model = train_router_classifier(texts, labels)
    save_router_classifier(model, ROUTER_MODEL_PATH)
    print(f"✅ Saved router classifier to {ROUTER_MODEL_PATH}")

    report = evaluate_router_classifier(texts, labels)
    print("📊 Evaluation (hold-out classification report):")
    print(report)

    sample_questions = [
        "장학금 신청 마감일 알려줘",
        "학칙 제정 절차는 어떻게 돼?",
        "수강신청 일정이 궁금해",
        "통계학과 전공 과목 추천해줘",
    ]
    probabilities = predict_router_proba(model, sample_questions)
    classes = model.named_steps["clf"].classes_
    for question, probs in zip(sample_questions, probabilities):
        ranked = sorted(zip(classes, probs), key=lambda x: x[1], reverse=True)
        best_label, best_prob = ranked[0]
        print(f"🔎 '{question}' → {best_label} ({best_prob:.2f})")


if __name__ == "__main__":
    main()
