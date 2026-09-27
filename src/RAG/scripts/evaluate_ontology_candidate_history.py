"""Replay accumulated questions through the production candidate shortlist.

Only real-traffic rules/schedule/notices cases with bounded ontology documents
are retrieved twice.  Cases without graph candidates are unchanged by
definition and are counted without doing redundant vector searches.  Question
text uses the same local redaction boundary as the existing history evaluator.
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

from scripts.evaluate_ontology_candidate_blend import (  # noqa: E402
    BLEND_DATASETS,
    _candidate_keys,
    _latency_summary,
    _production_shortlist,
    _ranked_document_keys,
)
from scripts.evaluate_ontology_history import (  # noqa: E402
    redact_question,
    select_history_questions,
)
from scripts.evaluate_ontology_retrieval import (  # noqa: E402
    build_document_identity_map,
)
from src.database import RagQueryLog, SessionLocal, SourceDocument  # noqa: E402
from src.services.ontology_retrieval import run_ontology_shadow  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "artifacts" / "ontology_evaluations"


async def evaluate_history_candidate_blend(
    session,
    cases,
    *,
    document_limit: int = 2,
    limit: int | None = None,
) -> dict:
    selected = [case for case in cases if case.dataset in BLEND_DATASETS]
    if limit is not None:
        selected = selected[: max(0, limit)]
    identity_map = build_document_identity_map(session)

    details: list[dict] = []
    ontology_latencies: list[float] = []
    baseline_latencies: list[float] = []
    blended_latencies: list[float] = []
    graph_candidate_cases = 0
    changed_cases = 0
    added_documents = 0
    removed_documents = 0
    warm = False

    for case_index, case in enumerate(selected):
        as_of = case.last_seen.date() if case.last_seen else date.today()
        started_at = time.perf_counter()
        result = run_ontology_shadow(
            session,
            case.question,
            [case.dataset],
            max_hops=2,
            max_entities=8,
            max_relations=100,
            max_documents=50,
        )
        ontology_latency = (time.perf_counter() - started_at) * 1000
        ontology_latencies.append(ontology_latency)
        ontology_keys = _candidate_keys(
            result.document_keys,
            case.dataset,
            limit=document_limit,
        )
        if not ontology_keys:
            continue
        graph_candidate_cases += 1

        if not warm:
            await _production_shortlist(
                case,
                ontology_keys=None,
                as_of=as_of,
            )
            warm = True

        async def timed_shortlist(keys):
            retrieval_started_at = time.perf_counter()
            shortlist = await _production_shortlist(
                case,
                ontology_keys=keys,
                as_of=as_of,
            )
            return shortlist, (time.perf_counter() - retrieval_started_at) * 1000

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

        baseline_documents = _ranked_document_keys(
            baseline_shortlist,
            identity_map,
        )
        blended_documents = _ranked_document_keys(
            blended_shortlist,
            identity_map,
        )
        baseline_set = set(baseline_documents)
        blended_set = set(blended_documents)
        added = [key for key in blended_documents if key not in baseline_set]
        removed = [key for key in baseline_documents if key not in blended_set]
        changed = baseline_documents != blended_documents
        changed_cases += int(changed)
        added_documents += len(added)
        removed_documents += len(removed)
        details.append(
            {
                "question_hash": case.question_hash,
                "question": redact_question(case.question),
                "dataset": case.dataset,
                "occurrences": case.occurrences,
                "last_seen": case.last_seen.isoformat() if case.last_seen else None,
                "as_of": as_of.isoformat(),
                "linked_entities": [
                    entity.as_dict() for entity in result.linked_entities
                ],
                "ontology_candidate_documents": list(
                    ontology_keys.get(case.dataset, ())
                ),
                "baseline_documents": baseline_documents,
                "blended_documents": blended_documents,
                "added_documents": added,
                "removed_documents": removed,
                "changed": changed,
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

    return {
        "contract": {
            "evaluation_level": "production candidate shortlist before evidence selection",
            "datasets": sorted(BLEND_DATASETS),
            "document_limit": document_limit,
            "feature_flag_mutated": False,
            "answer_model_called": False,
            "relevance_labels_available": False,
        },
        "selected_cases": len(selected),
        "graph_candidate_cases": graph_candidate_cases,
        "changed_cases": changed_cases,
        "added_documents": added_documents,
        "removed_documents": removed_documents,
        "latency": {
            "ontology_traversal": _latency_summary(ontology_latencies),
            "baseline_retrieval_and_shortlist": _latency_summary(
                baseline_latencies
            ),
            "blended_retrieval_and_shortlist": _latency_summary(
                blended_latencies
            ),
        },
        "cases": details,
    }


def _document_catalog(session, report: dict) -> dict[str, dict[str, str]]:
    keys = {
        key
        for case in report["cases"]
        for field in (
            "ontology_candidate_documents",
            "baseline_documents",
            "blended_documents",
        )
        for key in case[field]
    }
    rows = (
        session.query(SourceDocument)
        .filter(SourceDocument.document_key.in_(tuple(keys)))
        .all()
        if keys
        else []
    )
    return {
        row.document_key: {
            "title": row.title or "",
            "source_url": row.source_url or "",
        }
        for row in rows
    }


def write_reports(output_dir: Path, selection: dict, report: dict, catalog: dict):
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    comparison_path = output_dir / "comparison.json"
    review_path = output_dir / "changed_documents_review.csv"
    summary_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().astimezone().isoformat(),
                "privacy": "questions are locally redacted before report output",
                "selection": selection,
                **{key: value for key, value in report.items() if key != "cases"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    comparison_path.write_text(
        json.dumps(report["cases"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with review_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "question_hash",
                "question",
                "dataset",
                "change",
                "document_key",
                "document_title",
                "source_url",
                "relevance",
                "review_note",
            ),
        )
        writer.writeheader()
        for case in report["cases"]:
            for change, field in (
                ("added", "added_documents"),
                ("removed", "removed_documents"),
            ):
                for document_key in case[field]:
                    metadata = catalog.get(document_key, {})
                    writer.writerow(
                        {
                            "question_hash": case["question_hash"],
                            "question": case["question"],
                            "dataset": case["dataset"],
                            "change": change,
                            "document_key": document_key,
                            "document_title": metadata.get("title", ""),
                            "source_url": metadata.get("source_url", ""),
                            "relevance": "",
                            "review_note": "",
                        }
                    )
    return summary_path, comparison_path, review_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay accumulated questions through ontology candidate blending"
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--document-limit", type=int, default=2)
    parser.add_argument("--include-synthetic", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir or DEFAULT_OUTPUT_ROOT / f"{stamp}-candidate-history"
    session = SessionLocal()
    try:
        rows = session.query(RagQueryLog).order_by(RagQueryLog.id.asc()).all()
        cases, selection = select_history_questions(
            rows,
            include_synthetic=args.include_synthetic,
        )
        report = asyncio.run(
            evaluate_history_candidate_blend(
                session,
                cases,
                document_limit=max(1, min(args.document_limit, 3)),
                limit=args.limit,
            )
        )
        catalog = _document_catalog(session, report)
        paths = write_reports(output_dir, selection, report, catalog)
    finally:
        session.close()

    print("Ontology candidate history evaluation")
    print(
        f"  selected={report['selected_cases']}, "
        f"graph_candidates={report['graph_candidate_cases']}, "
        f"changed={report['changed_cases']}"
    )
    print(
        f"  shortlist changes: added={report['added_documents']}, "
        f"removed={report['removed_documents']}"
    )
    print(
        "  latency p50/p95: "
        f"ontology={report['latency']['ontology_traversal'].get('p50_ms', 0):.2f}/"
        f"{report['latency']['ontology_traversal'].get('p95_ms', 0):.2f}ms, "
        f"baseline={report['latency']['baseline_retrieval_and_shortlist'].get('p50_ms', 0):.2f}/"
        f"{report['latency']['baseline_retrieval_and_shortlist'].get('p95_ms', 0):.2f}ms, "
        f"blended={report['latency']['blended_retrieval_and_shortlist'].get('p50_ms', 0):.2f}/"
        f"{report['latency']['blended_retrieval_and_shortlist'].get('p95_ms', 0):.2f}ms"
    )
    for path in paths:
        print(f"  output: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
