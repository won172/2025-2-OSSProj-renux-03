"""Compare current Hybrid and ontology retrieval on accumulated RAG questions.

The RAG query table contains real traffic and synthetic evaluation traffic.  This
script follows the product metrics boundary: ``eval_*``/``golden-*`` requests and
requests whose ``as_of`` differs from their log date are excluded by default.

Question text is processed locally and is redacted before being written.  The
default output directory is gitignored because even redacted user questions are
operational data, not a source fixture.  The report does not assign relevance:
ontology-only documents are candidates for a human reviewer to label.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys
import time
from typing import Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.evaluate_ontology_retrieval import (  # noqa: E402
    build_document_identity_map,
    retrieve_hybrid_documents,
)
from scripts.evaluate_retrieval import _frame  # noqa: E402
from src.database import (  # noqa: E402
    RagQueryLog,
    RagRetrievalLog,
    SessionLocal,
    SourceDocument,
)
from src.services.ontology_retrieval import run_ontology_shadow  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "artifacts" / "ontology_evaluations"
SYNTHETIC_REQUEST_PREFIXES = ("eval_", "golden-")
ONTOLOGY_ROUTES = ("courses", "notices", "rules", "schedule", "staff")

_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?!\w)")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?82[-.\s]?)?(?:0\d{1,2}[-.\s]?)?\d{3,4}[-.\s]?\d{4}(?!\d)"
)
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{7,}(?!\d)")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class HistoryQuestion:
    question: str
    question_hash: str
    dataset: str
    occurrences: int
    first_seen: datetime | None
    last_seen: datetime | None
    latest_log_id: int


def normalized_question(value: object) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "")).strip()


def is_malformed_question(question: str) -> bool:
    normalized = normalized_question(question)
    if len(normalized) > 2000:
        return True
    machine_dump_markers = ("chunkId", "finalScore", "hybridScore", "snippet")
    return normalized.startswith(": {source:") and sum(
        marker in normalized for marker in machine_dump_markers
    ) >= 2


def question_hash(question: str) -> str:
    normalized = normalized_question(question).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def redact_question(question: str) -> str:
    redacted = _EMAIL_RE.sub("[EMAIL]", normalized_question(question))
    redacted = _PHONE_RE.sub("[PHONE]", redacted)
    return _LONG_NUMBER_RE.sub("[LONG_NUMBER]", redacted)


def parse_route(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        values = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return ()
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = [part.strip() for part in raw.split(",")]
        values = parsed if isinstance(parsed, list) else [parsed]
    return tuple(
        route
        for route in (str(item).strip() for item in values)
        if route in ONTOLOGY_ROUTES
    )


def is_real_traffic(row: RagQueryLog) -> bool:
    request_id = str(row.request_id or "")
    if request_id.startswith(SYNTHETIC_REQUEST_PREFIXES):
        return False
    as_of = str(row.as_of or "").strip()
    if not as_of:
        return True
    created_at = row.created_at
    created_date = created_at.date().isoformat() if created_at else ""
    return as_of == created_date


def _in_date_range(
    created_at: datetime | None,
    since: date | None,
    until: date | None,
) -> bool:
    if created_at is None:
        return since is None and until is None
    created_date = created_at.date()
    if since is not None and created_date < since:
        return False
    if until is not None and created_date > until:
        return False
    return True


def select_history_questions(
    rows: Sequence[RagQueryLog],
    *,
    since: date | None = None,
    until: date | None = None,
    include_synthetic: bool = False,
) -> tuple[list[HistoryQuestion], dict[str, int]]:
    counters = {
        "total_logs": len(rows),
        "synthetic_or_shifted_logs": 0,
        "real_logs": 0,
        "real_logs_in_date_range": 0,
        "ontology_route_logs": 0,
        "blank_question_logs": 0,
        "malformed_question_logs": 0,
    }
    grouped: dict[tuple[str, str], dict] = {}
    real_question_hashes: set[str] = set()

    for row in rows:
        real = is_real_traffic(row)
        if not real:
            counters["synthetic_or_shifted_logs"] += 1
            if not include_synthetic:
                continue
        else:
            counters["real_logs"] += 1
        if not _in_date_range(row.created_at, since, until):
            continue
        if real:
            counters["real_logs_in_date_range"] += 1

        question = normalized_question(row.question)
        if not question:
            counters["blank_question_logs"] += 1
            continue
        if is_malformed_question(question):
            counters["malformed_question_logs"] += 1
            continue
        digest = question_hash(question)
        if real:
            real_question_hashes.add(digest)
        routes = parse_route(row.route)
        if not routes:
            continue
        counters["ontology_route_logs"] += 1

        for dataset in routes:
            key = (digest, dataset)
            bucket = grouped.setdefault(
                key,
                {
                    "question": question,
                    "occurrences": 0,
                    "first_seen": row.created_at,
                    "last_seen": row.created_at,
                    "latest_log_id": row.id,
                },
            )
            bucket["occurrences"] += 1
            if row.created_at is not None and (
                bucket["first_seen"] is None
                or row.created_at < bucket["first_seen"]
            ):
                bucket["first_seen"] = row.created_at
            if row.created_at is not None and (
                bucket["last_seen"] is None
                or row.created_at >= bucket["last_seen"]
            ):
                bucket["last_seen"] = row.created_at
                bucket["latest_log_id"] = row.id
                bucket["question"] = question

    counters["unique_real_questions"] = len(real_question_hashes)
    counters["unique_ontology_route_cases"] = len(grouped)
    selected = [
        HistoryQuestion(
            question=value["question"],
            question_hash=digest,
            dataset=dataset,
            occurrences=value["occurrences"],
            first_seen=value["first_seen"],
            last_seen=value["last_seen"],
            latest_log_id=value["latest_log_id"],
        )
        for (digest, dataset), value in grouped.items()
    ]
    selected.sort(
        key=lambda item: (
            item.last_seen or datetime.min,
            item.question_hash,
            item.dataset,
        ),
        reverse=True,
    )
    return selected, counters


def _canonical_logged_documents(
    retrievals: Iterable[RagRetrievalLog],
    *,
    dataset: str,
    identity_map: dict[str, str],
    chunk_document_map: dict[str, str],
    top_k: int,
) -> list[str]:
    ranked: list[str] = []
    seen: set[str] = set()
    for retrieval in sorted(retrievals, key=lambda item: (item.rank or 10**9, item.id)):
        if retrieval.dataset != dataset:
            continue
        identity = str(retrieval.document_key or "").strip()
        document_key = identity_map.get(identity, "")
        if not document_key:
            # Logs created before RagRetrievalLog.document_key was introduced
            # retain only chunk_id. Resolve it through the current projection
            # when that chunk still exists; unresolved historical chunks remain
            # explicitly unavailable rather than being guessed from titles.
            document_key = chunk_document_map.get(
                str(retrieval.chunk_id or "").strip(),
                "",
            )
        if not document_key or document_key in seen:
            continue
        seen.add(document_key)
        ranked.append(document_key)
        if len(ranked) >= top_k:
            break
    return ranked


def build_chunk_document_map(identity_map: dict[str, str]) -> dict[str, str]:
    """Resolve current chunk IDs to canonical documents without index mutation."""

    result: dict[str, str] = {}
    for dataset in ONTOLOGY_ROUTES:
        frame = _frame(dataset)
        if frame.empty or "chunk_id" not in frame.columns:
            continue
        for _, row in frame.iterrows():
            chunk_id = str(row.get("chunk_id") or "").strip()
            identity = str(
                row.get("document_key") or row.get("doc_id") or ""
            ).strip()
            document_key = identity_map.get(identity, "")
            if chunk_id and document_key:
                result[chunk_id] = document_key
    return result


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


def evaluate_history(
    session,
    cases: Sequence[HistoryQuestion],
    *,
    top_k: int = 10,
    limit: int | None = None,
) -> dict:
    if limit is not None:
        cases = list(cases)[: max(0, limit)]

    identity_map = build_document_identity_map(session)
    chunk_document_map = build_chunk_document_map(identity_map)
    log_ids = [case.latest_log_id for case in cases]
    retrieval_rows = (
        session.query(RagRetrievalLog)
        .filter(RagRetrievalLog.query_log_id.in_(tuple(log_ids)))
        .all()
        if log_ids
        else []
    )
    retrievals_by_log: dict[int, list[RagRetrievalLog]] = {}
    for row in retrieval_rows:
        retrievals_by_log.setdefault(row.query_log_id, []).append(row)

    details: list[dict] = []
    hybrid_latencies: list[float] = []
    ontology_latencies: list[float] = []
    ontology_linked_cases = 0
    ontology_document_cases = 0
    ontology_only_cases = 0
    overlap_sizes: list[int] = []
    overlap_sizes_with_ontology: list[int] = []
    ontology_only_total = 0
    ontology_document_total = 0
    hybrid_document_total = 0
    logged_document_cases = 0

    for case in cases:
        started_at = time.perf_counter()
        ontology_result = run_ontology_shadow(
            session,
            case.question,
            [case.dataset],
            max_hops=2,
            max_entities=8,
            max_relations=100,
            max_documents=top_k,
        )
        ontology_latency = (time.perf_counter() - started_at) * 1000
        ontology_latencies.append(ontology_latency)

        ontology_documents = list(ontology_result.document_keys)
        if ontology_result.linked_entities:
            ontology_linked_cases += 1
        if ontology_documents:
            ontology_document_cases += 1
            ontology_document_total += len(ontology_documents)

        started_at = time.perf_counter()
        hybrid_documents = retrieve_hybrid_documents(
            case.dataset,
            case.question,
            top_k,
            document_identity_map=identity_map,
        )
        hybrid_latency = (time.perf_counter() - started_at) * 1000
        hybrid_latencies.append(hybrid_latency)

        logged_documents = _canonical_logged_documents(
            retrievals_by_log.get(case.latest_log_id, ()),
            dataset=case.dataset,
            identity_map=identity_map,
            chunk_document_map=chunk_document_map,
            top_k=top_k,
        )
        logged_document_cases += int(bool(logged_documents))
        hybrid_document_total += len(hybrid_documents)
        hybrid_set = set(hybrid_documents)
        ontology_set = set(ontology_documents)
        overlap = hybrid_set.intersection(ontology_set)
        ontology_only = [
            document_key
            for document_key in ontology_documents
            if document_key not in hybrid_set
        ]
        if ontology_only:
            ontology_only_cases += 1
        ontology_only_total += len(ontology_only)
        overlap_sizes.append(len(overlap))
        if ontology_documents:
            overlap_sizes_with_ontology.append(len(overlap))

        details.append(
            {
                "question_hash": case.question_hash,
                "question": redact_question(case.question),
                "dataset": case.dataset,
                "occurrences": case.occurrences,
                "first_seen": case.first_seen.isoformat() if case.first_seen else None,
                "last_seen": case.last_seen.isoformat() if case.last_seen else None,
                "latest_log_id": case.latest_log_id,
                "linked_entities": [
                    item.as_dict() for item in ontology_result.linked_entities
                ],
                "predicates": sorted(
                    {item.predicate for item in ontology_result.traversed_relations}
                ),
                "logged_documents": logged_documents,
                "hybrid_documents": hybrid_documents,
                "ontology_documents": ontology_documents,
                "overlap_documents": sorted(overlap),
                "ontology_only_documents": ontology_only,
                "hybrid_latency_ms": round(hybrid_latency, 2),
                "ontology_latency_ms": round(ontology_latency, 2),
            }
        )

    return {
        "evaluated_cases": len(details),
        "ontology_linked_cases": ontology_linked_cases,
        "ontology_document_cases": ontology_document_cases,
        "logged_document_cases": logged_document_cases,
        "ontology_only_cases": ontology_only_cases,
        "hybrid_documents": hybrid_document_total,
        "ontology_documents": ontology_document_total,
        "ontology_only_documents": ontology_only_total,
        "mean_overlap_documents_at_k": (
            round(statistics.fmean(overlap_sizes), 3) if overlap_sizes else 0.0
        ),
        "mean_overlap_ontology_cases_at_k": (
            round(statistics.fmean(overlap_sizes_with_ontology), 3)
            if overlap_sizes_with_ontology
            else 0.0
        ),
        "latency": {
            "hybrid": _latency_summary(hybrid_latencies),
            "ontology": _latency_summary(ontology_latencies),
        },
        "cases": details,
    }


def _document_catalog(session, report: dict) -> dict[str, dict[str, str]]:
    keys = {
        key
        for case in report["cases"]
        for field in (
            "logged_documents",
            "hybrid_documents",
            "ontology_documents",
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
            "source_type": row.source_type or "",
            "source_url": row.source_url or "",
        }
        for row in rows
    }


def write_reports(
    output_dir: Path,
    selection: dict[str, int],
    report: dict,
    document_catalog: dict[str, dict[str, str]],
    *,
    top_k: int,
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    comparison_path = output_dir / "comparison.csv"
    review_path = output_dir / "human_review.csv"

    safe_summary = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "privacy": (
            "question text masks email, phone, and long numeric patterns; "
            "question identifiers are SHA-256 hashes; person names are not classified"
        ),
        "top_k": top_k,
        "selection": selection,
        **{key: value for key, value in report.items() if key != "cases"},
    }
    summary_path.write_text(
        json.dumps(safe_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    comparison_fields = [
        "question_hash",
        "question",
        "dataset",
        "occurrences",
        "first_seen",
        "last_seen",
        "latest_log_id",
        "linked_entities_json",
        "predicates",
        "logged_documents_json",
        "hybrid_documents_json",
        "ontology_documents_json",
        "overlap_count",
        "ontology_only_count",
        "hybrid_latency_ms",
        "ontology_latency_ms",
    ]
    with comparison_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=comparison_fields)
        writer.writeheader()
        for case in report["cases"]:
            writer.writerow(
                {
                    "question_hash": case["question_hash"],
                    "question": case["question"],
                    "dataset": case["dataset"],
                    "occurrences": case["occurrences"],
                    "first_seen": case["first_seen"],
                    "last_seen": case["last_seen"],
                    "latest_log_id": case["latest_log_id"],
                    "linked_entities_json": json.dumps(
                        case["linked_entities"], ensure_ascii=False
                    ),
                    "predicates": ";".join(case["predicates"]),
                    "logged_documents_json": json.dumps(
                        case["logged_documents"], ensure_ascii=False
                    ),
                    "hybrid_documents_json": json.dumps(
                        case["hybrid_documents"], ensure_ascii=False
                    ),
                    "ontology_documents_json": json.dumps(
                        case["ontology_documents"], ensure_ascii=False
                    ),
                    "overlap_count": len(case["overlap_documents"]),
                    "ontology_only_count": len(case["ontology_only_documents"]),
                    "hybrid_latency_ms": case["hybrid_latency_ms"],
                    "ontology_latency_ms": case["ontology_latency_ms"],
                }
            )

    review_fields = [
        "question_hash",
        "question",
        "dataset",
        "occurrences",
        "candidate_origin",
        "document_key",
        "document_title",
        "source_type",
        "source_url",
        "relevance",
        "review_note",
    ]
    with review_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=review_fields)
        writer.writeheader()
        for case in report["cases"]:
            hybrid_set = set(case["hybrid_documents"])
            ontology_set = set(case["ontology_documents"])
            ordered_keys = list(case["hybrid_documents"])
            ordered_keys.extend(
                key for key in case["ontology_documents"] if key not in hybrid_set
            )
            for document_key in ordered_keys:
                metadata = document_catalog.get(document_key, {})
                origin = (
                    "both"
                    if document_key in hybrid_set and document_key in ontology_set
                    else "hybrid"
                    if document_key in hybrid_set
                    else "ontology"
                )
                writer.writerow(
                    {
                        "question_hash": case["question_hash"],
                        "question": case["question"],
                        "dataset": case["dataset"],
                        "occurrences": case["occurrences"],
                        "candidate_origin": origin,
                        "document_key": document_key,
                        "document_title": metadata.get("title", ""),
                        "source_type": metadata.get("source_type", ""),
                        "source_url": metadata.get("source_url", ""),
                        "relevance": "",
                        "review_note": "",
                    }
                )
    return summary_path, comparison_path, review_path


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare Hybrid and ontology retrieval on accumulated RAG questions"
    )
    parser.add_argument("--since", help="inclusive log date, YYYY-MM-DD")
    parser.add_argument("--until", help="inclusive log date, YYYY-MM-DD")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--datasets",
        nargs="+",
        choices=ONTOLOGY_ROUTES,
        help="evaluate only the selected ontology routes",
    )
    parser.add_argument("--include-synthetic", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir or DEFAULT_OUTPUT_ROOT / stamp
    session = SessionLocal()
    try:
        rows = session.query(RagQueryLog).order_by(RagQueryLog.id.asc()).all()
        cases, selection = select_history_questions(
            rows,
            since=_parse_date(args.since),
            until=_parse_date(args.until),
            include_synthetic=args.include_synthetic,
        )
        if args.datasets:
            selected_datasets = set(args.datasets)
            cases = [case for case in cases if case.dataset in selected_datasets]
            selection["selected_datasets"] = sorted(selected_datasets)
            selection["selected_cases_after_dataset_filter"] = len(cases)
        report = evaluate_history(
            session,
            cases,
            top_k=max(1, args.top_k),
            limit=args.limit,
        )
        catalog = _document_catalog(session, report)
        paths = write_reports(
            output_dir,
            selection,
            report,
            catalog,
            top_k=max(1, args.top_k),
        )
    finally:
        session.close()

    print("Ontology history evaluation")
    print(
        f"  logs: total={selection['total_logs']}, "
        f"real={selection['real_logs']}, "
        f"unique_real={selection['unique_real_questions']}"
    )
    print(
        f"  cases: eligible={selection['unique_ontology_route_cases']}, "
        f"evaluated={report['evaluated_cases']}, "
        f"linked={report['ontology_linked_cases']}, "
        f"with_documents={report['ontology_document_cases']}"
    )
    print(
        f"  comparison: ontology_only_cases={report['ontology_only_cases']}, "
        f"ontology_only_documents={report['ontology_only_documents']}, "
        f"logged_document_cases={report['logged_document_cases']}, "
        f"mean_overlap_ontology_cases@{max(1, args.top_k)}="
        f"{report['mean_overlap_ontology_cases_at_k']:.2f}"
    )
    print(
        "  latency: "
        f"hybrid p50/p95={report['latency']['hybrid'].get('p50_ms', 0):.2f}/"
        f"{report['latency']['hybrid'].get('p95_ms', 0):.2f}ms, "
        f"ontology p50/p95={report['latency']['ontology'].get('p50_ms', 0):.2f}/"
        f"{report['latency']['ontology'].get('p95_ms', 0):.2f}ms"
    )
    for path in paths:
        print(f"  output: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
