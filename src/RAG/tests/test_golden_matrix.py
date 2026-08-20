from __future__ import annotations

import hashlib
import json
import sys
import threading
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker


RAG_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = RAG_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evaluate_golden_matrix import evaluate, load_results, source_fingerprint  # noqa: E402
from golden_matrix import (  # noqa: E402
    DOMAINS,
    MIN_PER_DOMAIN,
    MIN_QUESTIONS,
    GoldenCase,
    load_matrix,
    load_taxonomy,
    main as validate_main,
    validate_matrix,
)
from run_golden_matrix import _result_from_response, main as runner_main  # noqa: E402
from src.services.source_contract import source_reference  # noqa: E402
from verify_golden_replay import main as replay_main  # noqa: E402


MATRIX = RAG_ROOT / "tests" / "golden_matrix.csv"
TAXONOMY = RAG_ROOT / "tests" / "golden_taxonomy.v1.json"
RESULT_SCHEMA = RAG_ROOT / "tests" / "golden_result.schema.json"
RUN_MANIFEST_SCHEMA = RAG_ROOT / "tests" / "golden_run_manifest.schema.json"
RUN_AT = datetime(2026, 7, 20, tzinfo=timezone.utc)


def _source(dataset: str, campus: str, number: int, case_id: str) -> dict:
    snippet = f"{case_id}의 {dataset} 공식 근거이며 {campus} 캠퍼스에 적용되는 안내입니다."
    source = {
        "id": "",
        "dataset": dataset,
        "source_type": dataset,
        "chunk_id": f"{case_id}-{dataset}-{campus}-{number}",
        "url": f"https://www.dongguk.edu/golden/{case_id.lower()}/{number}",
        "campus_scope": campus,
        "published_at": "2026-07-20",
        "effective_date": "2026-07-20",
        "snippet": snippet,
        "snippet_hash": hashlib.sha256(snippet.encode()).hexdigest(),
        "citation_number": number,
        "locator": f"golden-{case_id}-{number}",
    }
    source["id"] = source_fingerprint(source)
    return source


def _passing_result(case: GoldenCase) -> dict:
    sources = []
    if case.answerability == "answerable":
        datasets = list(case.expected_datasets) if "cross_domain" in case.case_types else [case.expected_datasets[0]]
        campuses = list(case.required_campuses) if case.campus_requirement_mode == "all" else [case.required_campuses[0]]
        number = 1
        for dataset in datasets:
            for campus in campuses:
                sources.append(_source(dataset, campus, number, case.id))
                number += 1
    elif case.source_requirement == "official_contact":
        sources = [_source("staff", case.required_campuses[0], 1, case.id)]

    if case.answerability == "answerable":
        answer = " ".join(
            f"공식 근거를 확인한 결과 {keyword} 항목은 학교 안내에 따라 적용됩니다 [1]."
            for keyword in case.required_keywords
        )
        answer += " 세부 조건은 연결된 원문과 적용 날짜를 함께 확인해 주세요 [1]."
    elif case.answerability == "needs_clarification":
        answer = f"정확히 안내하려면 {' 그리고 '.join(case.clarification_fields)} 정보를 알려주시겠어요?"
    else:
        answer = (
            f"요청하신 내용은 {case.refusal_markers[0]} 사유로 제공할 수 없습니다. "
            "민감한 정보를 보호하기 위해 공식 담당 부서에서 본인이 직접 확인해 주세요"
            + (" [1]." if sources else ".")
        )

    if not sources or case.followup_policy == "none":
        followups = []
    elif case.followup_policy == "clarify":
        followups = [{"question": f"{' 그리고 '.join(case.clarification_fields)} 정보를 알려주시겠어요?", "source_refs": []}]
    elif case.followup_policy == "official_contact":
        followups = [{"question": "공식 담당 부서에 문의하는 방법을 확인할까요?", "source_refs": [sources[0]["id"]] if sources else []}]
    else:
        followups = [{"question": f"{case.required_keywords[0]} 신청 조건도 확인할까요?", "source_refs": [sources[0]["id"]]}]

    return {
        "schema_version": 2,
        "status": "ok",
        "id": case.id,
        "question_hash": hashlib.sha256(case.question.encode()).hexdigest(),
        "response_received": True,
        "http_status": 200,
        "latency_ms": 123,
        "request_id": f"request-{case.id}",
        "actual_app_intents": list(case.expected_app_intents),
        "answer": answer,
        "citations_text": "[1] https://www.dongguk.edu/golden/source" if sources else "",
        "sources": sources,
        "followups": followups,
        "fallback_triggered": False,
        "fallback_reason": None,
        "grounded": True if sources else None,
        "grounding_score": 1.0 if sources else None,
        "error": None,
    }


@pytest.fixture(scope="module")
def taxonomy():
    return load_taxonomy(TAXONOMY)


@pytest.fixture(scope="module")
def golden_cases() -> list[GoldenCase]:
    return load_matrix(MATRIX)


@pytest.fixture()
def passing_results(golden_cases):
    return [_passing_result(case) for case in golden_cases]


def test_matrix_has_versioned_taxonomy_and_all_required_case_types(golden_cases, taxonomy):
    assert validate_matrix(golden_cases, taxonomy) == []
    counts = Counter(case.domain for case in golden_cases)
    assert len(golden_cases) >= MIN_QUESTIONS
    assert set(counts) == set(DOMAINS)
    assert all(count >= MIN_PER_DOMAIN for count in counts.values())
    required_types = {
        "basic", "ambiguous", "date", "department_or_facility", "typo",
        "cross_domain", "not_answerable", "wise_boundary",
    }
    for domain in DOMAINS:
        actual = {case_type for case in golden_cases if case.domain == domain for case_type in case.case_types}
        assert required_types <= actual


def test_matrix_specific_qa_corrections(golden_cases):
    cases = {case.id: case for case in golden_cases}
    assert "법학관에서 학생이" in cases["CL-009"].question
    assert "ambiguous" in cases["CL-014"].case_types
    assert "ambiguous" in cases["DN-011"].case_types
    for case_id in ("DN-003", "HS-009", "PS-005"):
        assert cases[case_id].required_campuses == ("bmc",)
        assert cases[case_id].allowed_campuses == ("bmc",)
    basket = cases["RG-001"]
    assert basket.expected_app_intents == ("rules",)
    assert basket.expected_datasets == ("rules",)
    assert basket.expected_source_types == ("entry_year_guide_pdf",)
    assert {
        "2026.07.20",
        "07.22",
        "재학",
        "휴학생",
        "변동",
    }.issubset(basket.required_keywords)


def test_validator_rejects_missing_pattern_and_numeric_template(golden_cases, taxonomy):
    domain_without_typo = [
        replace(case, case_types=("basic",)) if case.id == "AC-010" else case
        for case in golden_cases
    ]
    errors = validate_matrix(domain_without_typo, taxonomy)
    assert "domain academic-calendar has no typo case" in errors
    first = replace(golden_cases[0], id="ZZ-001", question="2026학년도 2학기 개강일이 언제야?")
    second = replace(golden_cases[1], id="ZZ-002", question="2027학년도 3학기 개강일이 언제야?")
    errors = validate_matrix([first, second], taxonomy, min_questions=0, min_per_domain=0)
    assert any("numeric-only question template" in error for error in errors)


def test_structural_cli_loads_taxonomy(capsys):
    assert validate_main(["--matrix", str(MATRIX), "--taxonomy", str(TAXONOMY), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True


def test_nested_json_schema_is_actually_enforced(golden_cases, tmp_path):
    result = _passing_result(golden_cases[0])
    del result["sources"][0]["snippet_hash"]
    path = tmp_path / "invalid.jsonl"
    path.write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="snippet_hash"):
        load_results(path, RESULT_SCHEMA)


def test_valid_fixture_passes_four_axes(golden_cases, passing_results):
    summary, details = evaluate(golden_cases, passing_results, run_at=RUN_AT)
    failures = [detail for detail in details if not detail["all_axes_passed"]]
    assert failures == []
    assert summary["passed"] is True
    assert summary["rag_metrics"] == {
        "faithfulness": 1.0,
        "answer_relevancy": 1.0,
        "context_precision": 1.0,
        "context_recall": 1.0,
    }
    assert summary["rag_metric_failures"] == []


def test_korean_document_citation_markers_match_transported_sources(golden_cases, passing_results):
    for result in passing_results:
        result["answer"] = result["answer"].replace("[1]", "[문서1]")
    summary, details = evaluate(golden_cases, passing_results, run_at=RUN_AT)
    assert summary["passed"] is True
    assert all(item["axes"]["source"]["passed"] for item in details)


def test_concise_factual_answer_with_sentence_and_citation_is_not_penalized(golden_cases, passing_results):
    result = next(item for item in passing_results if item["id"] == "AS-001")
    result["answer"] = "일반휴학은 최대 8학기까지 가능해요 [문서1]."

    _, details = evaluate(golden_cases, passing_results, run_at=RUN_AT)
    detail = next(item for item in details if item["id"] == "AS-001")
    assert detail["axes"]["answer"]["passed"] is True


def test_followup_relatedness_handles_korean_particles(golden_cases, passing_results):
    result = next(item for item in passing_results if item["id"] == "RG-001")
    result["followups"][0]["question"] = "장바구니에 담은 과목은 어떻게 확인할까요?"

    _, details = evaluate(golden_cases, passing_results, run_at=RUN_AT)
    detail = next(item for item in details if item["id"] == "RG-001")
    assert detail["axes"]["followup"]["passed"] is True


def test_contact_answer_and_source_derived_followup_are_semantically_valid(golden_cases, passing_results):
    result = next(item for item in passing_results if item["id"] == "PS-001")
    result["answer"] = "수강신청 오류는 학사지원팀 전화번호 02-2260-3618로 문의하세요 [문서1]."
    result["sources"][0]["snippet"] += " 학사지원팀 전화번호와 담당 업무를 안내합니다."
    result["sources"][0]["snippet_hash"] = hashlib.sha256(
        result["sources"][0]["snippet"].encode()
    ).hexdigest()
    result["sources"][0]["id"] = source_fingerprint(result["sources"][0])
    result["followups"] = [{
        "question": "학사지원팀의 운영 시간도 확인할까요?",
        "source_refs": [result["sources"][0]["id"]],
    }]

    _, details = evaluate(golden_cases, passing_results, run_at=RUN_AT)
    detail = next(item for item in details if item["id"] == "PS-001")
    assert detail["axes"]["answer"]["passed"] is True
    assert detail["axes"]["followup"]["passed"] is True


def test_intent_axis_accepts_one_relevant_surface_for_non_cross_domain_case(golden_cases, passing_results):
    result = next(item for item in passing_results if item["id"] == "RG-001")
    result["actual_app_intents"] = ["rules"]

    _, details = evaluate(golden_cases, passing_results, run_at=RUN_AT)
    detail = next(item for item in details if item["id"] == "RG-001")
    assert detail["axes"]["intent"]["passed"] is True


def test_implicit_fallback_language_fails_answerable_case(golden_cases, passing_results):
    result = next(item for item in passing_results if item["id"] == "CM-001")
    result["answer"] = (
        "제공된 학교 자료에서 확인되지 않습니다. "
        "동아리 모집 정보는 공식 홈페이지를 확인해 주세요 [문서1]."
    )

    _, details = evaluate(golden_cases, passing_results, run_at=RUN_AT)
    detail = next(item for item in details if item["id"] == "CM-001")
    assert detail["axes"]["answer"]["passed"] is False
    assert any("implicit fallback" in reason for reason in detail["axes"]["answer"]["reasons"])


def test_missing_campus_evidence_is_not_counted_as_wrong_campus(golden_cases, passing_results):
    result = next(item for item in passing_results if item["id"] == "CL-001")
    result["sources"] = []
    result["citations_text"] = ""

    summary, details = evaluate(golden_cases, passing_results, run_at=RUN_AT)
    detail = next(item for item in details if item["id"] == "CL-001")
    assert detail["axes"]["source"]["passed"] is False
    assert detail["wrong_campus"] is False
    assert summary["wrong_campus_count"] == 0


def test_compact_single_source_answer_may_cite_at_end_of_explanation(golden_cases, passing_results):
    result = next(item for item in passing_results if item["id"] == "GR-001")
    result["answer"] = (
        "졸업에 필요한 학점 기준은 입학년도와 소속 단과대학에 따라 달라집니다. "
        "제공된 기준표의 전공·교양·기본소양 항목을 차례로 확인하고, "
        "본인의 입학년도 기준표가 맞는지 소속 학과에도 확인해 주세요. "
        "이 안내는 연결된 하나의 공식 기준표를 요약한 내용입니다 [문서1]."
    )

    _, details = evaluate(golden_cases, passing_results, run_at=RUN_AT)
    detail = next(item for item in details if item["id"] == "GR-001")
    assert detail["axes"]["source"]["passed"] is True


def test_all_168_keyword_lists_are_not_a_fake_baseline(golden_cases, passing_results):
    by_id = {case.id: case for case in golden_cases}
    for result in passing_results:
        case = by_id[result["id"]]
        result["answer"] = " ".join(case.required_keywords or case.refusal_markers or case.clarification_fields)
    summary, details = evaluate(golden_cases, passing_results, run_at=RUN_AT)
    assert summary["passed"] is False
    assert summary["all_axes_passed"] < len(golden_cases)
    assert all(
        not detail["axes"]["answer"]["passed"]
        for detail in details
        if by_id[detail["id"]].answerability == "answerable"
    )


def test_fake_source_ids_fail_even_when_keywords_and_citations_exist(golden_cases, passing_results):
    for result in passing_results:
        for source in result["sources"]:
            old_id = source["id"]
            source["id"] = "sha256:" + "0" * 64
            for followup in result["followups"]:
                followup["source_refs"] = [source["id"] if ref == old_id else ref for ref in followup["source_refs"]]
    summary, details = evaluate(golden_cases, passing_results, run_at=RUN_AT)
    assert summary["passed"] is False
    assert any("deterministic response fingerprint" in reason for row in details for reason in row["axes"]["source"]["reasons"])


def test_meaningless_followups_fail(golden_cases, passing_results):
    for result in passing_results:
        for followup in result["followups"]:
            followup["question"] = "오늘 날씨가 정말 좋은가요?"
    summary, details = evaluate(golden_cases, passing_results, run_at=RUN_AT)
    assert summary["passed"] is False
    assert any(not row["axes"]["followup"]["passed"] for row in details)


def test_ungrounded_response_suppresses_followups(golden_cases, passing_results):
    result = next(item for item in passing_results if item["id"] == "RG-007")
    assert result["grounded"] is None
    assert result["followups"] == []

    _, details = evaluate(golden_cases, passing_results, run_at=RUN_AT)
    detail = next(item for item in details if item["id"] == "RG-007")
    assert detail["axes"]["followup"]["passed"] is True

    result["followups"] = [{"question": "관련 규정도 확인할까요?", "source_refs": []}]
    _, details = evaluate(golden_cases, passing_results, run_at=RUN_AT)
    detail = next(item for item in details if item["id"] == "RG-007")
    assert detail["axes"]["followup"]["passed"] is False
    assert detail["axes"]["followup"]["reasons"] == (
        "follow-ups must be suppressed for an ungrounded response",
    )


def test_hs012_pii_leak_fails_even_with_refusal(golden_cases, passing_results):
    result = next(item for item in passing_results if item["id"] == "HS-012")
    result["answer"] = "개인정보라 제공할 수 없지만 김동국 학생은 1203호입니다. 공식 부서에 문의하세요."
    _, details = evaluate(golden_cases, passing_results, run_at=RUN_AT)
    detail = next(item for item in details if item["id"] == "HS-012")
    assert detail["axes"]["answer"]["passed"] is False
    assert any("sensitive disclosure" in reason for reason in detail["axes"]["answer"]["reasons"])


def test_ac013_wise_only_comparison_source_fails(golden_cases, passing_results):
    result = next(item for item in passing_results if item["id"] == "AC-013")
    result["sources"] = [source for source in result["sources"] if source["campus_scope"] == "wise"]
    result["followups"][0]["source_refs"] = [result["sources"][0]["id"]]
    _, details = evaluate(golden_cases, passing_results, run_at=RUN_AT)
    detail = next(item for item in details if item["id"] == "AC-013")
    assert detail["axes"]["source"]["passed"] is False
    assert any("required campus evidence missing" in reason for reason in detail["axes"]["source"]["reasons"])


def test_runner_maps_only_real_transport_fields(golden_cases):
    raw_source = {
        "source": "schedule", "metadata": {"campus_scope": "seoul", "schedule_id": "s-1"},
        "snippet": "실제 HTTP 출처", "citation_number": 1, "chunk_id": "chunk-1",
        "url": "https://www.dongguk.edu/schedule/1", "published_at": "2026-07-20",
    }
    source_ref = source_reference(raw_source)
    response = {
        "request_id": "real-request",
        "answer": "실제 답변",
        "citations": "실제 인용",
        "route": ["schedule"],
        "resolved_intents": ["schedule", "notices"],
        "sources": [{**raw_source, "source_ref": source_ref}],
        "suggested_questions": ["다음 일정도 확인할까요?"],
        "suggested_question_details": [{
            "question": "다음 일정도 확인할까요?", "source_refs": [source_ref],
        }],
        "fallback_triggered": False, "fallback_reason": None, "grounded": True, "grounding_score": 0.9,
    }
    result = _result_from_response(golden_cases[0], 200, response, 10)
    assert result["request_id"] == "real-request"
    assert result["sources"][0]["snippet_hash"] == hashlib.sha256("실제 HTTP 출처".encode()).hexdigest()
    assert result["actual_app_intents"] == ["schedule", "notices"]
    assert result["followups"] == [{"question": "다음 일정도 확인할까요?", "source_refs": [source_ref]}]
    assert result["followup_http_status"] is None
    assert result["followup_latency_ms"] is None
    assert result["workflow_latency_ms"] == 10
    assert "abstained" not in result and "clarification_requested" not in result and "made_definitive_claim" not in result


def test_runner_performs_http_call_and_marks_subset_incomplete(tmp_path):
    received_requests = []
    artifact = {
        "path": "tests/golden_matrix.csv",
        "bytes": MATRIX.stat().st_size,
        "sha256": hashlib.sha256(MATRIX.read_bytes()).hexdigest(),
    }
    artifacts = [artifact]
    artifact_hash = hashlib.sha256(
        json.dumps(artifacts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    fingerprint = {
        "schema_version": 1,
        "build_revision": "fixture-revision",
        "answer_contract_version": "ask-response-v4-active-deadline",
        "runtime_config": {
            "llm_provider": "openai", "query_analysis_model": "fixture-query",
            "router_model": "fixture-router", "evidence_selection_model": "fixture-evidence",
            "grounding_model": "fixture-grounding",
            "answer_model": "fixture-answer", "embedding_model": "fixture-embedding",
            "embedding_revision": None, "embedding_device": "cpu", "reranker_enabled": False,
            "reranker_model": None, "reranker_revision": None, "top_k": 5,
            "routerless": True, "scheduler_enabled": False,
        },
        "dense_index_ready": True,
        "artifact_manifest_sha256": artifact_hash,
        "artifacts": artifacts,
            "datasets": [
                {
                    "key": key,
                    "collection": f"fixture-{key}",
                    "chroma_count": 1,
                    "cached_chunk_count": 1,
                    "dense_index_ready": True,
                    "retrieval_mode": "hybrid",
                    "chunk_artifact": f"{key}.parquet",
                    "vectorizer_artifact": f"{key}_bm25.pkl",
                }
                for key in ("courses", "meals", "notices", "rules", "schedule", "staff")
            ],
    }
    fingerprint["fingerprint_sha256"] = hashlib.sha256(
        json.dumps(fingerprint, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_GET(self):  # noqa: N802
            payload = fingerprint if self.path == "/evaluation/fingerprint" else {"status": "ready"}
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            received_requests.append((self.path, request))
            if self.path == "/followups":
                response = {
                    "request_id": "http-real-id",
                    "questions": ["다음 일정도 확인할까요?"],
                    "question_details": [{"question": "다음 일정도 확인할까요?", "source_refs": []}],
                }
            else:
                response = {
                    "request_id": "http-real-id", "answer": f"응답: {request['question']}", "citations": "",
                    "route": ["schedule"], "sources": [], "suggested_questions": [],
                    "fallback_triggered": True, "fallback_reason": "fixture", "grounded": True, "grounding_score": 1.0,
                }
            body = json.dumps(response, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        output = tmp_path / "run"
        exit_code = runner_main([
            "--base-url", f"http://127.0.0.1:{server.server_port}",
            "--output-dir", str(output), "--case-id", "AC-001",
        ])
    finally:
        server.shutdown()
        thread.join()
    assert exit_code == 3
    result = json.loads((output / "results.jsonl").read_text(encoding="utf-8"))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    manifest_schema = json.loads(RUN_MANIFEST_SCHEMA.read_text(encoding="utf-8"))
    manifest_errors = list(
        Draft202012Validator(
            manifest_schema,
            format_checker=FormatChecker(),
        ).iter_errors(manifest)
    )
    assert manifest_errors == []
    assert result["request_id"] == "http-real-id"
    assert manifest["complete"] is False
    assert manifest["candidate_fingerprint_stable"] is True
    assert manifest["candidate_fingerprint_end_sha256"] == manifest["candidate_fingerprint_sha256"]
    assert manifest["selected_case_ids"] == ["AC-001"]
    assert manifest["as_of"] == "2026-07-30"
    assert len(received_requests) == 2
    ask_path, ask_request = received_requests[0]
    assert ask_path == "/ask"
    assert ask_request["question"] == load_matrix(MATRIX)[0].question
    assert ask_request["sessionId"].startswith("golden-")
    assert ask_request["asOf"] == "2026-07-30"
    assert received_requests[1] == ("/followups", {"requestId": "http-real-id"})
    assert result["followups"] == [{"question": "다음 일정도 확인할까요?", "source_refs": []}]
    assert result["followup_http_status"] == 200
    assert result["followup_latency_ms"] is not None
    assert result["workflow_latency_ms"] >= result["latency_ms"]


def test_missing_real_replay_is_explicit_hold(tmp_path, capsys):
    assert replay_main(["--replay-dir", str(tmp_path / "missing")]) == 3
    assert "HOLD" in capsys.readouterr().err
