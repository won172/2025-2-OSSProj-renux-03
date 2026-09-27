"""Replay eligible real single-route questions through hybrid and SQL-first search.

This produces an unlabeled, local-only review queue. Logged questions are
redacted before writing and the output directory is gitignored. Current corpus
artifacts are replayed; this is not a historical snapshot of the corpus.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import date, datetime
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api.rag_service as rag_service  # noqa: E402
from scripts.evaluate_ontology_candidate_blend import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    _latency_summary,
    _production_shortlist,
    _ranked_document_keys,
)
from scripts.evaluate_ontology_history import (  # noqa: E402
    parse_route,
    redact_question,
    select_history_questions,
)
from scripts.evaluate_ontology_retrieval import build_document_identity_map  # noqa: E402
from src.database import RagQueryLog, SessionLocal, SourceDocument  # noqa: E402
from src.services.ontology_build import ontology_projection_is_current  # noqa: E402
from src.services.ontology_retrieval import run_ontology_shadow  # noqa: E402
from src.services.retrieval_strategy import choose_retrieval_strategy  # noqa: E402


def select_structured_history(rows):
    # The runtime strategy explicitly refuses multi-route requests. Preserve
    # that boundary before the shared history selector splits routes apart.
    single_route_rows = [row for row in rows if len(parse_route(row.route)) == 1]
    cases, counters = select_history_questions(single_route_rows)
    eligible = [
        case for case in cases
        if choose_retrieval_strategy(case.question, [case.dataset]).mode == "structured"
        and rag_service._resolve_retrieval_route(
            case.question,
            rag_service.QueryAnalysisMeta(result=None, used=False, failed=False),
        ) == [case.dataset]
    ]
    return eligible, {**counters, "single_route_logs": len(single_route_rows),
                      "eligible_structured_cases": len(eligible)}


async def evaluate(session, cases, *, limit: int | None = None) -> dict:
    selected = list(cases[:max(0, limit)] if limit is not None else cases)
    identity_map = build_document_identity_map(session)
    details = []
    graph_latencies = []
    baseline_latencies = []
    structured_latencies = []
    total_latencies = []
    by_dataset: dict[str, dict[str, int]] = {}
    cold_warmup_ms: dict[str, float] = {}

    for case in selected:
        if case.dataset in cold_warmup_ms:
            continue
        as_of = case.last_seen.date() if case.last_seen else date.today()
        started = time.perf_counter()
        await _production_shortlist(
            case, ontology_keys=None, structured_keys=None, as_of=as_of,
        )
        cold_warmup_ms[case.dataset] = (time.perf_counter() - started) * 1000

    for index, case in enumerate(selected):
        as_of = case.last_seen.date() if case.last_seen else date.today()
        started = time.perf_counter()
        graph = run_ontology_shadow(
            session, case.question, [case.dataset],
            max_hops=2, max_entities=8, max_relations=100,
            max_documents=rag_service.rag_config.RAG_ONTOLOGY_MAX_DOCUMENTS,
        )
        graph_ms = (time.perf_counter() - started) * 1000
        keys = rag_service._structured_document_keys_by_dataset(graph, case.dataset)

        async def timed_shortlist(structured_keys):
            start = time.perf_counter()
            frame = await _production_shortlist(
                case, ontology_keys=None, structured_keys=structured_keys,
                as_of=as_of,
            )
            return frame, (time.perf_counter() - start) * 1000

        if keys and index % 2:
            structured_frame, structured_ms = await timed_shortlist(keys)
            baseline_frame, baseline_ms = await timed_shortlist(None)
        else:
            baseline_frame, baseline_ms = await timed_shortlist(None)
            structured_frame, structured_ms = (
                await timed_shortlist(keys) if keys
                else (baseline_frame, baseline_ms)
            )

        baseline = _ranked_document_keys(baseline_frame, identity_map)
        structured = _ranked_document_keys(structured_frame, identity_map)
        added = [key for key in structured if key not in baseline]
        removed = [key for key in baseline if key not in structured]
        applied = bool(
            "structured_match" in structured_frame.columns
            and structured_frame["structured_match"].eq(1).any()
        )
        stats = by_dataset.setdefault(case.dataset, {
            "cases": 0, "sql_applied": 0, "changed": 0,
            "added_documents": 0, "removed_documents": 0,
        })
        stats["cases"] += 1
        stats["sql_applied"] += int(applied)
        stats["changed"] += int(baseline != structured)
        stats["added_documents"] += len(added)
        stats["removed_documents"] += len(removed)
        graph_latencies.append(graph_ms)
        baseline_latencies.append(baseline_ms)
        structured_latencies.append(structured_ms)
        total_latencies.append(graph_ms + structured_ms)
        details.append({
            "question_hash": case.question_hash,
            "question_redacted": redact_question(case.question),
            "dataset": case.dataset,
            "occurrences": case.occurrences,
            "last_seen": case.last_seen.isoformat() if case.last_seen else None,
            "as_of": as_of.isoformat(),
            "sql_evidence_documents": list(keys.get(case.dataset, ())),
            "sql_applied": applied,
            "baseline_documents": baseline,
            "structured_documents": structured,
            "added_documents": added,
            "removed_documents": removed,
            "changed": baseline != structured,
            "graph_ms": round(graph_ms, 2),
            "baseline_shortlist_ms": round(baseline_ms, 2),
            "structured_shortlist_ms": round(structured_ms, 2),
        })

    return {
        "contract": {
            "evaluation_level": "production candidate shortlist before evidence selection",
            "label_source": "none_human_review_required",
            "corpus": "current_not_historical_snapshot",
            "answer_model_called": False,
            "feature_flag_mutated": False,
            "selected_cases": len(selected),
        },
        "by_dataset": by_dataset,
        "latency": {
            "cold_warmup_ms_by_dataset": {
                dataset: round(ms, 2) for dataset, ms in cold_warmup_ms.items()
            },
            "graph": _latency_summary(graph_latencies),
            "baseline_shortlist": _latency_summary(baseline_latencies),
            "structured_shortlist": _latency_summary(structured_latencies),
            "structured_end_to_end": _latency_summary(total_latencies),
        },
        "cases": details,
    }


def write_reports(output_dir: Path, selection: dict, report: dict, session):
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    comparison_path = output_dir / "comparison.json"
    review_path = output_dir / "changed_documents_review.csv"
    changed = [case for case in report["cases"] if case["changed"]]
    document_keys = {
        key for case in changed
        for field in ("added_documents", "removed_documents")
        for key in case[field]
    }
    catalog = {
        row.document_key: row
        for row in session.query(SourceDocument)
        .filter(SourceDocument.document_key.in_(tuple(document_keys))).all()
    } if document_keys else {}
    summary_path.write_text(json.dumps({
        "generated_at": datetime.now().astimezone().isoformat(),
        "privacy": "redacted questions; local gitignored output only",
        "selection": selection,
        "changed_cases": len(changed),
        **{key: value for key, value in report.items() if key != "cases"},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    comparison_path.write_text(
        json.dumps(report["cases"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with review_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=(
            "question_hash", "question_redacted", "dataset", "change",
            "document_key", "document_title", "source_url",
            "relevance", "relation_correct", "review_note",
        ))
        writer.writeheader()
        for case in changed:
            for change, field in (("added", "added_documents"),
                                  ("removed", "removed_documents")):
                for key in case[field]:
                    source = catalog.get(key)
                    writer.writerow({
                        "question_hash": case["question_hash"],
                        "question_redacted": case["question_redacted"],
                        "dataset": case["dataset"],
                        "change": change,
                        "document_key": key,
                        "document_title": source.title if source else "",
                        "source_url": source.source_url if source else "",
                        "relevance": "",
                        "relation_correct": "",
                        "review_note": "",
                    })
    return summary_path, comparison_path, review_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    session = SessionLocal()
    try:
        current, reason = ontology_projection_is_current(session)
        if not current:
            print(json.dumps({"gate": "failed", "reason": reason}))
            return 1
        rows = session.query(RagQueryLog).order_by(RagQueryLog.id).all()
        cases, selection = select_structured_history(rows)
        report = asyncio.run(evaluate(session, cases, limit=args.limit))
        output_dir = args.output_dir or (
            DEFAULT_OUTPUT_ROOT
            / f"{datetime.now().astimezone():%Y%m%d-%H%M%S}-structured-history"
        )
        paths = write_reports(output_dir, selection, report, session)
    finally:
        session.close()
    print(json.dumps({
        "selection": selection,
        "contract": report["contract"],
        "by_dataset": report["by_dataset"],
        "latency": report["latency"],
        "changed_cases": sum(case["changed"] for case in report["cases"]),
        "outputs": [str(path) for path in paths],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
