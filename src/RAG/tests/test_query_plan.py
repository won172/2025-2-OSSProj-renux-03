"""Pre-retrieval decisions and endpoint transport parity, without network calls."""
import asyncio
import csv
import json
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api import rag_service as service
from src.database import Base, RagQueryLog, RagRetrievalLog
from src.services.direct_answer import DirectAnswer
from src.services.grounding import GroundingResult
from tests.measure_simple_query_bypass import baseline_analysis_skip

AS_OF = date(2026, 9, 28)
QUESTION = "동국대학교 질문"
BRANCHES = ("crisis_support", "campus_out_of_scope", "course_recommendation",
            "semantic_cache", "smalltalk", "future_unannounced", "structured_direct",
            "clarification", "out_of_domain", None)


def response_from_events(events, response, *, expected_grounding_reason=None):
    assert events[-1]["type"] == "done"
    assert sum(event["type"] == "metadata" for event in events) == 1
    assert sum(event["type"] == "completion" for event in events) == 1
    event_fields = {
        "metadata": {"type", "request_id", "sources", "citations", "route", "fallback_triggered"},
        "text": {"type", "content"},
        "suggestions": {"type", "questions"},
        "grounding": {"type", "grounded", "score", "reason"},
        "completion": {"type", "request_id", "grounded", "grounding_score", "relevance_score",
                       "verification_status", "suggested_questions", "suggested_question_details",
                       "resolved_intents", "fallback_reason", "sources",
                       "retrieval_mode", "degraded_datasets"},
        "done": {"type", "request_id"},
    }
    for event in events:
        expected_fields = event_fields[event["type"]]
        if event["type"] == "metadata" and "fallback_reason" in event:
            expected_fields = expected_fields | {"fallback_reason"}
        assert set(event) == expected_fields
    metadata = next(event for event in events if event["type"] == "metadata")
    completion = next(event for event in events if event["type"] == "completion")
    assert events[0] is metadata and events.index(completion) == len(events) - 2
    assert {key: value for key, value in metadata.items() if key not in {"type", "request_id"}} == {
        key: response[key] for key in ("sources", "citations", "route", "fallback_triggered")
    } | ({"fallback_reason": response["fallback_reason"]} if "fallback_reason" in metadata else {})
    assert metadata.get("fallback_reason") == response["fallback_reason"]
    assert {key: value for key, value in completion.items() if key not in {"type", "request_id"}} == {
        key: response[key] for key in event_fields["completion"] - {"type", "request_id"}
    }
    assert "".join(event["content"] for event in events if event["type"] == "text") == response["answer"]
    suggestions = [event for event in events if event["type"] == "suggestions"]
    assert len(suggestions) == bool(response["suggested_questions"])
    if suggestions:
        assert suggestions[0]["questions"] == completion["suggested_questions"]
    grounding = [event for event in events if event["type"] == "grounding"]
    assert len(grounding) == (1 if response["grounded"] is False else 0)
    if grounding:
        assert grounding[0] == {
            "type": "grounding", "grounded": response["grounded"],
            "score": response["grounding_score"], "reason": expected_grounding_reason,
        }
    else:
        assert expected_grounding_reason is None
    return {
        "answer": "".join(event["content"] for event in events if event["type"] == "text"),
        "citations": metadata["citations"],
        "route": metadata["route"],
        "resolved_intents": completion["resolved_intents"],
        "sources": completion["sources"],
        "suggested_questions": completion["suggested_questions"],
        "suggested_question_details": completion["suggested_question_details"],
        "grounded": completion["grounded"],
        "grounding_score": completion["grounding_score"],
        "relevance_score": completion["relevance_score"],
        "verification_status": completion["verification_status"],
        "fallback_triggered": metadata["fallback_triggered"],
        "fallback_reason": completion["fallback_reason"],
        "retrieval_mode": completion["retrieval_mode"],
        "degraded_datasets": completion["degraded_datasets"],
    }


async def assert_endpoint_parity(monkeypatch, question=QUESTION, *, expected_grounding_reason=None,
                                 saved_stages=None, persist_logs=False):
    temporal_context = service.TemporalContext(
        as_of=AS_OF, academic_year=2026, semester=2, phase="학기중")
    monkeypatch.setattr(service, "_request_temporal_context", lambda _req: temporal_context)
    def save(*args, **_kwargs):
        if saved_stages is not None:
            saved_stages.append(next(value.copy() for value in args
                                     if isinstance(value, dict) and "query_plan" in value))
    if not persist_logs:
        monkeypatch.setattr(service, "_save_rag_evaluation_log", save)
    monkeypatch.setattr(service, "append_manual_history", lambda *_a: None)
    monkeypatch.setattr(service, "_update_observability_log", lambda *_a: None)
    original = service._plan_query
    plans = []

    async def capture(**kw):
        plan = await original(**kw)
        plans.append(plan)
        return plan

    monkeypatch.setattr(service, "_plan_query", capture)
    req = service.AskRequest(question=question, session_id="parity-session")
    request = SimpleNamespace(state=SimpleNamespace(request_id="same-request"))
    answer = await service.ask(req, request)
    stream = await service.ask_stream(req, request)
    body = ""
    async for item in stream.body_iterator:
        body += item.decode() if isinstance(item, bytes) else item
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert len(plans) == 2 and plans[0] == plans[1]
    response = answer.model_dump(exclude={"request_id"})
    assert response == response_from_events(
        events, response, expected_grounding_reason=expected_grounding_reason,
    )
    return plans[0], answer, events


def configure(monkeypatch, branch):
    monkeypatch.setattr(service, "USE_QUERY_ANALYSIS", False)
    monkeypatch.setattr(service, "RAG_SEMANTIC_CACHE_ENABLED", False)
    monkeypatch.setattr(service, "detect_crisis", lambda _q: None)
    monkeypatch.setattr(service, "query_explicitly_requests_wise", lambda _q: False)
    monkeypatch.setattr(service, "detect_smalltalk", lambda _q: None)
    monkeypatch.setattr(service, "is_meal_direct_question", lambda _q: False)
    monkeypatch.setattr(service, "is_schedule_direct_question", lambda *_a: False)
    monkeypatch.setattr(service, "future_publication_years", lambda *_a: [])
    monkeypatch.setattr(service, "get_recent_history_text", lambda _s: "")
    monkeypatch.setattr(service, "_chat_course_recommendation", lambda *_a: None)
    monkeypatch.setattr(service, "_try_future_unannounced_answer", lambda *_a: None)
    monkeypatch.setattr(service, "_try_direct_answer", lambda *_a: None)
    monkeypatch.setattr(service, "_first_turn_clarification_fields", lambda *_a: [])
    monkeypatch.setattr(service, "_has_school_info_terms", lambda _q: True)

    async def retrieval_plan(**_kw):
        return service._RetrievalPlan(["rules"], service.RetrievalStrategy("hybrid"), {}, {})
    monkeypatch.setattr(service, "_plan_retrieval", retrieval_plan)

    if branch == "crisis_support":
        monkeypatch.setattr(service, "detect_crisis", lambda _q: SimpleNamespace(answer="위기 지원 안내", kind="self_harm"))
    elif branch == "campus_out_of_scope":
        monkeypatch.setattr(service, "query_explicitly_requests_wise", lambda _q: True)
    elif branch == "course_recommendation":
        monkeypatch.setattr(service, "_chat_course_recommendation", lambda *_a: ("추천 답변", [], ()))
    elif branch == "semantic_cache":
        monkeypatch.setattr(service, "RAG_SEMANTIC_CACHE_ENABLED", True)
        monkeypatch.setattr(service.semantic_cache, "get", lambda *_a: {
            "answer": "캐시 답변", "route": ["rules"], "sources": [],
            "verification_status": "passed", "grounded": True,
            "suggested_questions": ["관련 규정은 어디서 확인해?"],
        })
    elif branch == "smalltalk":
        monkeypatch.setattr(service, "detect_smalltalk", lambda _q: SimpleNamespace(answer="안녕하세요", kind="greeting"))
    elif branch == "future_unannounced":
        monkeypatch.setattr(service, "_try_future_unannounced_answer", lambda *_a: DirectAnswer(answer="아직 발표되지 않았습니다", kind="future_unannounced"))
    elif branch == "structured_direct":
        monkeypatch.setattr(service, "_try_direct_answer", lambda *_a: DirectAnswer(answer="식단 확인 불가", kind="meal_stale"))
    elif branch == "clarification":
        monkeypatch.setattr(service, "_first_turn_clarification_fields", lambda *_a: ["학과"])
    elif branch == "out_of_domain":
        monkeypatch.setattr(service, "USE_QUERY_ANALYSIS", True)
        monkeypatch.setattr(service, "_has_school_info_terms", lambda _q: False)
        async def analyze(*_a, **_kw):
            return service.QueryAnalysisResult(normalized_question=QUESTION, intent="unknown")
        monkeypatch.setattr(service, "analyze_query", analyze)


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", BRANCHES, ids=lambda value: value or "retrieval")
async def test_every_decision_branch_has_a_plan(monkeypatch, branch):
    configure(monkeypatch, branch)
    stages = {}
    plan = await service._plan_query(
        req=service.AskRequest(question=QUESTION, major="컴퓨터·AI학부"), raw_query=QUESTION,
        temporal_context=SimpleNamespace(as_of=AS_OF), request_id="plan", session_id="session",
        stage_timings=stages, llm_usage=[], mode="ask")
    assert plan.direct_handler == branch
    assert plan.routes and plan.reason and plan.confidence > 0
    assert plan.filters["as_of"] == AS_OF
    assert stages["query_plan"] == {"reason": plan.reason, "direct_handler": branch}
    with pytest.raises(FrozenInstanceError):
        plan.reason = "changed"
    if branch is None:
        assert plan.route == ["rules"] and plan.strategy.mode == "hybrid"
        assert plan.filters["campus"] == "seoul_bmc"
        assert plan.allow_wise is False
        assert plan.filters["department"] == "컴퓨터·AI학부"
        assert plan.filters["where"]["major"] == {"$eq": "컴퓨터·AI학부"}
        assert plan.ontology_document_keys_by_dataset == {}
        assert plan.structured_document_keys_by_dataset == {}
    elif branch == "campus_out_of_scope":
        assert plan.filters["campus"] == "wise"
    elif branch == "future_unannounced":
        assert plan.intent == "future_unannounced"
        assert plan.time_sensitivity == "future"


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", BRANCHES, ids=lambda value: value or "retrieval")
async def test_endpoints_share_plan_response_and_event_contract(monkeypatch, branch):
    configure(monkeypatch, branch)
    if branch is None:
        async def no_results(**_kw):
            return [], False, []
        async def enrich(**kw):
            return kw["frames"], []
        monkeypatch.setattr(service, "_retrieve_frames_for_queries", no_results)
        monkeypatch.setattr(service, "_enrich_staff_lookup_frames", enrich)
    _plan, _answer, events = await assert_endpoint_parity(monkeypatch)
    expected_types = ["metadata", "text"]
    if branch == "semantic_cache":
        expected_types.append("suggestions")
    assert [event["type"] for event in events] == expected_types + ["completion", "done"]


@pytest.mark.asyncio
async def test_crisis_precedes_wise_and_history_or_course_lookup(monkeypatch):
    configure(monkeypatch, "crisis_support")
    monkeypatch.setattr(service, "query_explicitly_requests_wise", lambda _q: True)
    monkeypatch.setattr(service, "get_recent_history_text", lambda _s: pytest.fail("history read"))
    monkeypatch.setattr(service, "_chat_course_recommendation", lambda *_a: pytest.fail("course lookup"))
    plan = await service._plan_query(
        req=service.AskRequest(question=QUESTION), raw_query=QUESTION,
        temporal_context=SimpleNamespace(as_of=AS_OF), request_id="priority",
        session_id="session", stage_timings={}, llm_usage=[], mode="ask")
    assert plan.direct_handler == "crisis_support"


@pytest.mark.asyncio
async def test_analyzed_retrieval_keeps_time_and_date_filters(monkeypatch):
    configure(monkeypatch, None)
    monkeypatch.setattr(service, "USE_QUERY_ANALYSIS", True)
    async def analyze(*_a, **_kw):
        return service.QueryAnalysisResult(
            normalized_question="이번 주 학사일정", intent="schedule", time_focus="this_week")
    monkeypatch.setattr(service, "analyze_query", analyze)
    date_filter = service.QueryDateFilter(start=AS_OF, end=AS_OF, label="today", is_relative=True)
    monkeypatch.setattr(service, "extract_date_filter_from_query", lambda *_a, **_kw: date_filter)
    plan = await service._plan_query(
        req=service.AskRequest(question=QUESTION), raw_query=QUESTION,
        temporal_context=SimpleNamespace(as_of=AS_OF), request_id="analysis",
        session_id="session", stage_timings={}, llm_usage=[], mode="ask")
    assert plan.direct_handler is None
    assert plan.analysis_meta.used is True
    assert plan.intent == "schedule"
    assert plan.reason == "analyzed_retrieval"
    assert plan.time_sensitivity == "this_week"
    assert plan.filters["date"] == date_filter


@pytest.mark.asyncio
async def test_bmc_query_keeps_the_retrieval_campus_boundary(monkeypatch):
    configure(monkeypatch, None)
    question = "바이오메디캠퍼스 학사 규정 알려줘"
    plan = await service._plan_query(
        req=service.AskRequest(question=question), raw_query=question,
        temporal_context=SimpleNamespace(as_of=AS_OF), request_id="bmc",
        session_id="session", stage_timings={}, llm_usage=[], mode="ask")
    assert plan.direct_handler is None
    assert plan.filters["campus"] == "seoul_bmc"
    assert plan.allow_wise is False
    assert plan.filters["as_of"] == AS_OF
    assert plan.filters["where"] == {}


@pytest.mark.asyncio
async def test_typo_correction_records_the_audience_of_each_search_query(monkeypatch):
    configure(monkeypatch, None)
    question = "수강신챠 어떻게 해?"
    plan = await service._plan_query(
        req=service.AskRequest(question=question), raw_query=question,
        temporal_context=SimpleNamespace(as_of=AS_OF), request_id="typo",
        session_id="session", stage_timings={}, llm_usage=[], mode="ask")
    assert plan.direct_handler is None
    assert question in plan.retrieval_queries
    assert "수강신청 어떻게 해?" in plan.retrieval_queries
    assert plan.filters["audience_by_query"] == {
        query: service.query_audience(query) for query in plan.retrieval_queries
    }
    assert plan.filters["audience_by_query"][question] == "common"
    assert plan.filters["audience_by_query"]["수강신청 어떻게 해?"] == "undergraduate"
    assert "audience" not in plan.filters


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "history"),
    [
        ("교내 등록끔 고지서 알려줘", ""),
        ("통계학과 희망 강의 개설학과 알려줘", ""),
        ("교내 시간표짜 알려줘", ""),
        ("올해 교내 등록끔 고지서 알려줘", ""),
        ("교내 등록끔 고지서와 장학금 지급일 비교해줘", ""),
        ("컴퓨터공학과 사무실 전화번호 02-2260-0000 알려줘", ""),
        ("02-2260-0000 담당자 알려줘", ""),
        ("CSC2007 과목 개설학과 알려줘", ""),
        ("CSC2007 개설학과 알려줘", ""),
        ("CSC2007이랑 데이터베이스 개설학과 알려줘", ""),
        ("교내 등록끔 고지서와 학생식당 메뉴 알려줘", ""),
        ("최근 등록끔 공지 알려줘", ""),
        ("교내 등록끔 고지서 신청 공지 알려줘", ""),
        ("컴퓨터공학과 사무실 전화번호 02-2260-0000와 신청 공지 알려줘", ""),
        ("02-2260-0000와 02-2260-1111 담당자를 비교해줘", ""),
        ("02-2260-0000 담당자와 소속을 비교해줘", ""),
        ("02-2260-0000 담당자 및 부서 알려줘", ""),
        ("CSC2007 및 CSC2008 개설학과 알려줘", ""),
        ("CSC2007 개설학과와 담당학과 차이 알려줘", ""),
        ("CSC2007과 개설학과별 과목을 각각 알려줘", ""),
        ("CSC2007 과목 개설학과와 수강신청 변경 공지 알려줘", ""),
        ("CSC2007 과목 개설학과 알려줘", "직전 질문과 답변"),
        ("이번 학기 CSC2007 과목 개설학과 알려줘", ""),
        ("2024학번 교양 이수 기준 알려줘", ""),
    ],
)
async def test_analysis_skip_decision_and_log_match_head(
    monkeypatch, question, history,
):
    configure(monkeypatch, None)
    monkeypatch.setattr(service, "USE_QUERY_ANALYSIS", True)
    monkeypatch.setattr(service, "get_recent_history_text", lambda _session: history)
    calls = []
    async def analyze(*args, **_kwargs):
        calls.append(args)
        return service.QueryAnalysisResult(normalized_question=question, intent="courses")
    monkeypatch.setattr(service, "analyze_query", analyze)
    route = service._resolve_retrieval_route(
        question, service.QueryAnalysisMeta(result=None, used=False, failed=False),
    )
    expected_skip = not history and baseline_analysis_skip(
        question, service._query_for_analysis(question), route,
    )
    stages = {}
    plan = await service._plan_query(
        req=service.AskRequest(question=question), raw_query=question,
        temporal_context=SimpleNamespace(as_of=AS_OF), request_id="skip-decision",
        session_id="session", stage_timings=stages, llm_usage=[], mode="ask",
    )
    assert bool(calls) is not expected_skip
    assert plan.analysis_meta.used is not expected_skip
    assert stages["query_analysis_decision"] == {
        "skipped": expected_skip,
        "reason": "single_explicit_route" if expected_skip else "llm_required",
    }


def test_analysis_skip_decisions_equal_head_for_every_golden_question():
    matrix = Path(__file__).with_name("golden_matrix.csv")
    with matrix.open(encoding="utf-8-sig", newline="") as source:
        questions = [case["question"] for case in csv.DictReader(source)]
    assert len(questions) == 190
    for question in questions:
        normalized = service._query_for_analysis(question)
        route = service._resolve_retrieval_route(
            question, service.QueryAnalysisMeta(result=None, used=False, failed=False),
        )
        assert service._can_skip_query_analysis(question, normalized, "") is baseline_analysis_skip(
            question, normalized, route,
        ), question


@pytest.mark.asyncio
async def test_sql_scoped_bypass_has_json_stream_parity_and_still_checks_grounding(monkeypatch):
    configure(monkeypatch, None)
    async def structured_plan(**_kwargs):
        return service._RetrievalPlan(
            ["rules"], service.RetrievalStrategy("structured", "rules"), {},
            {"rules": ("rules:one",)},
        )
    monkeypatch.setattr(service, "_plan_retrieval", structured_plan)
    frame = pd.DataFrame([{
        "candidate_id": "c1", "chunk_id": "rule-chunk", "document_key": "rules:one",
        "dataset": "rules", "source": "official", "title": "2024학번 교양 이수 기준",
        "chunk_text": "2024학번 교양 이수 기준 공식 자료", "hybrid_score": 0.9,
        "vector_score": 0.9, "sparse_score": 0.9, "dataset_rank": 1,
        "structured_match": 1,
    }])
    async def retrieve(**_kwargs):
        return [frame.copy()], False, []
    async def enrich(**kwargs):
        return kwargs["frames"], []
    async def unexpected_selector(*_args, **_kwargs):
        pytest.fail("LLM selector called")
    async def generate(**_kwargs):
        return "2024학번 교양 이수 기준입니다. [문서1]"
    async def generate_stream(**_kwargs):
        yield "2024학번 교양 이수 기준입니다. [문서1]"
    checked = []
    async def ground(*args, **_kwargs):
        checked.append(args)
        return GroundingResult(checked=True, grounded=True, score=0.95,
                               relevance_score=0.9, reason="official source")
    monkeypatch.setattr(service, "_retrieve_frames_for_queries", retrieve)
    monkeypatch.setattr(service, "_enrich_staff_lookup_frames", enrich)
    monkeypatch.setattr(service, "_build_balanced_shortlist", lambda *_a, **_kw: frame.copy())
    monkeypatch.setattr(service, "_apply_cross_encoder_rerank", lambda rows, _q: rows)
    monkeypatch.setattr(service, "select_evidence_groups", unexpected_selector)
    monkeypatch.setattr(service, "generate_langchain_answer", generate)
    monkeypatch.setattr(service, "generate_langchain_answer_stream", generate_stream)
    monkeypatch.setattr(service, "check_answer_grounding", ground)
    monkeypatch.setattr(service, "RAG_GROUNDING_CHECK_ENABLED", True)
    monkeypatch.setattr(service, "RAG_STREAM_BUFFER_UNTIL_GROUNDED", True)

    saved_stages = []
    plan, answer, events = await assert_endpoint_parity(
        monkeypatch, "2024학번 교양 이수 기준 알려줘", saved_stages=saved_stages,
    )
    assert plan.direct_handler is None
    assert answer.verification_status == "passed"
    assert len(checked) == 2
    assert len(saved_stages) == 2
    assert all(stages["evidence_selection_decision"] == {
        "skipped": True, "reason": "structured_sql_document",
    } for stages in saved_stages)
    assert [event["type"] for event in events] == ["metadata", "text", "completion", "done"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["schedule_event", "meal", "course_recommendation"])
async def test_source_bearing_direct_answers_have_full_transport_parity(monkeypatch, kind):
    branch = "course_recommendation" if kind == "course_recommendation" else "structured_direct"
    configure(monkeypatch, branch)
    if branch == "course_recommendation":
        source = service.SourceChunk(
            source="courses", metadata={"course_code": "STA3001", "campus_scope": "bmc"},
            snippet="통계학과 머신러닝 3학점", citation_number=1,
            chunk_id="courses:STA3001", title="머신러닝",
            url="https://www.dongguk.edu/course/STA3001",
        )
        source.source_ref = service.source_reference(source.model_dump())
        monkeypatch.setattr(service, "_chat_course_recommendation", lambda *_a: (
            "머신러닝을 추천합니다. [문서1]", [source], (),
        ))
    else:
        is_meal = kind.startswith("meal")
        title = "학생식당" if is_meal else "2026학년도 2학기 학사일정"
        source = {
            "source": "meals" if is_meal else "schedule", "title": title,
            "published_at": AS_OF.isoformat(), "snippet": "공식 정형 자료",
            "chunk_id": "meals:1" if is_meal else "schedule:1",
            "url": "https://www.dongguk.edu/official",
            "metadata": {
                "campus_scope": "seoul" if is_meal else "shared",
                "source_type": "meal" if is_meal else "schedule",
            },
        }
        direct = DirectAnswer(answer=f"{title} 안내입니다.", sources=[source], kind=kind)
        monkeypatch.setattr(service, "_try_direct_answer", lambda *_a: direct)
    plan, answer, events = await assert_endpoint_parity(monkeypatch)
    assert plan.direct_handler == branch
    assert answer.sources and answer.sources[0].source_ref
    assert events[0]["sources"] == [source.model_dump() for source in answer.sources]
    if branch == "structured_direct":
        assert "[문서1]" in answer.answer
        assert answer.citations.startswith("- [문서1]")
        assert answer.route == (["meals"] if kind.startswith("meal") else ["schedule"])
    else:
        assert answer.route == ["courses"]
        assert answer.citations == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("grounded", [True, False], ids=["grounded", "grounding_failed"])
@pytest.mark.parametrize("retrieval_mode", ["hybrid", "sparse_degraded"])
async def test_source_bearing_retrieval_has_full_transport_parity(monkeypatch, grounded, retrieval_mode):
    configure(monkeypatch, None)
    frame = pd.DataFrame([{
        "dataset": "rules", "source": "rules", "chunk_id": "rules:bmc-1",
        "chunk_text": "[바이오메디캠퍼스 학사 규정]\n학사 규정 내용입니다.",
        "title": "바이오메디캠퍼스 학사 규정",
        "url": "https://www.dongguk.edu/rules/bmc-1",
        "published_at": "2026-09-01", "campus_scope": "bmc",
        "hybrid_score": 0.95, "structured_match": 1,
        "evidence_group": 1, "citation_number": 1,
        "matched_query": "바이오메디캠퍼스 학사 규정 알려줘",
        "dense_rank": 1, "sparse_rank": 2, "fusion_rank": 1,
        "corpus_revision": "rules:revision",
        "dense_similarity_raw": 0.9,
    }])

    async def retrieve(**kwargs):
        assert kwargs["allow_wise"] is False
        service._retrieval_observations.get().append({
            "dataset": "rules", "retrieval_mode": retrieval_mode,
            "dense_error_type": "InternalError" if retrieval_mode == "sparse_degraded" else None,
        })
        return [frame.copy()], False, []

    async def enrich(**kwargs):
        return kwargs["frames"], []

    async def select(_question, shortlist, _usage, **_kwargs):
        return shortlist, False

    async def generate(**_kwargs):
        return "바이오메디캠퍼스 학사 규정 내용입니다. [문서1]"

    async def generate_stream(**_kwargs):
        yield "바이오메디캠퍼스 학사 규정 내용입니다. [문서1]"

    async def ground(*_args, **_kwargs):
        return GroundingResult(
            checked=True, grounded=grounded, score=0.94 if grounded else 0.2,
            relevance_score=0.9 if grounded else 0.3,
            reason="source supports answer" if grounded else "source conflicts with answer",
        )

    monkeypatch.setattr(service, "_retrieve_frames_for_queries", retrieve)
    monkeypatch.setattr(service, "_enrich_staff_lookup_frames", enrich)
    monkeypatch.setattr(service, "_build_balanced_shortlist", lambda *_a, **_kw: frame.copy())
    monkeypatch.setattr(service, "_apply_cross_encoder_rerank", lambda shortlist, _q: shortlist)
    monkeypatch.setattr(service, "_select_answer_evidence", select)
    monkeypatch.setattr(service, "generate_langchain_answer", generate)
    monkeypatch.setattr(service, "generate_langchain_answer_stream", generate_stream)
    monkeypatch.setattr(service, "check_answer_grounding", ground)
    monkeypatch.setattr(service, "RAG_GROUNDING_CHECK_ENABLED", True)
    monkeypatch.setattr(service, "RAG_STREAM_BUFFER_UNTIL_GROUNDED", True)
    plan, answer, events = await assert_endpoint_parity(
        monkeypatch, "바이오메디캠퍼스 학사 규정 알려줘",
        expected_grounding_reason=None if grounded else "source conflicts with answer",
    )
    assert plan.direct_handler is None
    assert plan.filters["campus"] == "seoul_bmc"
    assert answer.sources and answer.sources[0].metadata["campus_scope"] == "bmc"
    assert answer.retrieval_mode == retrieval_mode
    assert answer.degraded_datasets == (["rules"] if retrieval_mode == "sparse_degraded" else [])
    completion = next(event for event in events if event["type"] == "completion")
    assert completion["retrieval_mode"] == answer.retrieval_mode
    assert completion["degraded_datasets"] == answer.degraded_datasets
    assert not set(service._SOURCE_TRACE_FIELDS).intersection(answer.sources[0].metadata)
    assert not set(service._SOURCE_TRACE_FIELDS).intersection(events[0]["sources"][0]["metadata"])
    assert answer.citations.startswith("- 바이오메디캠퍼스 학사 규정")
    assert answer.verification_status == (
        service.VERIFICATION_PASSED if grounded else service.VERIFICATION_FAILED
    )
    assert events[0]["sources"] and events[0]["citations"]
    if not grounded:
        assert [event["type"] for event in events] == [
            "metadata", "grounding", "text", "completion", "done",
        ]


@pytest.mark.asyncio
async def test_one_degraded_dataset_reaches_both_endpoints_and_query_logs(monkeypatch, tmp_path):
    configure(monkeypatch, None)
    async def two_dataset_plan(**_kwargs):
        return service._RetrievalPlan(
            ["rules", "courses"], service.RetrievalStrategy("hybrid"), {}, {},
        )
    monkeypatch.setattr(service, "_plan_retrieval", two_dataset_plan)
    monkeypatch.setattr(service, "_request_temporal_context", lambda _req: service.TemporalContext(
        as_of=AS_OF, academic_year=2026, semester=2, phase="학기중",
    ))
    monkeypatch.setattr(service, "_ensure_dataset", lambda _dataset: (pd.DataFrame(), None, None, None))
    monkeypatch.setattr(service, "RAG_GROUNDING_CHECK_ENABLED", False)
    monkeypatch.setattr(service, "append_manual_history", lambda *_args: None)

    row = {
        "dataset": "rules", "source": "rules", "chunk_id": "rules:1",
        "chunk_text": "공식 규정 내용", "title": "공식 규정",
        "url": "https://www.dongguk.edu/rules/1",
        "published_at": "2026-09-01", "campus_scope": "seoul",
        "hybrid_score": 0.95, "structured_match": 1,
        "evidence_group": 1, "citation_number": 1,
        "dense_rank": 1, "sparse_rank": 2, "fusion_rank": 1,
        "corpus_revision": "rules:revision",
    }
    searches = []
    def search(**kwargs):
        dataset = next(
            key for key, artifacts in service.DATASET_ARTIFACTS.items()
            if artifacts.collection == kwargs["collection_name"]
        )
        searches.append(dataset)
        hits = pd.DataFrame([row]) if dataset == "rules" else pd.DataFrame(columns=row)
        hits.attrs.update(
            retrieval_mode="hybrid" if dataset == "rules" else "sparse_degraded",
            dense_error_type=None if dataset == "rules" else "InternalError",
        )
        return hits

    async def enrich(**kwargs):
        return kwargs["frames"], []

    async def select(_question, shortlist, _usage, **_kwargs):
        return shortlist, False

    async def generate(**_kwargs):
        return "공식 규정 내용입니다. [문서1]"

    async def generate_stream(**_kwargs):
        yield "공식 규정 내용입니다. [문서1]"

    def shortlist(frames, **_kwargs):
        assert len(frames) == 1 and frames[0]["dataset"].tolist() == ["rules"]
        return frames[0].copy()

    monkeypatch.setattr(service, "hybrid_search_with_meta", search)
    monkeypatch.setattr(service, "_enrich_staff_lookup_frames", enrich)
    monkeypatch.setattr(service, "_build_balanced_shortlist", shortlist)
    monkeypatch.setattr(service, "_apply_cross_encoder_rerank", lambda frame, _q: frame)
    monkeypatch.setattr(service, "_select_answer_evidence", select)
    monkeypatch.setattr(service, "generate_langchain_answer", generate)
    monkeypatch.setattr(service, "generate_langchain_answer_stream", generate_stream)

    engine = create_engine(f"sqlite:///{tmp_path / 'queries.db'}")
    Base.metadata.create_all(engine, tables=[RagQueryLog.__table__, RagRetrievalLog.__table__])
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(service, "SessionLocal", sessions)
    try:
        req = service.AskRequest(question=QUESTION, session_id="mixed-dataset-session")
        json_request = SimpleNamespace(state=SimpleNamespace(request_id="mixed-json"))
        answer = await service.ask(req, json_request)
        stream_request = SimpleNamespace(state=SimpleNamespace(request_id="mixed-stream"))
        stream = await service.ask_stream(req, stream_request)
        body = "".join([
            item.decode() if isinstance(item, bytes) else item
            async for item in stream.body_iterator
        ])
        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
        completion = next(event for event in events if event["type"] == "completion")
        expected = {"retrieval_mode": "sparse_degraded", "degraded_datasets": ["courses"]}
        assert {key: getattr(answer, key) for key in expected} == expected
        assert {key: completion[key] for key in expected} == expected
        assert searches == ["rules", "courses", "rules", "courses"]
        assert [event["type"] for event in events] == ["metadata", "text", "completion", "done"]
        assert answer.sources and completion["sources"] == [source.model_dump() for source in answer.sources]
        assert not set(service._SOURCE_TRACE_FIELDS).intersection(answer.sources[0].metadata)
        with sessions() as session:
            logs = session.query(RagQueryLog).order_by(RagQueryLog.request_id).all()
            assert [log.request_id for log in logs] == ["mixed-json", "mixed-stream"]
            for log in logs:
                retrieval = json.loads(log.stage_timings_json)["retrieval"]
                assert {key: retrieval[key] for key in expected} == expected
                assert [(entry["dataset"], entry["retrieval_mode"]) for entry in retrieval["searches"]] == [
                    ("rules", "hybrid"), ("courses", "sparse_degraded"),
                ]
                assert retrieval["sources"][0]["corpus_revision"] == "rules:revision"
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_retrieval_keeps_endpoint_sources_citations_and_trace(monkeypatch, tmp_path):
    configure(monkeypatch, None)

    async def two_dataset_plan(**_kwargs):
        return service._RetrievalPlan(
            ["rules", "courses"], service.RetrievalStrategy("hybrid"), {}, {},
        )

    monkeypatch.setattr(service, "_plan_retrieval", two_dataset_plan)
    monkeypatch.setattr(service, "_ensure_dataset", lambda _: (pd.DataFrame(), None, None, None))
    monkeypatch.setattr(service, "RAG_GROUNDING_CHECK_ENABLED", False)
    row = {
        "dataset": "rules", "source": "rules", "chunk_id": "rules:1",
        "chunk_text": "공식 규정 내용", "title": "공식 규정",
        "url": "https://www.dongguk.edu/rules/1", "published_at": "2026-09-01",
        "campus_scope": "seoul", "hybrid_score": 0.95,
        "structured_match": 1, "evidence_group": 1, "citation_number": 1,
        "dense_rank": 1, "sparse_rank": 2, "fusion_rank": 1,
        "corpus_revision": "rules:revision",
    }

    def search(**kwargs):
        dataset = next(
            key for key, artifacts in service.DATASET_ARTIFACTS.items()
            if artifacts.collection == kwargs["collection_name"]
        )
        hits = pd.DataFrame([row]) if dataset == "rules" else pd.DataFrame(columns=row)
        hits.attrs.update(
            retrieval_mode="hybrid" if dataset == "rules" else "sparse_degraded",
            dense_error_type=None if dataset == "rules" else "InternalError",
        )
        return hits

    async def select(_question, shortlist, _usage, **_kwargs):
        return shortlist, False

    async def generate(**_kwargs):
        return "공식 규정 내용입니다. [문서1]"

    async def generate_stream(**_kwargs):
        yield "공식 규정 내용입니다. [문서1]"

    monkeypatch.setattr(service, "hybrid_search_with_meta", search)
    monkeypatch.setattr(service, "_enrich_staff_lookup_frames", lambda **kw: _return_enriched(kw))
    monkeypatch.setattr(service, "_build_balanced_shortlist", lambda frames, **_: frames[0].copy())
    monkeypatch.setattr(service, "_apply_cross_encoder_rerank", lambda frame, _q: frame)
    monkeypatch.setattr(service, "_select_answer_evidence", select)
    monkeypatch.setattr(service, "generate_langchain_answer", generate)
    monkeypatch.setattr(service, "generate_langchain_answer_stream", generate_stream)

    engine = create_engine(f"sqlite:///{tmp_path / 'parity.db'}")
    Base.metadata.create_all(engine, tables=[RagQueryLog.__table__, RagRetrievalLog.__table__])
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(service, "SessionLocal", sessions)
    try:
        outcomes = []
        for concurrency in (1, 3):
            monkeypatch.setattr(service.rag_config, "RAG_RETRIEVAL_CONCURRENCY", concurrency)
            _, answer, events = await assert_endpoint_parity(monkeypatch, persist_logs=True)
            with sessions() as session:
                logs = session.query(RagQueryLog).order_by(RagQueryLog.id.desc()).limit(2).all()
                traces = [json.loads(log.stage_timings_json)["retrieval"] for log in logs]
            assert len(traces) == 2
            assert traces[0]["searches"] == traces[1]["searches"]
            outcomes.append((
                answer.model_dump(exclude={"request_id"}),
                [{key: value for key, value in event.items() if key != "request_id"} for event in events],
                {key: traces[0][key] for key in ("retrieval_mode", "degraded_datasets", "searches")},
            ))
        assert outcomes[0] == outcomes[1]
        answer, events, trace = outcomes[0]
        assert answer["sources"] and answer["citations"]
        assert answer["retrieval_mode"] == "sparse_degraded"
        assert answer["degraded_datasets"] == ["courses"]
        assert [(item["dataset"], item["retrieval_mode"]) for item in trace["searches"]] == [
            ("rules", "hybrid"), ("courses", "sparse_degraded"),
        ]
        assert next(event for event in events if event["type"] == "completion")["sources"] == answer["sources"]
    finally:
        engine.dispose()


async def _return_enriched(kwargs):
    return kwargs["frames"], []

@pytest.mark.asyncio
async def test_execute_query_emits_typed_events_then_one_direct_outcome(monkeypatch):
    configure(monkeypatch, "crisis_support")
    monkeypatch.setattr(service, "_save_rag_evaluation_log", lambda *_a, **_kw: None)
    monkeypatch.setattr(service, "append_manual_history", lambda *_a: None)
    req = service.AskRequest(question=QUESTION, session_id="typed-session")
    temporal_context = service.TemporalContext(
        as_of=AS_OF, academic_year=2026, semester=2, phase="학기중",
    )
    stages = {}
    plan = await service._plan_query(
        req=req, raw_query=QUESTION, temporal_context=temporal_context,
        request_id="typed-request", session_id="typed-session",
        stage_timings=stages, llm_usage=[], mode="ask",
    )
    steps = [step async for step in service.execute_query(
        plan, "ask", req=req, raw_query=QUESTION,
        temporal_context=temporal_context, request_id="typed-request",
        session_id="typed-session", stage_timings=stages, llm_usage=[],
        request_started_at=service.time.perf_counter(),
    )]
    assert [step.payload["type"] for step in steps[:-1]] == [
        "metadata", "text", "completion",
    ]
    assert all(isinstance(step, service.QueryEvent) for step in steps[:-1])
    assert isinstance(steps[-1], service.QueryOutcome)
    assert steps[-1].response().answer == "위기 지원 안내"
    assert steps[-1].route == ["crisis_support"]
    assert steps[-1].verification_status == service.VERIFICATION_NOT_REQUIRED


# These fixed outputs were recorded against b74145e before execute_query was added.
# HEAD's default unbuffered replace policy exposes the candidate in SSE before
# grounding, while JSON returns only the guard. Distress support notes also have
# mode-specific ordering at HEAD; preserve those differences explicitly.
@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["generated", "fallback", "grounding_failed", "grounding_unavailable", "active_notice", "cache_hit", "cache_store", "grounding_failed_unbuffered", "grounding_failed_unbuffered_append", "distress_replace", "distress_append"])
async def test_head_execution_snapshots_and_single_writes(monkeypatch, case):
    configure(monkeypatch, "semantic_cache" if case == "cache_hit" else None)
    question = "번아웃이 왔는데 휴학 절차 알려주세요" if case.startswith("distress_") else QUESTION
    failed_cases = {"grounding_failed", "grounding_failed_unbuffered", "grounding_failed_unbuffered_append", "distress_replace", "distress_append"}
    temporal_context = service.TemporalContext(as_of=AS_OF, academic_year=2026, semester=2, phase="학기중")
    monkeypatch.setattr(service, "_request_temporal_context", lambda _req: temporal_context)
    monkeypatch.setattr(service, "RAG_GROUNDING_CHECK_ENABLED", case in failed_cases | {"grounding_unavailable", "active_notice", "cache_store"})
    # Active notices must buffer on their own, even with global buffering off.
    monkeypatch.setattr(service, "RAG_STREAM_BUFFER_UNTIL_GROUNDED", case in {"grounding_failed", "distress_replace", "distress_append"})
    monkeypatch.setattr(service, "RAG_GROUNDING_FAILURE_POLICY", "append" if case in {"grounding_failed_unbuffered_append", "distress_append"} else "replace")
    if case == "cache_store":
        monkeypatch.setattr(service, "RAG_SEMANTIC_CACHE_ENABLED", True)
        monkeypatch.setattr(service.semantic_cache, "get", lambda *_a: None)
    monkeypatch.setattr(service, "_is_active_notice_state_query", lambda *_a: case == "active_notice")
    monkeypatch.setattr(service, "_filter_active_notice_frames", lambda frames, _as_of: (frames, service.ActiveNoticeFilterStats()))
    monkeypatch.setattr(service, "_enforce_active_notice_answer_contract", lambda _q, answer, _rows, _as_of: answer)
    monkeypatch.setattr(service, "append_manual_history", lambda *_a: None)
    monkeypatch.setattr(service, "_update_observability_log", lambda *_a: None)
    writes = {"log": 0, "cache": 0, "ground": 0}
    log_calls = []
    cache_calls = []
    def log(*args, **kwargs):
        writes["log"] += 1
        log_calls.append((args, kwargs))
    def cache(*args, **kwargs):
        writes["cache"] += 1
        cache_calls.append((args, kwargs))
    def ground_log(*_a, **_kw):
        writes["ground"] += 1
    monkeypatch.setattr(service, "_save_rag_evaluation_log", log)
    monkeypatch.setattr(service.semantic_cache, "put", cache)
    monkeypatch.setattr(service, "_update_grounding_log", ground_log)
    frame = pd.DataFrame([{
        "dataset": "rules", "source": "rules", "chunk_id": "rules:1",
        "chunk_text": "공식 규정 내용", "title": "공식 규정", "url": "https://www.dongguk.edu/rules/1",
        "published_at": "2026-09-01", "campus_scope": "seoul", "hybrid_score": 0.95,
        "structured_match": 1, "evidence_group": 1, "citation_number": 1,
        "matched_query": question,
    }])
    async def retrieve(**_kw):
        return ([] if case == "fallback" else [frame.copy()]), False, []
    async def enrich(**kw):
        return kw["frames"], []
    async def select(_q, rows, _usage, **_kw):
        return rows, False
    async def generate(**_kw):
        return "공식 답변 [문서1]"
    async def generate_stream(**_kw):
        yield "공식 "
        yield "답변 [문서1]"
    checked_answers = []
    grounding_entered = asyncio.Event()
    release_grounding = asyncio.Event()
    hold_stream_grounding = False
    async def ground(_question, candidate, *_a, **_kw):
        checked_answers.append(candidate)
        if hold_stream_grounding:
            grounding_entered.set()
            await release_grounding.wait()
        return GroundingResult(
            checked=case != "grounding_unavailable", grounded=case in {"active_notice", "cache_store"},
            score=0.2 if case in failed_cases else 0.9,
            relevance_score=0.3 if case in failed_cases else 0.9,
            reason="source conflicts with answer" if case in failed_cases else "source supports answer",
        )
    monkeypatch.setattr(service, "_retrieve_frames_for_queries", retrieve)
    monkeypatch.setattr(service, "_enrich_staff_lookup_frames", enrich)
    monkeypatch.setattr(service, "_build_balanced_shortlist", lambda *_a, **_kw: frame.copy() if case != "fallback" else pd.DataFrame())
    monkeypatch.setattr(service, "_apply_cross_encoder_rerank", lambda rows, _q: rows)
    monkeypatch.setattr(service, "_select_answer_evidence", select)
    monkeypatch.setattr(service, "generate_langchain_answer", generate)
    monkeypatch.setattr(service, "generate_langchain_answer_stream", generate_stream)
    monkeypatch.setattr(service, "check_answer_grounding", ground)
    monkeypatch.setattr(service, "_try_direct_answer", lambda *_a: None)
    expected = {
        "generated": (["rules"], False, None, "unavailable", None, ["metadata", "text", "text", "completion", "done"]),
        "fallback": (["rules"], True, "no_results", "not_required", None, ["metadata", "text", "completion", "done"]),
        "grounding_failed": (["rules"], False, None, "failed", False, ["metadata", "grounding", "text", "completion", "done"]),
        "grounding_unavailable": (["rules"], False, None, "unavailable", None, ["metadata", "text", "text", "completion", "done"]),
        "grounding_failed_unbuffered": (["rules"], False, None, "failed", False, ["metadata", "text", "text", "grounding", "text", "completion", "done"]),
        "grounding_failed_unbuffered_append": (["rules"], False, None, "failed", False, ["metadata", "text", "text", "grounding", "text", "completion", "done"]),
        "distress_replace": (["rules"], False, None, "failed", False, ["metadata", "grounding", "text", "completion", "done"]),
        "distress_append": (["rules"], False, None, "failed", False, ["metadata", "grounding", "text", "completion", "done"]),
        "active_notice": (["rules"], False, None, "passed", True, ["metadata", "text", "completion", "done"]),
        "cache_hit": (["rules"], False, None, "passed", True, ["metadata", "text", "suggestions", "completion", "done"]),
        "cache_store": (["rules"], False, None, "passed", True, ["metadata", "text", "text", "completion", "done"]),
    }[case]
    candidate = "공식 답변 [문서1]"
    guard = (
        "확인 필요: 검색된 공식 자료만으로는 생성 후보 답변을 충분히 뒷받침하기 어렵습니다. "
        "근거 일치도는 약 20%입니다. 아래 출처에서 원문을 확인한 뒤 판단해 주세요.\n\n"
        "검토 사유: source conflicts with answer\n\n확인할 공식 출처:\n"
        "- [문서1] 공식 규정: https://www.dongguk.edu/rules/1"
    )
    support_note = (
        "---\n\n"
        "혹시 지금 많이 지쳐 있다면, 학사 절차와 별개로 이야기 나눌 곳이 있어요.\n"
        "**동국대 카운슬링센터** 02-2260-3933 (재학생 심리상담) · "
        "**정신건강 위기상담전화** 1577-0199 (24시간)"
    )
    fallback = (
        "제공된 동국대학교 자료에서 질문과 충분히 관련 있는 정보를 찾지 못했습니다.\n\n"
        "정확하지 않은 정보를 추측해서 답변하는 대신, 다음과 같은 방법을 권장합니다:\n"
        "- **질문 구체화**: 학과명, 날짜, 정확한 공지 제목 등을 포함해 주시면 더 나은 결과를 얻을 수 있습니다.\n"
        "- **공식 채널 이용**: 긴급한 사안은 해당 학과 사무실이나 행정 부서에 직접 유선으로 문의하시기 바랍니다."
    )
    head_answers = {
        "generated": (candidate, candidate),
        "fallback": (fallback, fallback),
        "grounding_failed": (guard, guard),
        "grounding_unavailable": (candidate, candidate),
        # HEAD default: the stream already emitted the candidate before a
        # failed grounding check, so replace cannot retract its text.
        "grounding_failed_unbuffered": (guard, candidate + "\n\n" + guard),
        "grounding_failed_unbuffered_append": (candidate + "\n\n" + guard,) * 2,
        "distress_replace": (guard, guard + "\n\n" + support_note),
        "distress_append": (
            candidate + "\n\n" + support_note + "\n\n" + guard,
            candidate + "\n\n" + guard + "\n\n" + support_note,
        ),
        "active_notice": (candidate, candidate),
        "cache_hit": ("캐시 답변", "캐시 답변"),
        "cache_store": (candidate, candidate),
    }[case]
    head_source = {
        "chunk_id": "rules:1",
        "citation_number": 1,
        "final_score": None,
        "hybrid_score": 0.95,
        "metadata": {
            "campus_scope": "seoul", "chunk_id": "rules:1",
            "matched_query": question, "published_at": "2026-09-01",
            "source": "rules", "title": "공식 규정",
            "url": "https://www.dongguk.edu/rules/1",
        },
        "published_at": "2026-09-01",
        "recency_score": None,
        "snippet": "공식 규정 내용",
        "sort_date": None,
        "source": "rules",
        "source_ref": "sha256:058267b1a261dae2748f36d3310bc85acfbc146f4e72848e415e8933c52e4809",
        "sparse_score": None,
        "url": "https://www.dongguk.edu/rules/1",
        "title": "공식 규정",
        "vector_score": None,
    }
    head_sources = [] if case in {"fallback", "cache_hit"} else [head_source]
    head_citations = "" if case in {"fallback", "cache_hit"} else "- 공식 규정 내용 (2026-09-01) — https://www.dongguk.edu/rules/1"
    head_suggestions = ["관련 규정은 어디서 확인해?"] if case == "cache_hit" else []
    head_resolved_intents = [] if case == "fallback" else ["rules"]
    head_grounding_score = 0.2 if case in failed_cases else (0.9 if case in {"active_notice", "cache_store"} else None)
    head_relevance_score = 0.3 if case in failed_cases else (0.9 if case in {"active_notice", "cache_store"} else None)
    head_response_fields = {
        "citations": head_citations,
        "route": expected[0],
        "resolved_intents": head_resolved_intents,
        "sources": head_sources,
        "suggested_questions": head_suggestions,
        "suggested_question_details": [],
        "grounded": expected[4],
        "grounding_score": head_grounding_score,
        "relevance_score": head_relevance_score,
        "verification_status": expected[3],
        "fallback_triggered": expected[1],
        "fallback_reason": expected[2],
    }
    head_text_chunks = {
        "generated": ["공식 ", "답변 [문서1]"],
        "fallback": [fallback],
        "grounding_failed": [guard],
        "grounding_unavailable": ["공식 ", "답변 [문서1]"],
        "grounding_failed_unbuffered": ["공식 ", "답변 [문서1]", "\n\n" + guard],
        "grounding_failed_unbuffered_append": ["공식 ", "답변 [문서1]", "\n\n" + guard],
        "distress_replace": [guard + "\n\n" + support_note],
        "distress_append": [candidate + "\n\n" + guard + "\n\n" + support_note],
        "active_notice": [candidate],
        "cache_hit": ["캐시 답변"],
        "cache_store": ["공식 ", "답변 [문서1]"],
    }[case]
    head_metadata = {
        "type": "metadata", "sources": head_sources, "citations": head_citations,
        "route": expected[0], "fallback_triggered": expected[1],
    }
    if case == "fallback":
        head_metadata["fallback_reason"] = "no_results"
    head_completion = {
        "type": "completion", "grounded": expected[4],
        "grounding_score": head_grounding_score,
        "relevance_score": head_relevance_score,
        "verification_status": expected[3],
        "suggested_questions": head_suggestions,
        "suggested_question_details": [],
        "resolved_intents": head_resolved_intents,
        "fallback_reason": expected[2],
        "sources": head_sources,
    }
    head_events = [head_metadata]
    if case in failed_cases and case not in {"grounding_failed_unbuffered", "grounding_failed_unbuffered_append"}:
        head_events.append({"type": "grounding", "grounded": False, "score": 0.2, "reason": "source conflicts with answer"})
    for chunk in head_text_chunks:
        head_events.append({"type": "text", "content": chunk})
        if case in {"grounding_failed_unbuffered", "grounding_failed_unbuffered_append"} and chunk == "답변 [문서1]":
            head_events.append({"type": "grounding", "grounded": False, "score": 0.2, "reason": "source conflicts with answer"})
    if case == "cache_hit":
        head_events.append({"type": "suggestions", "questions": head_suggestions})
    head_events += [head_completion, {"type": "done"}]
    req = service.AskRequest(question=question, session_id="snapshot-session")
    request = SimpleNamespace(state=SimpleNamespace(request_id="snapshot-request"))
    for mode, expected_answer in zip(("ask", "stream"), head_answers):
        before = writes.copy()
        if mode == "ask":
            response = (await service.ask(req, request)).model_dump(exclude={"request_id"})
            json_response = response
        else:
            stream = await service.ask_stream(req, request)
            grounding_checks_before_stream = len(checked_answers)
            body_parts = []
            if case == "active_notice":
                hold_stream_grounding = True
                seen_events = asyncio.Queue()

                async def consume_stream():
                    async for item in stream.body_iterator:
                        body_parts.append(item)
                        for line in item.splitlines():
                            if line.startswith("data: "):
                                seen_events.put_nowait(json.loads(line[6:]))

                consumer = asyncio.create_task(consume_stream())
                try:
                    metadata = await asyncio.wait_for(seen_events.get(), timeout=10)
                    assert metadata["type"] == "metadata"
                    await asyncio.wait_for(grounding_entered.wait(), timeout=10)
                    assert len(checked_answers) == grounding_checks_before_stream + 1
                    # With global buffering off, active notices still must not
                    # emit answer text while grounding is pending.
                    with pytest.raises(asyncio.TimeoutError):
                        await asyncio.wait_for(seen_events.get(), timeout=0.05)
                    assert not consumer.done()
                finally:
                    release_grounding.set()
                    await asyncio.wait_for(consumer, timeout=10)
            else:
                async for item in stream.body_iterator:
                    body_parts.append(item)
            body = "".join(body_parts)
            events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
            response = response_from_events(
                events, json_response | {"answer": expected_answer},
                expected_grounding_reason="source conflicts with answer" if case in failed_cases else None,
            )
            assert [{key: value for key, value in event.items() if key not in {"request_id", "retrieval_mode", "degraded_datasets"}} for event in events] == head_events
            assert [event["type"] for event in events] == expected[5]
        assert (response["route"], response["fallback_triggered"], response["fallback_reason"],
                response["verification_status"], response["grounded"]) == expected[:5]
        assert {key: value for key, value in response.items() if key not in {"retrieval_mode", "degraded_datasets"}} == {"answer": expected_answer, **head_response_fields}
        assert response["retrieval_mode"] is None
        assert response["degraded_datasets"] == []
        assert response["answer"] == expected_answer
        assert response["sources"] == head_sources
        assert response["citations"] == head_citations
        assert response["suggested_questions"] == head_suggestions
        assert response["suggested_question_details"] == []
        assert writes["log"] - before["log"] == (0 if case == "cache_hit" else 1)
        assert writes["cache"] - before["cache"] == (1 if case == "cache_store" else 0)
        assert writes["ground"] - before["ground"] == (1 if case in failed_cases | {"active_notice", "cache_store"} else 0)
        if case != "cache_hit":
            args, kwargs = log_calls[-1]
            assert (args[0], args[1], args[2], args[4], args[5], args[6], args[7]) == (
                "snapshot-request", "snapshot-session", question,
                response["route"], response["answer"],
                response["fallback_triggered"], response["fallback_reason"],
            )
            assert args[18] == (
                None if mode == "stream" and case == "fallback"
                else json.dumps([], ensure_ascii=False)
            )
            if kwargs:
                assert set(kwargs) == {"source_traces"}
                assert [(trace["rank"], trace["chunk_id"]) for trace in kwargs["source_traces"]] == [(1, "rules:1")]
        if case == "cache_store":
            args, kwargs = cache_calls[-1]
            assert args[0] == question
            assert args[1] == service._semantic_cache_namespace(req.major)
            assert args[2]["answer"] == response["answer"]
            assert args[2]["verification_status"] == response["verification_status"]
            assert kwargs == {}
    if case.startswith("distress_"):
        assert checked_answers == [candidate + "\n\n" + support_note, candidate]
