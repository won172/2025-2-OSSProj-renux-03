"""Pre-retrieval decisions and endpoint transport parity, without network calls."""
import json
from dataclasses import FrozenInstanceError
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from api import rag_service as service
from src.services.direct_answer import DirectAnswer
from src.services.grounding import GroundingResult

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
                       "resolved_intents", "fallback_reason", "sources"},
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
    }


async def assert_endpoint_parity(monkeypatch, question=QUESTION, *, expected_grounding_reason=None):
    temporal_context = service.TemporalContext(
        as_of=AS_OF, academic_year=2026, semester=2, phase="학기중")
    monkeypatch.setattr(service, "_request_temporal_context", lambda _req: temporal_context)
    monkeypatch.setattr(service, "_save_rag_evaluation_log", lambda *_a, **_kw: None)
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
async def test_source_bearing_retrieval_has_full_transport_parity(monkeypatch, grounded):
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
    }])

    async def retrieve(**kwargs):
        assert kwargs["allow_wise"] is False
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
    assert answer.citations.startswith("- 바이오메디캠퍼스 학사 규정")
    assert answer.verification_status == (
        service.VERIFICATION_PASSED if grounded else service.VERIFICATION_FAILED
    )
    assert events[0]["sources"] and events[0]["citations"]
    if not grounded:
        assert [event["type"] for event in events] == [
            "metadata", "grounding", "text", "completion", "done",
        ]
