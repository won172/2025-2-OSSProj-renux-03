"""Evaluate the actual RAG shortlist with ontology candidates disabled/enabled.

This is a local, read-only evaluation.  It reuses the production retrieval and
balanced-shortlist functions, but it never calls an answer model, writes query
logs, changes artifacts, or enables the runtime feature flag.  Only datasets
approved for the first candidate-blend rollout are evaluated.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

import api.rag_service as rag_service  # noqa: E402
from scripts.evaluate_ontology_retrieval import (  # noqa: E402
    CompetencyCase,
    build_document_identity_map,
    load_cases,
    load_document_qrels,
    validate_fixture,
)
from scripts.evaluate_retrieval import (  # noqa: E402
    aggregate,
    ndcg_at_k,
    score_case,
)
from src.database import SessionLocal  # noqa: E402
from src.services.ontology_retrieval import run_ontology_shadow  # noqa: E402
from src.utils.date_parser import extract_date_filter_from_query  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "artifacts" / "ontology_evaluations"
BLEND_DATASETS = frozenset({"rules", "schedule", "notices"})


def _candidate_keys(
    document_keys: Sequence[str],
    dataset: str,
    *,
    limit: int,
) -> dict[str, tuple[str, ...]]:
    if dataset not in BLEND_DATASETS:
        return {}
    prefix = f"{dataset}:"
    selected: list[str] = []
    for raw_key in document_keys:
        key = str(raw_key or "").strip()
        if key.startswith(prefix) and key not in selected:
            selected.append(key)
        if len(selected) >= limit:
            break
    return {dataset: tuple(selected)} if selected else {}


def _ranked_document_keys(
    shortlist: pd.DataFrame,
    identity_map: dict[str, str],
) -> list[str]:
    ranked: list[str] = []
    seen: set[str] = set()
    for _, row in shortlist.iterrows():
        identity = ""
        for column in ("document_key", "doc_id"):
            value = row.get(column)
            if value is None or pd.isna(value):
                continue
            identity = str(value).strip()
            if identity:
                break
        document_key = identity_map.get(identity, "")
        if not document_key or document_key in seen:
            continue
        seen.add(document_key)
        ranked.append(document_key)
    return ranked


async def _production_shortlist(
    case: CompetencyCase,
    *,
    ontology_keys: dict[str, tuple[str, ...]] | None,
    as_of: date,
    structured_keys: dict[str, tuple[str, ...]] | None = None,
) -> pd.DataFrame:
    dataset = getattr(case, "route", None) or getattr(case, "dataset")
    case_id = getattr(case, "id", None) or getattr(case, "question_hash", "history")[:12]
    date_filter = extract_date_filter_from_query(case.question, today=as_of)
    recent_notice_query, notice_board_filter, _ = (
        rag_service._resolve_notice_retrieval_controls(
            case.question,
            case.question,
            [dataset],
        )
    )
    active_notice_query = rag_service._is_active_notice_state_query(
        case.question,
        [dataset],
    )
    frames, _, unavailable = await rag_service._retrieve_frames_for_queries(
        route=[dataset],
        queries=[case.question],
        final_where_filter={},
        notice_board_filter=notice_board_filter,
        date_filter=date_filter,
        entry_year=rag_service._extract_entry_year_from_query(case.question),
        request_id=f"ontology-blend-eval-{case_id}",
        notice_visibility_filter=None,
        recent_notice_query=recent_notice_query,
        active_notice_query=active_notice_query,
        active_notice_as_of=as_of if active_notice_query else None,
        current_operational_notice_terms=(
            rag_service._current_operational_notice_terms(
                case.question,
                [dataset],
            )
        ),
        allow_wise=False,
        ontology_document_keys_by_dataset=ontology_keys,
        structured_document_keys_by_dataset=structured_keys,
        as_of=as_of,
    )
    if unavailable:
        raise RuntimeError(
            f"unavailable retrieval artifact for {case_id}: {unavailable}"
        )
    shortlist = rag_service._build_balanced_shortlist(
        frames,
        per_dataset=rag_service.RAG_EVIDENCE_CANDIDATES_PER_DATASET,
        max_candidates=rag_service.RAG_EVIDENCE_MAX_CANDIDATES,
        query=case.question,
        as_of=as_of,
    )
    return rag_service._apply_cross_encoder_rerank(shortlist, case.question)


def _latency_summary(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, int((len(ordered) - 1) * 0.95))
    return {
        "p50_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[p95_index], 2),
        "mean_ms": round(statistics.fmean(ordered), 2),
    }


async def evaluate(
    session,
    cases: Sequence[CompetencyCase],
    qrels: dict[tuple[str, str], dict[str, int]],
    *,
    as_of: date,
    document_limit: int = 2,
) -> dict:
    identity_map = build_document_identity_map(session)
    baseline_scores = []
    blended_scores = []
    details: list[dict] = []
    baseline_latencies: list[float] = []
    blended_latencies: list[float] = []
    ontology_latencies: list[float] = []
    blended_end_to_end_latencies: list[float] = []
    baseline_recall_at_shortlist: list[float] = []
    blended_recall_at_shortlist: list[float] = []
    baseline_ndcg_at_shortlist: list[float] = []
    blended_ndcg_at_shortlist: list[float] = []
    changed_cases = 0
    added_relevant_documents = 0

    selected_cases = [case for case in cases if case.route in BLEND_DATASETS]
    if selected_cases:
        # Exclude one-time embedding/model and artifact initialization from the
        # paired latency comparison.  The warm-up result is intentionally not
        # scored.
        await _production_shortlist(
            selected_cases[0],
            ontology_keys=None,
            as_of=as_of,
        )

    for case_index, case in enumerate(selected_cases):
        ontology_started_at = time.perf_counter()
        result = run_ontology_shadow(
            session,
            case.question,
            [case.route],
            max_hops=2,
            max_entities=8,
            max_relations=100,
            max_documents=50,
        )
        ontology_latency = (time.perf_counter() - ontology_started_at) * 1000
        ontology_latencies.append(ontology_latency)
        ontology_keys = _candidate_keys(
            result.document_keys,
            case.route,
            limit=document_limit,
        )

        async def timed_shortlist(keys):
            started_at = time.perf_counter()
            shortlist = await _production_shortlist(
                case,
                ontology_keys=keys,
                as_of=as_of,
            )
            return shortlist, (time.perf_counter() - started_at) * 1000

        # Alternate pair order so cache warmth cannot systematically favor one
        # side of the comparison.
        if case_index % 2 == 0:
            baseline_shortlist, baseline_latency = await timed_shortlist(None)
            blended_shortlist, blended_latency = await timed_shortlist(
                ontology_keys
            )
        else:
            blended_shortlist, blended_latency = await timed_shortlist(
                ontology_keys
            )
            baseline_shortlist, baseline_latency = await timed_shortlist(None)
        baseline_latencies.append(baseline_latency)
        blended_latencies.append(blended_latency)
        blended_end_to_end_latencies.append(
            ontology_latency + blended_latency
        )

        baseline_documents = _ranked_document_keys(
            baseline_shortlist,
            identity_map,
        )
        blended_documents = _ranked_document_keys(
            blended_shortlist,
            identity_map,
        )
        relevance = qrels[(case.id, case.route)]
        baseline_score = score_case(
            question_id=case.id,
            dataset=case.route,
            question=case.question,
            ranked_ids=baseline_documents,
            relevance_by_id=relevance,
        )
        blended_score = score_case(
            question_id=case.id,
            dataset=case.route,
            question=case.question,
            ranked_ids=blended_documents,
            relevance_by_id=relevance,
        )
        baseline_scores.append(baseline_score)
        blended_scores.append(blended_score)
        shortlist_size = rag_service.RAG_EVIDENCE_CANDIDATES_PER_DATASET
        all_relevance = [value for value in relevance.values() if value >= 1]
        baseline_ranked_relevance = [
            int(relevance.get(key, 0)) for key in baseline_documents
        ]
        blended_ranked_relevance = [
            int(relevance.get(key, 0)) for key in blended_documents
        ]
        total_relevant = len(all_relevance)
        baseline_recall = (
            sum(value >= 1 for value in baseline_ranked_relevance[:shortlist_size])
            / total_relevant
            if total_relevant
            else 0.0
        )
        blended_recall = (
            sum(value >= 1 for value in blended_ranked_relevance[:shortlist_size])
            / total_relevant
            if total_relevant
            else 0.0
        )
        baseline_recall_at_shortlist.append(baseline_recall)
        blended_recall_at_shortlist.append(blended_recall)
        baseline_ndcg_at_shortlist.append(
            ndcg_at_k(
                baseline_ranked_relevance,
                all_relevance,
                shortlist_size,
            )
        )
        blended_ndcg_at_shortlist.append(
            ndcg_at_k(
                blended_ranked_relevance,
                all_relevance,
                shortlist_size,
            )
        )
        changed_cases += int(baseline_documents != blended_documents)
        baseline_relevant = {
            key for key in baseline_documents if relevance.get(key, 0) >= 1
        }
        blended_relevant = {
            key for key in blended_documents if relevance.get(key, 0) >= 1
        }
        added_relevant_documents += len(blended_relevant - baseline_relevant)
        details.append(
            {
                "id": case.id,
                "route": case.route,
                "question": case.question,
                "ontology_candidate_documents": list(
                    ontology_keys.get(case.route, ())
                ),
                "baseline_documents": baseline_documents,
                "blended_documents": blended_documents,
                "baseline_recall_at_3": baseline_recall,
                "blended_recall_at_3": blended_recall,
                "baseline_mrr": baseline_score.mrr,
                "blended_mrr": blended_score.mrr,
                "ontology_latency_ms": round(ontology_latency, 2),
                "baseline_retrieval_and_shortlist_ms": round(
                    baseline_latency,
                    2,
                ),
                "blended_retrieval_and_shortlist_ms": round(
                    blended_latency,
                    2,
                ),
            }
        )

    baseline_summary = aggregate(baseline_scores)
    blended_summary = aggregate(blended_scores)
    baseline_summary["recall@3"] = statistics.fmean(
        baseline_recall_at_shortlist
    )
    blended_summary["recall@3"] = statistics.fmean(
        blended_recall_at_shortlist
    )
    baseline_summary["ndcg@3"] = statistics.fmean(
        baseline_ndcg_at_shortlist
    )
    blended_summary["ndcg@3"] = statistics.fmean(
        blended_ndcg_at_shortlist
    )

    return {
        "contract": {
            "evaluation_level": "production candidate shortlist before evidence selection",
            "datasets": sorted(BLEND_DATASETS),
            "cases": len(selected_cases),
            "per_dataset_shortlist": rag_service.RAG_EVIDENCE_CANDIDATES_PER_DATASET,
            "ontology_document_limit": document_limit,
            "feature_flag_mutated": False,
            "answer_model_called": False,
        },
        "baseline": baseline_summary,
        "blended": blended_summary,
        "comparison": {
            "changed_cases": changed_cases,
            "added_relevant_documents": added_relevant_documents,
        },
        "latency": {
            "baseline_retrieval_and_shortlist": _latency_summary(
                baseline_latencies
            ),
            "blended_retrieval_and_shortlist": _latency_summary(
                blended_latencies
            ),
            "ontology_traversal": _latency_summary(ontology_latencies),
            "blended_end_to_end": _latency_summary(
                blended_end_to_end_latencies
            ),
        },
        "cases": details,
    }


def print_report(report: dict) -> None:
    baseline = report["baseline"]
    blended = report["blended"]
    comparison = report["comparison"]
    print("Ontology candidate blend evaluation")
    print(
        f"  cases={report['contract']['cases']}, "
        f"datasets={','.join(report['contract']['datasets'])}"
    )
    print(
        "  baseline shortlist: "
        f"recall@3={baseline.get('recall@3', 0):.3f}, "
        f"mrr={baseline.get('mrr', 0):.3f}, "
        f"ndcg@3={baseline.get('ndcg@3', 0):.3f}"
    )
    print(
        "  blended shortlist: "
        f"recall@3={blended.get('recall@3', 0):.3f}, "
        f"mrr={blended.get('mrr', 0):.3f}, "
        f"ndcg@3={blended.get('ndcg@3', 0):.3f}"
    )
    print(
        "  changes: "
        f"cases={comparison['changed_cases']}, "
        f"added_relevant_documents={comparison['added_relevant_documents']}"
    )
    baseline_latency = report["latency"]["baseline_retrieval_and_shortlist"]
    blended_latency = report["latency"]["blended_retrieval_and_shortlist"]
    ontology_latency = report["latency"]["ontology_traversal"]
    end_to_end_latency = report["latency"]["blended_end_to_end"]
    print(
        "  retrieval+shortlist latency p50/p95: "
        f"baseline={baseline_latency.get('p50_ms', 0):.2f}/"
        f"{baseline_latency.get('p95_ms', 0):.2f}ms, "
        f"blended={blended_latency.get('p50_ms', 0):.2f}/"
        f"{blended_latency.get('p95_ms', 0):.2f}ms"
    )
    print(
        "  ontology p50/p95 and blended end-to-end: "
        f"ontology={ontology_latency.get('p50_ms', 0):.2f}/"
        f"{ontology_latency.get('p95_ms', 0):.2f}ms, "
        f"end-to-end={end_to_end_latency.get('p50_ms', 0):.2f}/"
        f"{end_to_end_latency.get('p95_ms', 0):.2f}ms"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare baseline and ontology-blended production shortlists"
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--document-limit", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    cases = load_cases()
    qrels = load_document_qrels()
    session = SessionLocal()
    try:
        errors = validate_fixture(session, cases, qrels)
        if errors:
            print(json.dumps({"gate": "failed", "errors": errors}, ensure_ascii=False, indent=2))
            return 1
        report = asyncio.run(
            evaluate(
                session,
                cases,
                qrels,
                as_of=args.as_of,
                document_limit=max(1, min(args.document_limit, 3)),
            )
        )
    finally:
        session.close()

    print_report(report)
    output = args.output
    if output is None:
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        output = DEFAULT_OUTPUT_ROOT / f"{stamp}-candidate-blend.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
