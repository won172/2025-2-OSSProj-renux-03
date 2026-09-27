"""Replay the production SQL-first shortlist against the previous hybrid path.

The checked-in qrels are structural fixtures, not human relevance judgments.
This read-only comparison does not call an answer model or toggle runtime flags.
It checks the exact production strategy selector, ontology evidence keys, and
shortlist materialization. A current ontology build is required at the CLI gate.
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

import api.rag_service as rag_service  # noqa: E402
from scripts.evaluate_ontology_candidate_blend import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    _latency_summary,
    _production_shortlist,
    _ranked_document_keys,
)
from scripts.evaluate_ontology_retrieval import (  # noqa: E402
    CompetencyCase,
    build_document_identity_map,
    load_cases,
    load_document_qrels,
    validate_fixture,
)
from scripts.evaluate_retrieval import aggregate, ndcg_at_k, score_case  # noqa: E402
from src.database import SessionLocal  # noqa: E402
from src.services.ontology_build import ontology_projection_is_current  # noqa: E402
from src.services.ontology_retrieval import run_ontology_shadow  # noqa: E402
from src.services.retrieval_strategy import choose_retrieval_strategy  # noqa: E402


def selected_cases(
    cases: Sequence[CompetencyCase],
    qrels: dict[tuple[str, str], dict[str, int]],
) -> list[CompetencyCase]:
    return [
        case for case in cases
        if (case.id, case.route) in qrels
        and choose_retrieval_strategy(case.question, [case.route]).mode == "structured"
        and rag_service._resolve_retrieval_route(
            case.question,
            rag_service.QueryAnalysisMeta(result=None, used=False, failed=False),
        ) == [case.route]
    ]


def _shortlist_metrics(
    ranked: list[str], relevance: dict[str, int], shortlist_size: int,
) -> tuple[float, float]:
    positive = [value for value in relevance.values() if value >= 1]
    if not positive:
        return 0.0, 0.0
    ranked_relevance = [int(relevance.get(key, 0)) for key in ranked]
    recall = sum(value >= 1 for value in ranked_relevance[:shortlist_size]) / len(positive)
    return recall, ndcg_at_k(ranked_relevance, positive, shortlist_size)


async def evaluate(
    session,
    cases: Sequence[CompetencyCase],
    qrels: dict[tuple[str, str], dict[str, int]],
    *,
    as_of: date,
    limit: int | None = None,
) -> dict:
    chosen = selected_cases(cases, qrels)
    if limit is not None:
        chosen = chosen[:max(0, limit)]
    identity_map = build_document_identity_map(session)
    baseline_scores = []
    structured_scores = []
    baseline_recalls: list[float] = []
    structured_recalls: list[float] = []
    baseline_ndcgs: list[float] = []
    structured_ndcgs: list[float] = []
    graph_latencies: list[float] = []
    baseline_latencies: list[float] = []
    structured_latencies: list[float] = []
    structured_total_latencies: list[float] = []
    details: list[dict] = []
    per_dataset: dict[str, dict[str, int]] = {}
    cold_warmup_ms: dict[str, float] = {}
    shortlist_size = rag_service.RAG_EVIDENCE_CANDIDATES_PER_DATASET

    # A single global warm-up biases the first case of every later dataset.
    # Record one cold load per dataset separately from paired warm timings.
    for case in chosen:
        if case.route in cold_warmup_ms:
            continue
        started = time.perf_counter()
        await _production_shortlist(
            case, ontology_keys=None, structured_keys=None, as_of=as_of,
        )
        cold_warmup_ms[case.route] = (time.perf_counter() - started) * 1000

    for index, case in enumerate(chosen):
        graph_started = time.perf_counter()
        graph_result = run_ontology_shadow(
            session, case.question, [case.route],
            max_hops=2, max_entities=8, max_relations=100,
            max_documents=rag_service.rag_config.RAG_ONTOLOGY_MAX_DOCUMENTS,
        )
        graph_ms = (time.perf_counter() - graph_started) * 1000
        graph_latencies.append(graph_ms)
        keys = rag_service._structured_document_keys_by_dataset(graph_result, case.route)

        async def timed_shortlist(structured_keys):
            started = time.perf_counter()
            shortlist = await _production_shortlist(
                case, ontology_keys=None, structured_keys=structured_keys,
                as_of=as_of,
            )
            return shortlist, (time.perf_counter() - started) * 1000

        if keys and index % 2:
            structured_frame, structured_ms = await timed_shortlist(keys)
            baseline_frame, baseline_ms = await timed_shortlist(None)
        else:
            baseline_frame, baseline_ms = await timed_shortlist(None)
            if keys:
                structured_frame, structured_ms = await timed_shortlist(keys)
            else:
                # No SQL evidence means production falls back to the same hybrid path.
                structured_frame, structured_ms = baseline_frame, baseline_ms

        baseline_latencies.append(baseline_ms)
        structured_latencies.append(structured_ms)
        structured_total_latencies.append(graph_ms + structured_ms)
        baseline_documents = _ranked_document_keys(baseline_frame, identity_map)
        structured_documents = _ranked_document_keys(structured_frame, identity_map)
        relevance = qrels[(case.id, case.route)]
        baseline_scores.append(score_case(
            question_id=case.id, dataset=case.route, question=case.question,
            ranked_ids=baseline_documents, relevance_by_id=relevance,
        ))
        structured_scores.append(score_case(
            question_id=case.id, dataset=case.route, question=case.question,
            ranked_ids=structured_documents, relevance_by_id=relevance,
        ))
        baseline_recall, baseline_ndcg = _shortlist_metrics(
            baseline_documents, relevance, shortlist_size,
        )
        structured_recall, structured_ndcg = _shortlist_metrics(
            structured_documents, relevance, shortlist_size,
        )
        baseline_recalls.append(baseline_recall)
        structured_recalls.append(structured_recall)
        baseline_ndcgs.append(baseline_ndcg)
        structured_ndcgs.append(structured_ndcg)
        applied = bool(
            "structured_match" in structured_frame.columns
            and structured_frame["structured_match"].eq(1).any()
        )
        dataset = per_dataset.setdefault(
            case.route,
            {"cases": 0, "sql_applied": 0, "changed": 0, "regressed": 0},
        )
        dataset["cases"] += 1
        dataset["sql_applied"] += int(applied)
        dataset["changed"] += int(baseline_documents != structured_documents)
        dataset["regressed"] += int(structured_recall < baseline_recall)
        details.append({
            "id": case.id,
            "dataset": case.route,
            "question": case.question,
            "sql_evidence_documents": list(keys.get(case.route, ())),
            "sql_applied": applied,
            "baseline_documents": baseline_documents,
            "structured_documents": structured_documents,
            "baseline_recall_at_shortlist": round(baseline_recall, 4),
            "structured_recall_at_shortlist": round(structured_recall, 4),
            "graph_ms": round(graph_ms, 2),
            "baseline_shortlist_ms": round(baseline_ms, 2),
            "structured_shortlist_ms": round(structured_ms, 2),
        })

    baseline = aggregate(baseline_scores) if baseline_scores else {}
    structured = aggregate(structured_scores) if structured_scores else {}
    for summary, recalls, ndcgs in (
        (baseline, baseline_recalls, baseline_ndcgs),
        (structured, structured_recalls, structured_ndcgs),
    ):
        summary["recall@shortlist"] = statistics.fmean(recalls) if recalls else 0.0
        summary["ndcg@shortlist"] = statistics.fmean(ndcgs) if ndcgs else 0.0
    return {
        "contract": {
            "evaluation_level": "production candidate shortlist before evidence selection",
            "label_source": "canonical_structured_fixture_not_human",
            "as_of": as_of.isoformat(),
            "cases": len(chosen),
            "per_dataset_shortlist": shortlist_size,
            "routing_gate": "current_deterministic_route_matches_fixture_dataset",
            "answer_model_called": False,
            "feature_flag_mutated": False,
        },
        "baseline": baseline,
        "structured": structured,
        "by_dataset": per_dataset,
        "latency": {
            "cold_warmup_ms_by_dataset": {
                dataset: round(ms, 2) for dataset, ms in cold_warmup_ms.items()
            },
            "graph": _latency_summary(graph_latencies),
            "baseline_shortlist": _latency_summary(baseline_latencies),
            "structured_shortlist": _latency_summary(structured_latencies),
            "structured_end_to_end": _latency_summary(structured_total_latencies),
        },
        "cases": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    session = SessionLocal()
    try:
        cases = load_cases()
        qrels = load_document_qrels()
        errors = validate_fixture(session, cases, qrels)
        current, reason = ontology_projection_is_current(session)
        if not current:
            errors.append(f"ontology projection is not current: {reason}")
        if errors:
            print(json.dumps({"gate": "failed", "errors": errors}, ensure_ascii=False, indent=2))
            return 1
        report = asyncio.run(evaluate(
            session, cases, qrels, as_of=args.as_of, limit=args.limit,
        ))
    finally:
        session.close()
    output = args.output or (
        DEFAULT_OUTPUT_ROOT
        / f"{datetime.now().astimezone():%Y%m%d-%H%M%S}-structured-retrieval.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "contract": report["contract"],
        "baseline": report["baseline"],
        "structured": report["structured"],
        "by_dataset": report["by_dataset"],
        "latency": report["latency"],
        "output": str(output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
