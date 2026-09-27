"""Compare existing hybrid retrieval with ontology shadow candidates.

This evaluator is deliberately document-level.  The production retrieval
stack ranks chunks, while ontology evidence points to canonical
``SourceDocument.document_key`` values.  Hybrid hits are therefore deduplicated
to document keys before both paths are scored against the same qrels.

The fixture is a structural contract, not a human relevance study: qrels are
frozen from explicit course/department and public staff/organization fields.
They prove identity, linking, traversal, and candidate recovery.  They do not
authorize graph boosting or claim end-user answer quality.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Callable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.search.hybrid as hybrid  # noqa: E402
from scripts.evaluate_retrieval import (  # noqa: E402
    _frame,
    _lexical,
    aggregate,
    score_case,
)
from src.database import SessionLocal, SourceDocument  # noqa: E402
from src.pipelines.ingest import DATASET_ARTIFACTS  # noqa: E402
from src.services.ontology_retrieval import (  # noqa: E402
    OntologyShadowResult,
    run_ontology_shadow,
)
from src.utils.preprocess import make_doc_id  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_PATH = ROOT / "tests" / "ontology_competency_questions.csv"
QRELS_PATH = ROOT / "tests" / "ontology_qrels.csv"
MIN_COMPETENCY_QUESTIONS = 30


@dataclass(frozen=True)
class CompetencyCase:
    id: str
    question: str
    route: str
    case_type: str
    expected_entity_type: str
    expected_entity_name: str
    expected_predicates: tuple[str, ...]
    note: str = ""


def _split_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(";") if item.strip())


def load_cases(path: Path = QUESTIONS_PATH) -> list[CompetencyCase]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "id",
            "question",
            "route",
            "case_type",
            "expected_entity_type",
            "expected_entity_name",
            "expected_predicates",
        }
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"competency question columns missing: {sorted(missing)}")
        return [
            CompetencyCase(
                id=str(row["id"]).strip(),
                question=str(row["question"]).strip(),
                route=str(row["route"]).strip(),
                case_type=str(row["case_type"]).strip(),
                expected_entity_type=str(row["expected_entity_type"]).strip(),
                expected_entity_name=str(row["expected_entity_name"]).strip(),
                expected_predicates=_split_values(str(row["expected_predicates"])),
                note=str(row.get("note") or "").strip(),
            )
            for row in reader
        ]


def load_document_qrels(
    path: Path = QRELS_PATH,
) -> dict[tuple[str, str], dict[str, int]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"question_id", "dataset", "document_key", "relevance"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"ontology qrels columns missing: {sorted(missing)}")
        qrels: dict[tuple[str, str], dict[str, int]] = {}
        for row in reader:
            key = (
                str(row["question_id"]).strip(),
                str(row["dataset"]).strip(),
            )
            document_key = str(row["document_key"]).strip()
            if not document_key:
                continue
            qrels.setdefault(key, {})[document_key] = int(row["relevance"])
        return qrels


def validate_fixture(
    session,
    cases: Sequence[CompetencyCase],
    qrels: dict[tuple[str, str], dict[str, int]],
    *,
    min_questions: int = MIN_COMPETENCY_QUESTIONS,
) -> list[str]:
    errors: list[str] = []
    case_ids = [case.id for case in cases]
    case_by_id = {case.id: case for case in cases}
    if len(cases) < min_questions:
        errors.append(f"competency question count below {min_questions}: {len(cases)}")
    if len(case_ids) != len(set(case_ids)):
        errors.append("duplicate competency question id")
    for case in cases:
        if not all(
            (
                case.id,
                case.question,
                case.route,
                case.case_type,
                case.expected_entity_type,
                case.expected_entity_name,
                case.expected_predicates,
            )
        ):
            errors.append(f"incomplete competency case: {case.id or '<empty>'}")
        if case.route not in {"courses", "notices", "rules", "schedule", "staff"}:
            errors.append(f"unsupported ontology route: {case.id}={case.route}")

    document_keys = {
        document_key
        for relevance_by_key in qrels.values()
        for document_key in relevance_by_key
    }
    documents = (
        session.query(SourceDocument)
        .filter(SourceDocument.document_key.in_(tuple(document_keys)))
        .all()
        if document_keys
        else []
    )
    document_by_key = {document.document_key: document for document in documents}

    for (question_id, dataset), relevance_by_key in sorted(qrels.items()):
        case = case_by_id.get(question_id)
        if case is None:
            errors.append(f"qrel references unknown question: {question_id}")
            continue
        if dataset != case.route:
            errors.append(
                f"qrel route mismatch: {question_id}={dataset}, case={case.route}"
            )
        if not any(relevance >= 1 for relevance in relevance_by_key.values()):
            errors.append(f"qrel has no positive document: {question_id}/{dataset}")
        for document_key in relevance_by_key:
            document = document_by_key.get(document_key)
            if document is None:
                errors.append(f"qrel document missing from canonical source: {document_key}")
                continue
            if document.dataset != dataset:
                errors.append(
                    f"qrel document dataset mismatch: {document_key}={document.dataset}"
                )
            if document.status not in {"active", "updated"}:
                errors.append(
                    f"qrel document is not published: {document_key}={document.status}"
                )
    return errors


def build_document_identity_map(session) -> dict[str, str]:
    """Map both canonical and pre-canonical course doc IDs to document keys.

    Current course artifacts may have been built directly from the crawler CSV
    before ``document_key`` was attached. Their ``doc_id`` is the deterministic
    SHA1 produced by ``build_course_chunks``. Recomputing that legacy identity
    from the canonical payload lets the evaluator compare the existing index
    without mutating or reindexing it.
    """

    identity_map: dict[str, str] = {}
    documents = (
        session.query(SourceDocument)
        .filter(
            SourceDocument.dataset.in_(
                ("courses", "notices", "rules", "schedule", "staff")
            ),
            SourceDocument.status.in_(("active", "updated")),
        )
        .all()
    )
    for document in documents:
        identity_map[document.document_key] = document.document_key
        if document.dataset != "courses":
            continue
        try:
            payload = json.loads(document.normalized_payload_json or "{}")
        except (TypeError, ValueError):
            continue
        title = next(
            (
                str(payload.get(column) or "").strip()
                for column in (
                    "국문교과목명",
                    "과목명",
                    "course_name",
                    "교과목명",
                    "title",
                    "교과목",
                )
                if str(payload.get(column) or "").strip()
            ),
            "교과목 정보",
        )
        code = str(payload.get("학수번호") or "").strip()
        major_name = str(
            payload.get("major") or payload.get("department_name") or ""
        ).strip()
        curriculum_url = str(
            payload.get("curriculum_url")
            or payload.get("source_url")
            or ""
        ).strip()
        legacy_doc_id = make_doc_id(
            "courses",
            major_name,
            code or title,
            curriculum_url,
            payload.get("section_title", ""),
            payload.get("_source_table"),
        )
        identity_map[legacy_doc_id] = document.document_key
    return identity_map


def _document_key_from_hit(
    row,
    frame_by_chunk: dict[str, str],
    document_identity_map: dict[str, str],
) -> str:
    for column in ("document_key", "doc_id"):
        value = row.get(column)
        if value is not None and str(value).strip():
            identity = str(value).strip()
            return document_identity_map.get(identity, "")
    identity = frame_by_chunk.get(str(row.get("chunk_id")), "")
    return document_identity_map.get(identity, "")


def retrieve_hybrid_documents(
    dataset: str,
    question: str,
    top_k: int,
    *,
    document_identity_map: dict[str, str],
) -> list[str]:
    frame = _frame(dataset)
    if frame.empty:
        return []
    data = _lexical(dataset)
    hits = hybrid.hybrid_search(
        DATASET_ARTIFACTS[dataset].collection,
        frame,
        data["vectorizer"],
        data["matrix"],
        question,
        top_k=max(top_k * 5, top_k),
        tfidf_chunk_ids=data["chunk_ids"],
    )
    if hits.empty:
        return []

    frame_by_chunk: dict[str, str] = {}
    if "chunk_id" in frame.columns:
        for _, row in frame.iterrows():
            chunk_id = str(row.get("chunk_id") or "")
            document_key = str(
                row.get("document_key") or row.get("doc_id") or ""
            ).strip()
            if chunk_id and document_key:
                frame_by_chunk[chunk_id] = document_key

    ranked: list[str] = []
    seen: set[str] = set()
    for _, row in hits.iterrows():
        document_key = _document_key_from_hit(
            row,
            frame_by_chunk,
            document_identity_map,
        )
        if not document_key or document_key in seen:
            continue
        seen.add(document_key)
        ranked.append(document_key)
        if len(ranked) >= top_k:
            break
    return ranked


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


def _relevant_at_k(ranked: Sequence[str], qrels: dict[str, int], k: int) -> set[str]:
    return {key for key in ranked[:k] if qrels.get(key, 0) >= 1}


def evaluate(
    session,
    cases: Sequence[CompetencyCase],
    qrels: dict[tuple[str, str], dict[str, int]],
    *,
    top_k: int = 20,
    baseline_retriever: Callable[..., list[str]] = retrieve_hybrid_documents,
    ontology_runner: Callable[..., OntologyShadowResult] = run_ontology_shadow,
) -> dict:
    baseline_scores = []
    ontology_scores = []
    case_details: list[dict] = []
    baseline_latencies: list[float] = []
    ontology_latencies: list[float] = []
    link_hits = 0
    predicate_hits = 0
    alias_cases = 0
    alias_hits = 0
    union_relevant_at_10 = 0
    total_relevant = 0
    graph_added_relevant_at_10 = 0
    overlap_at_10: list[int] = []

    document_identity_map = build_document_identity_map(session)
    for case in cases:
        started_at = time.perf_counter()
        if baseline_retriever is retrieve_hybrid_documents:
            baseline_ranked = baseline_retriever(
                case.route,
                case.question,
                top_k,
                document_identity_map=document_identity_map,
            )
        else:
            baseline_ranked = baseline_retriever(case.route, case.question, top_k)
        baseline_latencies.append((time.perf_counter() - started_at) * 1000)

        started_at = time.perf_counter()
        result = ontology_runner(
            session,
            case.question,
            [case.route],
            max_hops=2,
            max_entities=8,
            max_relations=100,
            max_documents=top_k,
        )
        ontology_latencies.append((time.perf_counter() - started_at) * 1000)
        ontology_ranked = list(result.document_keys)

        entity_hit = any(
            linked.entity_type == case.expected_entity_type
            and linked.canonical_name == case.expected_entity_name
            for linked in result.linked_entities
        )
        traversed_predicates = {item.predicate for item in result.traversed_relations}
        predicate_hit = set(case.expected_predicates).issubset(traversed_predicates)
        link_hits += int(entity_hit)
        predicate_hits += int(predicate_hit)
        if case.case_type == "alias_to_department":
            alias_cases += 1
            alias_hit = any(
                linked.entity_type == case.expected_entity_type
                and linked.canonical_name == case.expected_entity_name
                and linked.match_method == "alias"
                for linked in result.linked_entities
            )
            alias_hits += int(alias_hit)

        relevance_by_key = qrels.get((case.id, case.route))
        baseline_case = None
        ontology_case = None
        if relevance_by_key:
            baseline_case = score_case(
                question_id=case.id,
                dataset=case.route,
                question=case.question,
                ranked_ids=baseline_ranked,
                relevance_by_id=relevance_by_key,
            )
            ontology_case = score_case(
                question_id=case.id,
                dataset=case.route,
                question=case.question,
                ranked_ids=ontology_ranked,
                relevance_by_id=relevance_by_key,
            )
            baseline_scores.append(baseline_case)
            ontology_scores.append(ontology_case)

            relevant = {
                key for key, relevance in relevance_by_key.items() if relevance >= 1
            }
            baseline_relevant = _relevant_at_k(baseline_ranked, relevance_by_key, 10)
            ontology_relevant = _relevant_at_k(ontology_ranked, relevance_by_key, 10)
            union_relevant_at_10 += len(baseline_relevant | ontology_relevant)
            total_relevant += len(relevant)
            graph_added_relevant_at_10 += len(ontology_relevant - baseline_relevant)
            overlap_at_10.append(
                len(set(baseline_ranked[:10]).intersection(ontology_ranked[:10]))
            )

        case_details.append(
            {
                "id": case.id,
                "route": case.route,
                "case_type": case.case_type,
                "entity_hit": entity_hit,
                "predicate_hit": predicate_hit,
                "linked_entities": [item.as_dict() for item in result.linked_entities],
                "baseline_documents": baseline_ranked,
                "ontology_documents": ontology_ranked,
                "baseline_mrr": None if baseline_case is None else baseline_case.mrr,
                "ontology_mrr": None if ontology_case is None else ontology_case.mrr,
                "baseline_recall_at_10": (
                    None if baseline_case is None else baseline_case.recall[10]
                ),
                "ontology_recall_at_10": (
                    None if ontology_case is None else ontology_case.recall[10]
                ),
            }
        )

    return {
        "contract": {
            "competency_cases": len(cases),
            "ranking_cases": len(baseline_scores),
            "link_accuracy": link_hits / len(cases) if cases else 0.0,
            "predicate_path_accuracy": predicate_hits / len(cases) if cases else 0.0,
            "alias_accuracy": alias_hits / alias_cases if alias_cases else None,
        },
        "baseline": aggregate(baseline_scores),
        "ontology": aggregate(ontology_scores),
        "candidate_comparison": {
            "union_recall_at_10": (
                union_relevant_at_10 / total_relevant if total_relevant else 0.0
            ),
            "graph_added_relevant_at_10": graph_added_relevant_at_10,
            "mean_document_overlap_at_10": (
                statistics.fmean(overlap_at_10) if overlap_at_10 else 0.0
            ),
        },
        "latency": {
            "baseline": _latency_summary(baseline_latencies),
            "ontology": _latency_summary(ontology_latencies),
        },
        "cases": case_details,
    }


def print_report(report: dict) -> None:
    contract = report["contract"]
    baseline = report["baseline"]
    ontology = report["ontology"]
    comparison = report["candidate_comparison"]
    print("Ontology shadow retrieval evaluation")
    alias_accuracy = contract["alias_accuracy"]
    alias_text = "n/a" if alias_accuracy is None else f"{alias_accuracy:.3f}"
    print(
        f"  contract: {contract['competency_cases']} cases, "
        f"link={contract['link_accuracy']:.3f}, "
        f"path={contract['predicate_path_accuracy']:.3f}, "
        f"alias={alias_text}"
    )
    print(f"  ranking cases: {contract['ranking_cases']}")
    print(
        "  baseline: "
        f"recall@10={baseline.get('recall@10', 0):.3f}, "
        f"mrr={baseline.get('mrr', 0):.3f}, "
        f"ndcg@10={baseline.get('ndcg@10', 0):.3f}"
    )
    print(
        "  ontology: "
        f"recall@10={ontology.get('recall@10', 0):.3f}, "
        f"mrr={ontology.get('mrr', 0):.3f}, "
        f"ndcg@10={ontology.get('ndcg@10', 0):.3f}"
    )
    print(
        "  candidates: "
        f"union_recall@10={comparison['union_recall_at_10']:.3f}, "
        f"graph_added_relevant@10={comparison['graph_added_relevant_at_10']}, "
        f"mean_overlap@10={comparison['mean_document_overlap_at_10']:.2f}"
    )
    baseline_latency = report["latency"]["baseline"]
    ontology_latency = report["latency"]["ontology"]
    print(
        "  latency: "
        f"baseline p50/p95={baseline_latency.get('p50_ms', 0):.2f}/"
        f"{baseline_latency.get('p95_ms', 0):.2f}ms, "
        f"ontology p50/p95={ontology_latency.get('p50_ms', 0):.2f}/"
        f"{ontology_latency.get('p95_ms', 0):.2f}ms"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate ontology shadow candidates against canonical document qrels"
    )
    parser.add_argument("--questions", type=Path, default=QUESTIONS_PATH)
    parser.add_argument("--qrels", type=Path, default=QRELS_PATH)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--fail-on-contract", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    cases = load_cases(args.questions)
    qrels = load_document_qrels(args.qrels)
    session = SessionLocal()
    try:
        errors = validate_fixture(session, cases, qrels)
        if errors:
            print(json.dumps({"gate": "failed", "errors": errors}, ensure_ascii=False, indent=2))
            return 1
        if args.validate_only:
            print(
                json.dumps(
                    {
                        "gate": "passed",
                        "competency_cases": len(cases),
                        "qrel_cases": len(qrels),
                        "qrel_documents": sum(len(value) for value in qrels.values()),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        report = evaluate(session, cases, qrels, top_k=max(1, args.top_k))
    finally:
        session.close()

    print_report(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  output: {args.output}")
    if args.fail_on_contract:
        contract = report["contract"]
        required = (
            contract["link_accuracy"],
            contract["predicate_path_accuracy"],
            contract["alias_accuracy"],
        )
        if any(value is None or value < 1.0 for value in required):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
