"""Lightweight CLI to send a sample query through the RAG pipeline."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.config import DEFAULT_TOP_K, HYBRID_ALPHA
from src.pipelines.ingest import (
    DATASET_ARTIFACTS,
    ingest_courses,
    ingest_notices,
    ingest_rules,
    ingest_schedule,
)
from src.search.hybrid import load_tfidf, hybrid_search_with_meta
from src.services.answer import answer_with_citations


def run(dataset: str, query: str, top_k: int, alpha: float) -> None:
    artifacts = DATASET_ARTIFACTS.get(dataset)
    if artifacts is None:
        raise SystemExit(f"Unknown dataset '{dataset}'. Available: {', '.join(DATASET_ARTIFACTS)}")

    loaders = {
        "notices": ingest_notices,
        "rules": ingest_rules,
        "schedule": ingest_schedule,
        "courses": ingest_courses,
    }

    chunk_path = artifacts.chunk_path
    csv_path = artifacts.csv_path

    if chunk_path.exists():
        if chunk_path.suffix == ".csv":
            chunks_df = pd.read_csv(chunk_path)
        else:
            chunks_df = pd.read_parquet(chunk_path)
    elif csv_path.exists():
        chunks_df = pd.read_csv(csv_path)
        artifacts.chunk_path = csv_path
    else:
        print(f"⚠️ Chunks for {dataset} not found. Building on the fly...")
        chunks_df, _, _ = loaders[dataset]()

    try:
        vectorizer, matrix = load_tfidf(dataset)
    except FileNotFoundError:
        print(f"⚠️ TF-IDF for {dataset} not found. Rebuilding...")
        chunks_df, vectorizer, matrix = loaders[dataset]()

    hits = hybrid_search_with_meta(artifacts.collection, chunks_df, vectorizer, matrix, query, top_k=top_k, alpha=alpha)
    answer, citations = answer_with_citations(query, hits)
    print("Q:", query)
    print("A:", answer)
    print("\nSources:\n", citations)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", help="질문 문장")
    parser.add_argument("--dataset", default="notices", help="검색할 데이터셋 (default: notices)")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--alpha", type=float, default=HYBRID_ALPHA)
    parser.add_argument("--session", default=None, help="대화 세션 ID")
    args = parser.parse_args()
    run(args.dataset, args.query, args.top_k, args.alpha)


if __name__ == "__main__":
    main()
