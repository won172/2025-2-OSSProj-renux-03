"""Compare 300/600/900-character chunks with the same contextual BM25 setup.

The benchmark reconstructs source documents from the checked-in chunk
artifacts, re-chunks them without mutating production artifacts, and evaluates
document recall plus evidence co-location against ``evaluation_set.csv``.
It intentionally covers the long-form ``notices`` and ``rules`` datasets;
schedule/date lookups and other structured datasets do not use this path.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from rank_bm25 import BM25Okapi

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.search.hybrid import _kiwi_or_light_korean_tokenize  # noqa: E402
from src.services.retrieval_context import enrich_retrieval_fields  # noqa: E402
from src.utils.preprocess import chunk_text, make_chunk_id  # noqa: E402


SUPPORTED_DATASETS = ("notices", "rules")
DEFAULT_SIZES = (300, 600, 900)
GENERIC_EXPECTED_TERMS = {
    "공지",
    "최근",
    "최신",
    "신청",
    "기간",
    "기준",
    "절차",
    "학칙",
    "알려줘",
}


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _strip_repeated_title(text: str, title: str) -> str:
    prefix = f"[{title}]"
    stripped = str(text or "").strip()
    if stripped.startswith(prefix):
        stripped = stripped[len(prefix) :].lstrip()
    return stripped


def _merge_exact_overlap(left: str, right: str, *, max_overlap: int = 600) -> str:
    """Join adjacent legacy chunks while removing their exact overlap."""
    if not left:
        return right
    if not right:
        return left
    upper = min(len(left), len(right), max_overlap)
    for width in range(upper, 19, -1):
        if left[-width:] == right[:width]:
            return left + right[width:]
    return left + "\n" + right


def reconstruct_documents(frame: pd.DataFrame, dataset: str) -> pd.DataFrame:
    """Reconstruct stable document rows from current production chunks."""
    if dataset not in SUPPORTED_DATASETS:
        raise ValueError(f"unsupported dataset: {dataset}")
    if frame.empty:
        return pd.DataFrame()

    work = frame.copy().reset_index(drop=True)
    work["_source_order"] = np.arange(len(work))
    if dataset == "notices":
        work["_document_key"] = work["doc_id"].astype(str)
        if "position" in work.columns:
            work["_position"] = pd.to_numeric(
                work["position"], errors="coerce"
            ).fillna(0)
        else:
            work["_position"] = 0
    else:
        relative = work.get(
            "relative_dir", pd.Series("", index=work.index)
        ).fillna("").astype(str)
        filename = work.get(
            "filename", work.get("title", pd.Series("", index=work.index))
        ).fillna("").astype(str)
        work["_document_key"] = relative + "::" + filename
        work["_position"] = work.groupby("_document_key").cumcount()

    rows: list[dict] = []
    for document_key, group in work.groupby("_document_key", sort=False):
        group = group.sort_values(
            ["_position", "_source_order"], kind="stable"
        )
        first = group.iloc[0]
        title = str(first.get("title") or first.get("filename") or "").strip()
        merged = ""
        for value in group["chunk_text"].fillna("").astype(str):
            merged = _merge_exact_overlap(
                merged,
                _strip_repeated_title(value, title),
            )
        rows.append(
            {
                "doc_id": str(document_key),
                "title": title,
                "text": merged,
                "topics": first.get("topics", ""),
                "published_at": first.get("published_at", ""),
                "category": first.get("category", ""),
                "department": first.get("department", ""),
                "source": dataset,
            }
        )
    return pd.DataFrame(rows)


def rechunk_documents(
    documents: pd.DataFrame,
    *,
    chunk_size: int,
    overlap: int,
) -> pd.DataFrame:
    rows: list[dict] = []
    for _, document in documents.iterrows():
        segments = chunk_text(
            str(document.get("text") or ""),
            chunk_size,
            min(overlap, max(chunk_size // 3, 0)),
        )
        for position, segment in enumerate(segments):
            row = {
                key: value
                for key, value in document.items()
                if key != "text"
            }
            row.update(
                {
                    "chunk_id": make_chunk_id(str(document["doc_id"]), position),
                    "chunk_text": segment,
                    "position": position,
                }
            )
            rows.append(row)
    return enrich_retrieval_fields(pd.DataFrame(rows))


def _expected_terms(value: object) -> list[str]:
    return [
        _compact(term)
        for term in str(value or "").split(",")
        if _compact(term)
    ]


def _relevant_documents(
    documents: pd.DataFrame,
    expected_terms: list[str],
) -> set[str]:
    if not expected_terms:
        return set()
    focused = [
        term for term in expected_terms if term not in GENERIC_EXPECTED_TERMS
    ] or expected_terms
    required = max(1, math.ceil(len(focused) * 2 / 3))
    relevant: set[str] = set()
    for _, document in documents.iterrows():
        material = _compact(
            f"{document.get('title', '')} {document.get('text', '')}"
        )
        matched = sum(term in material for term in focused)
        if matched >= required:
            relevant.add(str(document["doc_id"]))
    return relevant


def evaluate_variant(
    chunks: pd.DataFrame,
    documents: pd.DataFrame,
    cases: pd.DataFrame,
    *,
    top_k: int = 20,
) -> dict[str, float | int]:
    corpus_tokens = [
        _kiwi_or_light_korean_tokenize(text)
        for text in chunks["retrieval_text"].fillna("").astype(str)
    ]
    index = BM25Okapi(corpus_tokens, k1=1.5, b=0.75)
    recalls: list[float] = []
    precisions: list[float] = []
    reciprocal_ranks: list[float] = []
    evidence_coverages: list[float] = []

    for _, case in cases.iterrows():
        terms = _expected_terms(case.get("expected_keywords"))
        relevant = _relevant_documents(documents, terms)
        if not relevant:
            continue
        scores = np.asarray(
            index.get_scores(
                _kiwi_or_light_korean_tokenize(str(case["question"]))
            )
        )
        ranked = np.argsort(-scores, kind="stable")
        ranked = [int(idx) for idx in ranked if scores[idx] > 0][:top_k]
        retrieved = chunks.iloc[ranked] if ranked else chunks.iloc[:0]
        relevant_flags = [
            str(doc_id) in relevant
            for doc_id in retrieved["doc_id"].astype(str).tolist()
        ]
        recalls.append(float(any(relevant_flags)))
        precisions.append(
            sum(relevant_flags) / len(relevant_flags)
            if relevant_flags
            else 0.0
        )
        first_rank = next(
            (rank for rank, hit in enumerate(relevant_flags, start=1) if hit),
            None,
        )
        reciprocal_ranks.append(1.0 / first_rank if first_rank else 0.0)

        if terms and not retrieved.empty:
            coverages = [
                sum(term in _compact(text) for term in terms) / len(terms)
                for text in retrieved["retrieval_text"].astype(str)
            ]
            evidence_coverages.append(max(coverages))
        else:
            evidence_coverages.append(0.0)

    count = len(recalls)
    return {
        "evaluable_queries": count,
        "context_recall_at_20": round(float(np.mean(recalls)), 4) if count else 0.0,
        "context_precision_at_20": round(float(np.mean(precisions)), 4) if count else 0.0,
        "mrr_at_20": round(float(np.mean(reciprocal_ranks)), 4) if count else 0.0,
        "evidence_keyword_coverage_at_20": (
            round(float(np.mean(evidence_coverages)), 4) if count else 0.0
        ),
    }


def run_benchmark(
    *,
    artifact_dir: Path,
    evaluation_set: Path,
    sizes: tuple[int, ...] = DEFAULT_SIZES,
    overlap: int = 80,
) -> dict:
    cases = pd.read_csv(evaluation_set).fillna("")
    result: dict[str, object] = {
        "method": "contextual_bm25_document_recall",
        "sizes": {},
    }
    for dataset in SUPPORTED_DATASETS:
        source = pd.read_parquet(artifact_dir / f"{dataset}.parquet")
        documents = reconstruct_documents(source, dataset)
        dataset_cases = cases[cases["expected_dataset"] == dataset]
        for size in sizes:
            chunks = rechunk_documents(
                documents,
                chunk_size=size,
                overlap=overlap,
            )
            metrics = evaluate_variant(chunks, documents, dataset_cases)
            size_result = result["sizes"].setdefault(
                str(size),
                {"datasets": {}, "chunk_count": 0},
            )
            size_result["datasets"][dataset] = metrics
            size_result["chunk_count"] += len(chunks)

    for size_result in result["sizes"].values():
        dataset_metrics = list(size_result["datasets"].values())
        total_queries = sum(
            metric["evaluable_queries"] for metric in dataset_metrics
        )
        for metric_name in (
            "context_recall_at_20",
            "context_precision_at_20",
            "mrr_at_20",
            "evidence_keyword_coverage_at_20",
        ):
            weighted = sum(
                metric[metric_name] * metric["evaluable_queries"]
                for metric in dataset_metrics
            )
            size_result[metric_name] = (
                round(weighted / total_queries, 4) if total_queries else 0.0
            )
        size_result["evaluable_queries"] = total_queries
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=ROOT / "artifacts" / "chunks",
    )
    parser.add_argument(
        "--evaluation-set",
        type=Path,
        default=ROOT / "tests" / "evaluation_set.csv",
    )
    parser.add_argument(
        "--sizes",
        default="300,600,900",
        help="comma-separated character chunk sizes",
    )
    parser.add_argument("--overlap", type=int, default=80)
    args = parser.parse_args()
    sizes = tuple(int(value) for value in args.sizes.split(",") if value.strip())
    print(
        json.dumps(
            run_benchmark(
                artifact_dir=args.artifact_dir,
                evaluation_set=args.evaluation_set,
                sizes=sizes,
                overlap=args.overlap,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
