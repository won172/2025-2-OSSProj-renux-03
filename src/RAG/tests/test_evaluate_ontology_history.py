from __future__ import annotations

from datetime import datetime
from pathlib import Path
import csv
import json

from scripts.evaluate_ontology_history import (
    HistoryQuestion,
    _canonical_logged_documents,
    is_real_traffic,
    is_malformed_question,
    parse_route,
    question_hash,
    redact_question,
    select_history_questions,
    write_reports,
)
from src.database import RagQueryLog, RagRetrievalLog


def _log(
    log_id: int,
    question: str,
    route: str,
    *,
    request_id: str = "request-real",
    as_of: str | None = None,
    created_at: datetime = datetime(2026, 8, 27, 9, 0, 0),
) -> RagQueryLog:
    return RagQueryLog(
        id=log_id,
        request_id=request_id,
        question=question,
        route=route,
        as_of=as_of,
        created_at=created_at,
    )


def test_real_traffic_boundary_matches_product_metrics_rules():
    assert is_real_traffic(_log(1, "질문", '["courses"]'))
    assert not is_real_traffic(
        _log(2, "질문", '["courses"]', request_id="eval_fixture")
    )
    assert not is_real_traffic(
        _log(3, "질문", '["courses"]', request_id="golden-fixture")
    )
    assert is_real_traffic(
        _log(4, "질문", '["courses"]', as_of="2026-08-27")
    )
    assert not is_real_traffic(
        _log(5, "질문", '["courses"]', as_of="2030-01-01")
    )


def test_route_parser_keeps_only_ontology_supported_routes():
    assert parse_route('["notices", "courses", "schedule", "staff"]') == (
        "notices",
        "courses",
        "schedule",
        "staff",
    )
    assert parse_route("courses, notices") == ("courses", "notices")
    assert parse_route(None) == ()


def test_selection_deduplicates_questions_per_route_and_keeps_latest_log():
    rows = [
        _log(
            1,
            "  컴퓨터·AI학부   과목 알려줘 ",
            '["courses"]',
            created_at=datetime(2026, 8, 25, 9, 0, 0),
        ),
        _log(
            2,
            "컴퓨터·AI학부 과목 알려줘",
            '["courses"]',
            created_at=datetime(2026, 8, 27, 9, 0, 0),
        ),
        _log(3, "오늘 학식", '["meals"]'),
        _log(4, "합성 질문", '["courses"]', request_id="eval_1"),
    ]

    cases, counters = select_history_questions(rows)

    assert len(cases) == 1
    assert cases[0].occurrences == 2
    assert cases[0].latest_log_id == 2
    assert cases[0].question == "컴퓨터·AI학부 과목 알려줘"
    assert counters["total_logs"] == 4
    assert counters["real_logs"] == 3
    assert counters["synthetic_or_shifted_logs"] == 1
    assert counters["unique_real_questions"] == 2


def test_question_output_is_stable_and_redacts_direct_identifiers():
    raw = "연락처 010-1234-5678, mail student@example.com, 학번 2026123456"
    assert question_hash(raw) == question_hash(f"  {raw}  ")
    redacted = redact_question(raw)
    assert "010-1234-5678" not in redacted
    assert "student@example.com" not in redacted
    assert "2026123456" not in redacted
    assert "[PHONE]" in redacted
    assert "[EMAIL]" in redacted


def test_machine_result_dump_is_not_treated_as_a_user_question():
    dump = ': {source: "notices", chunkId: "abc", finalScore: 0.7, snippet: "x"}'
    assert is_malformed_question(dump)
    assert is_malformed_question("질문 " + "가" * 2001)
    assert not is_malformed_question("통계학과 행사 공지 올라온 거 있어?")


def test_legacy_logged_retrieval_uses_chunk_lineage_when_document_key_is_missing():
    rows = [
        RagRetrievalLog(
            id=1,
            query_log_id=1,
            rank=1,
            dataset="courses",
            chunk_id="courses:legacy:0",
            document_key=None,
        )
    ]

    assert _canonical_logged_documents(
        rows,
        dataset="courses",
        identity_map={"courses:canonical": "courses:canonical"},
        chunk_document_map={"courses:legacy:0": "courses:canonical"},
        top_k=10,
    ) == ["courses:canonical"]


def test_reports_are_redacted_and_create_blank_human_labels(tmp_path: Path):
    case = HistoryQuestion(
        question="컴퓨터·AI학부 담당자 010-1234-5678",
        question_hash="hash-1",
        dataset="staff",
        occurrences=2,
        first_seen=datetime(2026, 8, 20),
        last_seen=datetime(2026, 8, 27),
        latest_log_id=7,
    )
    report = {
        "evaluated_cases": 1,
        "ontology_linked_cases": 1,
        "ontology_document_cases": 1,
        "logged_document_cases": 0,
        "ontology_only_cases": 1,
        "hybrid_documents": 1,
        "ontology_documents": 2,
        "ontology_only_documents": 1,
        "mean_overlap_documents_at_k": 0.0,
        "mean_overlap_ontology_cases_at_k": 1.0,
        "latency": {"hybrid": {}, "ontology": {}},
        "cases": [
            {
                "question_hash": case.question_hash,
                "question": redact_question(case.question),
                "dataset": case.dataset,
                "occurrences": case.occurrences,
                "first_seen": case.first_seen.isoformat(),
                "last_seen": case.last_seen.isoformat(),
                "latest_log_id": case.latest_log_id,
                "linked_entities": [],
                "predicates": ["WORKS_AT"],
                "logged_documents": [],
                "hybrid_documents": ["staff:shared"],
                "ontology_documents": ["staff:shared", "staff:ontology"],
                "overlap_documents": ["staff:shared"],
                "ontology_only_documents": ["staff:ontology"],
                "hybrid_latency_ms": 10.0,
                "ontology_latency_ms": 2.0,
            }
        ],
    }
    summary, comparison, review = write_reports(
        tmp_path,
        {"total_logs": 1},
        report,
        {
            "staff:shared": {"title": "공통", "source_type": "staff"},
            "staff:ontology": {"title": "추가", "source_type": "staff"},
        },
        top_k=10,
    )

    assert json.loads(summary.read_text(encoding="utf-8"))["evaluated_cases"] == 1
    assert "010-1234-5678" not in comparison.read_text(encoding="utf-8")
    with review.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["candidate_origin"] for row in rows] == ["both", "ontology"]
    assert all(row["relevance"] == "" for row in rows)
