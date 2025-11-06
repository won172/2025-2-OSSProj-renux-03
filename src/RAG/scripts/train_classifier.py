"""Train or refresh the notice category classifier with optional augmentation."""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.models.embedding import encode_texts
from src.models.category_classifier import (
    CLASSIFIER_PATH,
    predict_category,
    save_classifier,
    train_classifier,
)
from src.pipelines.ingest import DATASET_ARTIFACTS

_BASE_TEMPLATE = [
    "{label} 관련 최신 공지 알려줘",
    "{label} 일정 확인하고 싶어",
    "{label} 공지가 있나요?",
    "{label} 신청 방법이 궁금해",
    "{label} 마감일 알려줘",
]

_TITLE_TEMPLATE = [
    "{title} 공지가 뭐야?",
    "{title} 언제까지야?",
    "{title} 요약해 줘",
    "{title} 관련 정보 알려줘",
]


def _load_notice_chunks() -> pd.DataFrame:
    artifacts = DATASET_ARTIFACTS["notices"]
    path = artifacts.chunk_path
    if not path.exists():
        csv_path = artifacts.csv_path
        if csv_path.exists():
            path = csv_path
        else:
            raise SystemExit("Chunks artifact not found. Run scripts/build_indices.py first.")

    if path.suffix == ".parquet":
        try:
            return pd.read_parquet(path)
        except ImportError:
            print("ℹ️ pyarrow not installed; falling back to CSV export if available.")
            csv_path = artifacts.csv_path
            if csv_path.exists():
                return pd.read_csv(csv_path)
            raise SystemExit("Install pyarrow or fastparquet to read parquet artifacts.")
    return pd.read_csv(path)


def _generate_synthetic_queries(
    label: str,
    title: str,
    max_per_row: int,
    seed: int | None = None,
) -> List[str]:
    rng = random.Random(seed)
    queries: List[str] = []

    templates: List[str] = []
    if label:
        templates.extend(_BASE_TEMPLATE)
    if title:
        templates.extend(_TITLE_TEMPLATE)

    unique_templates = list(dict.fromkeys(templates))
    rng.shuffle(unique_templates)

    for template in unique_templates[:max_per_row]:
        queries.append(
            template.format(label=label, title=title)
        )
    return queries


def _build_training_corpus(
    df: pd.DataFrame,
    *,
    augment: bool,
    max_queries: int,
    seed: int,
) -> Tuple[List[str], List[str]]:
    texts: List[str] = []
    labels: List[str] = []

    rng = random.Random(seed)

    for _, row in df.iterrows():
        label = str(row.get("topics", "")).strip()
        if not label:
            continue
        content = str(row.get("chunk_text", "")).strip()
        title = str(row.get("title", "")).strip()
        if content:
            texts.append(content)
            labels.append(label)

        if augment:
            synthetic_queries = _generate_synthetic_queries(
                label=label,
                title=title,
                max_per_row=max_queries,
                seed=rng.randint(0, 1_000_000),
            )
            for query in synthetic_queries:
                texts.append(query)
                labels.append(label)

    if not texts:
        raise SystemExit("No training samples created. Check the notice chunks file.")

    return texts, labels


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the notice category classifier.")
    parser.add_argument(
        "--augment",
        action="store_true",
        help="Generate synthetic query variations per notice to diversify training.",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=2,
        help="Maximum synthetic queries to create per notice when --augment is enabled.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed used for augmentation shuffling.",
    )
    args = parser.parse_args()

    chunks_df = _load_notice_chunks()
    if "topics" not in chunks_df.columns:
        raise SystemExit("'topics' column not present in chunks_df; cannot train classifier.")

    texts, labels = _build_training_corpus(
        chunks_df,
        augment=args.augment,
        max_queries=max(0, args.max_queries),
        seed=args.seed,
    )
    embeddings = encode_texts(texts)
    classifier = train_classifier(embeddings, labels)
    save_classifier(classifier, CLASSIFIER_PATH)

    sample_text = "장학금 신청 일정 알려줘"
    sample_pred = predict_category(classifier, encode_texts([sample_text]))[0]
    print(f"✅ Saved classifier to {CLASSIFIER_PATH}")
    print(f"🎯 Sample prediction for '{sample_text}': {sample_pred}")
    print(f"📚 Training samples used: {len(texts)} (augment={args.augment})")


if __name__ == "__main__":
    main()
