"""Product regressions: multi-hop corrections, citation references and API commits."""
import json
from datetime import date
from types import SimpleNamespace

import pytest
import pandas as pd
from langchain_core.messages import AIMessage, AIMessageChunk

from api import rag_service as service
from src.services import langchain_chat as chat
from src.services import query_analysis
from src.services.conversation import (
    effective_scope_query,
    _document_numbers,
    followup_source_clarification, history_allows_context_rewrite,
    needs_context_rewrite, relevant_history, rewrite_with_context,
)
from src.services.conversation_history import HistoryStore
from src.services.grounding import GroundingResult
from src.services.campus_scope import query_explicitly_requests_wise
from src.utils import department_match
from tests.test_grounding_verification_status import _patch_generated_path
from tests.test_query_plan import configure


@pytest.fixture(autouse=True)
def indexed_departments(monkeypatch):
    # The empty-DB checkout has no course CSV. Supply the catalog boundary,
    # keeping actual department resolution and filter construction under test.
    monkeypatch.setattr(department_match, "_iter_source_departments", lambda: ["통계학과", "컴퓨터공학과"])
    monkeypatch.setattr(department_match, "_iter_aliases", lambda: [])
    department_match._department_entries.cache_clear()
    yield
    department_match._department_entries.cache_clear()


def history_turn(question, title, **cue):
    return (f"사용자: {question}\n동똑이: 오래된 답변은 현재 근거가 아님\n이전 출처(검색 단서): "
            + json.dumps({"title": title, **cue}, ensure_ascii=False) + "\n")


@pytest.mark.parametrize("question,old_title,correction,new_title,absent,present", [
    ("2024학번 통계학과 졸업요건 알려줘", "2024학번 통계학과 졸업요건",
     "아니 2026학번 기준으로", "2026학번 통계학과 졸업요건", "2024", "2026학번"),
    ("WISE캠퍼스 휴학 규정 알려줘", "WISE캠퍼스 휴학 규정",
     "아니 서울캠퍼스 기준으로 알려줘", "서울캠퍼스 휴학 규정", "WISE", "서울캠퍼스"),
    ("컴퓨터공학과 전공필수 과목 알려줘", "컴퓨터공학과 전공필수",
     "아니 통계학과 기준으로 알려줘", "통계학과 전공필수", "컴퓨터공학과", "통계학과"),
])
def test_newest_constraint_wins_during_correction_and_later_pronoun_followup(
    question, old_title, correction, new_title, absent, present,
):
    before = history_turn(question, old_title)
    immediate = rewrite_with_context(correction, before)
    assert present in immediate and absent not in immediate
    history = before + history_turn(correction, new_title)
    for followup in ("그 문서 자세히 알려줘", "신청 조건도?"):
        rewritten = rewrite_with_context(followup, history)
        assert followup in rewritten and present in rewritten
        assert absent not in rewritten
        assert " 학번" not in rewritten and " 학년도" not in rewritten
        safe = relevant_history(followup, history)
        assert absent not in safe and present in safe


def scholarship_history():
    return ("사용자: 교내 장학금과 국가 장학금 알려줘\n동똑이: 장학금 안내\n"
            + "\n".join("이전 출처(검색 단서): " + json.dumps(cue, ensure_ascii=False) for cue in [
                {"citation_number": 1, "source_ref": "sha256:one", "document_key": "notices:1",
                 "title": "교내 성적우수 장학 안내"},
                {"citation_number": 2, "source_ref": "sha256:two", "document_key": "notices:2",
                 "title": "국가 장학금 2차 신청 안내"},
            ]))


@pytest.mark.parametrize("query", ["[문서2] 자세히 알려줘", "두 번째 문서 자세히 알려줘", "2번째 자료 다시 보여줘"])
def test_numbered_reference_resolves_only_its_intended_source(query):
    history = scholarship_history()
    assert history_allows_context_rewrite(query, history)
    assert needs_context_rewrite(query, history)
    assert not followup_source_clarification(query, history)
    rewritten = rewrite_with_context(query, history)
    assert query in rewritten and "국가 장학금 2차 신청 안내" in rewritten
    assert "교내" not in rewritten
    safe = relevant_history(query, history)
    assert "sha256:two" in safe and "notices:2" in safe
    assert "sha256:one" not in safe and "notices:1" not in safe


@pytest.mark.parametrize("query", [
    "이전에 참고한 문서 다시 보여줘", "아까 확인한 자료 다시 보여줘",
    "한 문서 다시 보여줘", "연구세 번째 문서 알려줘", "고문서2번 알려줘",
])
def test_verb_suffixes_and_noun_substrings_are_never_ordinals(query):
    assert _document_numbers(query) is None


@pytest.mark.parametrize("query", ["이전에 참고한 문서 다시 보여줘", "아까 확인한 자료 다시 보여줘"])
def test_generic_singular_document_reference_clarifies_multiple_sources_but_continues_one(query):
    multiple = (scholarship_history()
                .replace("교내 장학금과 국가 장학금 알려줘", "장학금 알려줘")
                .replace("교내 성적우수 장학 안내", "교내 장학금 안내")
                .replace("국가 장학금 2차 신청 안내", "국가 장학금 안내"))
    assert followup_source_clarification(query, multiple)
    assert rewrite_with_context(query, multiple) == query
    assert relevant_history(query, multiple) == ""
    single = history_turn("장학금 알려줘", "교내 장학금 안내", citation_number=1,
                          source_ref="sha256:one", document_key="notices:1")
    assert not followup_source_clarification(query, single)
    assert "교내 장학금 안내" in rewrite_with_context(query, single)


@pytest.mark.parametrize("query,expected", [
    ("[문서2] 자세히 알려줘", 2), ("두 번째 문서 자세히 알려줘", 2),
    ("첫 번째 문서 다시 보여줘", 1),
])
def test_explicit_ordinal_tokens_still_choose_the_labeled_source(query, expected):
    assert _document_numbers(query) == [expected]
    safe = relevant_history(query, scholarship_history())
    assert f"sha256:{'one' if expected == 1 else 'two'}" in safe
    assert f"sha256:{'two' if expected == 1 else 'one'}" not in safe


@pytest.mark.parametrize("history", ["", scholarship_history(), scholarship_history() +
    '\n이전 출처(검색 단서): {"citation_number": 3, "title": "중복 번호 하나"}' +
    '\n이전 출처(검색 단서): {"citation_number": 3, "title": "중복 번호 둘"}'])
def test_nonexistent_or_ambiguous_source_reference_requires_clarification(history):
    query = "[문서3] 자세히 알려줘"
    assert followup_source_clarification(query, history)
    assert rewrite_with_context(query, history) == query


def test_slot_followups_inherit_active_thread_but_new_topics_do_not():
    history = history_turn("교내 장학금 신청 안내 알려줘", "성적우수 장학 안내")
    history += history_turn("그 문서 다시 보여줘", "성적우수 장학 안내")
    assert "교내 장학금" in rewrite_with_context("신청 조건도?", history)
    for query in ("샤갈", "오늘 학식은?", "이번 학기 기숙사 신청 조건은?", "그럼 휴학 규정 알려줘"):
        assert not needs_context_rewrite(query, history)
        assert rewrite_with_context(query, history) == query


@pytest.mark.asyncio
async def test_analysis_uses_effective_user_year_not_old_answer_or_title(monkeypatch):
    history = history_turn("2024학번 통계학과 졸업요건", "2024학번 졸업요건")
    history += history_turn("아니 2026학번 기준으로", "2026학번 졸업요건")
    captured = {}
    class Chain:
        async def ainvoke(self, payload):
            captured.update(payload)
            return {"raw": None, "parsed": query_analysis._QueryAnalysisOutput(
                normalized_question="그 문서 자세히 알려줘 2024학번 졸업요건", intent="rules",
                entities=[], time_focus="none", search_queries=[], needs_clarification=False,
                clarification_reason=None, is_compound=False, sub_queries=[]), "parsing_error": None}
    monkeypatch.setattr(query_analysis, "_build_analysis_chain", Chain)
    result = await query_analysis.analyze_query("그 문서 자세히 알려줘", history)
    assert "2026학번" in captured["history"] and "2024" not in captured["history"]
    assert "오래된 답변" not in captured["history"]
    assert result.normalized_question == "그 문서 자세히 알려줘"


async def plan_for(monkeypatch, query, history, *, analysis="disabled", major="컴퓨터공학과"):
    configure(monkeypatch, None)
    monkeypatch.setattr(service, "get_recent_history_text", lambda _session: history)
    monkeypatch.setattr(service, "query_explicitly_requests_wise", query_explicitly_requests_wise)
    monkeypatch.setattr(service, "USE_QUERY_ANALYSIS", analysis != "disabled")
    if analysis != "disabled":
        async def analyze(*_a, **_kw):
            return None if analysis == "failed" else service.QueryAnalysisResult(
                normalized_question=query, intent="courses")
        monkeypatch.setattr(service, "analyze_query", analyze)
    return await service._plan_query(
        req=service.AskRequest(question=query, major=major), raw_query=query,
        temporal_context=SimpleNamespace(as_of=date(2026, 10, 17)), request_id="continuity",
        session_id="a", stage_timings={}, llm_usage=[], mode="ask")


@pytest.mark.asyncio
@pytest.mark.parametrize("analysis", ["disabled", "failed", "successful"])
async def test_api_followups_retrieve_with_context_even_without_usable_analysis(monkeypatch, analysis):
    history = history_turn("통계학과 전공필수 과목 알려줘", "통계학과 전공필수", document_key="courses:1")
    history += history_turn("그 문서 다시 보여줘", "통계학과 전공필수", document_key="courses:1")
    plan = await plan_for(monkeypatch, "신청 조건도?", history, analysis=analysis)
    assert plan.direct_handler is None
    assert "통계학과" in plan.query_for_retrieval and "통계학과" in plan.semantic_query
    assert "신청 조건도?" in plan.retrieval_queries
    assert plan.filters["where"] == {"major": {"$eq": "통계학과"}}
    # Referenced department can scope public courses, never grant notice access.
    assert plan.filters["notice_visibility"] == service._notice_visibility_where_filter("컴퓨터공학과")
    assert plan.allow_wise is False and plan.filters["campus"] == "seoul_bmc"


@pytest.mark.asyncio
async def test_corrected_campus_is_not_blocked_by_old_wise_but_actual_wise_stays_blocked(monkeypatch):
    history = history_turn("WISE캠퍼스 휴학 규정 알려줘", "WISE캠퍼스 휴학 규정")
    correction = "아니 서울캠퍼스 기준으로 알려줘"
    plan = await plan_for(monkeypatch, correction, history)
    assert plan.direct_handler is None and "WISE" not in plan.query_for_retrieval
    history += history_turn(correction, "서울캠퍼스 휴학 규정")
    assert (await plan_for(monkeypatch, "그 문서 자세히 알려줘", history)).direct_handler is None
    actual = await plan_for(monkeypatch, "아니 WISE캠퍼스 기준으로 알려줘", history)
    assert actual.direct_handler == "campus_out_of_scope"


@pytest.mark.asyncio
async def test_numbered_reference_api_clarifies_missing_and_retrieves_selected_title(monkeypatch):
    missing = await plan_for(monkeypatch, "[문서7] 자세히 알려줘", scholarship_history())
    assert missing.direct_handler == "clarification"
    selected = await plan_for(monkeypatch, "[문서2] 자세히 알려줘", scholarship_history())
    assert selected.direct_handler is None
    assert "국가 장학금 2차 신청 안내" in selected.query_for_retrieval
    assert all("교내" not in query for query in selected.retrieval_queries)


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["이전에 참고한 문서 다시 보여줘", "아까 확인한 자료 다시 보여줘"])
async def test_generic_singular_reference_planner_does_not_guess_first_source(monkeypatch, query):
    plan = await plan_for(monkeypatch, query, scholarship_history())
    assert plan.direct_handler == "clarification" and plan.reason == "source_reference_unresolved"


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [
    "2026학년도 1학기 수강신청 일정 알려줘", "2026-1 수강신청 일정 알려줘",
])
@pytest.mark.parametrize("later", [False, True])
async def test_corrected_semester_planner_and_real_candidate_filter_keep_only_second_semester(monkeypatch, initial, later):
    history = history_turn(initial, initial.removesuffix(" 알려줘"))
    correction = "아니 2학기 기준으로 알려줘"
    if later:
        history += history_turn(correction, "2026학년도 2학기 수강신청 일정")
    query = "자세히 알려줘" if later else correction
    plan = await plan_for(monkeypatch, query, history)
    assert plan.direct_handler is None and query in plan.query_for_retrieval
    assert "1학기" not in plan.query_for_retrieval and "2026-1" not in plan.query_for_retrieval
    assert service._query_academic_period(plan.query_for_retrieval) == (2026, "2")
    assert service._query_academic_period(plan.semantic_query) == (2026, "2")
    candidates = pd.DataFrame([
        {"candidate_id": "first", "chunk_id": "schedule:1", "dataset": "schedule",
         "title": "2026학년도 1학기 수강신청 일정", "chunk_text": "1학기 신청 일정", "schedule_start": "2026-02-10"},
        {"candidate_id": "second", "chunk_id": "schedule:2", "dataset": "schedule",
         "title": "2026학년도 2학기 수강신청 일정", "chunk_text": "2학기 신청 일정", "schedule_start": "2026-08-10"},
    ])
    assert service._refine_final_candidate_scope(plan.semantic_query, candidates)["candidate_id"].tolist() == ["second"]
    safe = relevant_history(query, history)
    assert "1학기" not in safe and "2026-1" not in safe
    assert "2026" in safe


@pytest.mark.parametrize("old,new,absent,present", [
    ("2026학년도 여름 계절학기 수강신청 일정", "아니 2학기 기준으로 알려줘", "여름", "2학기"),
    ("2026학년도 1학기 수강신청 일정", "아니 겨울 계절학기 기준으로 알려줘", "1학기", "겨울 계절학기"),
])
def test_semester_correction_preserves_season_and_user_year_boundaries(old, new, absent, present):
    rewritten = rewrite_with_context(new, history_turn(old, old))
    assert present in rewritten and absent not in rewritten
    assert "2026" in rewritten and "2025" not in rewritten


def test_semester_only_correction_does_not_inherit_year_from_source_title():
    history = history_turn("1학기 수강신청 일정 알려줘", "2026학년도 1학기 수강신청 일정")
    history += history_turn("아니 2학기 기준으로 알려줘", "2026학년도 2학기 수강신청 일정")
    rewritten = rewrite_with_context("자세히 알려줘", history)
    assert "2학기" in rewritten and "1학기" not in rewritten and "2026" not in rewritten


def install_local_history(monkeypatch):
    store = HistoryStore(lambda _s: None, lambda _e: None)
    monkeypatch.setattr(chat, "_HISTORY_STORE", store)
    monkeypatch.setattr(service, "get_recent_history_text", chat.get_recent_history_text)
    monkeypatch.setattr(service, "append_manual_history", chat.append_manual_history)
    return store


async def run_api(mode, *, query="휴학 신청 방법 알려줘", session="a"):
    req = service.AskRequest(question=query, session_id=session)
    request = SimpleNamespace(state=SimpleNamespace(request_id="memory-test"))
    if mode == "ask":
        return (await service.ask(req, request)).model_dump()
    stream = await service.ask_stream(req, request)
    events = []
    async for item in stream.body_iterator:
        for line in item.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ask", "stream"])
@pytest.mark.parametrize("status", ["passed", "failed", "unavailable", "disabled"])
async def test_api_commits_only_after_grounding_never_memorises_rejected_candidate(monkeypatch, mode, status):
    result = GroundingResult(checked=status in {"passed", "failed"},
                             grounded=True if status == "passed" else False if status == "failed" else None,
                             score=0.95 if status == "passed" else 0.1, reason="fixture",
                             status="unavailable" if status == "disabled" else status)
    _patch_generated_path(monkeypatch, result)
    store = install_local_history(monkeypatch)
    if status == "disabled":
        monkeypatch.setattr(service, "RAG_GROUNDING_CHECK_ENABLED", False)
    async def check(*_a, **_kw):
        assert store.get("a").messages == []  # Candidate must not already be in history.
        return result
    monkeypatch.setattr(service, "check_answer_grounding", check)
    await run_api(mode)
    messages = store.get("a").messages
    assert len(messages) == 2 and messages[0].content == "휴학 신청 방법 알려줘"
    candidate = "휴학은 학기 개시 전까지 신청합니다 [문서1]."
    if status == "passed":
        assert messages[1].content == candidate
        assert messages[1].additional_kwargs["source_cues"][0]["citation_number"] == 1
    else:
        assert candidate not in messages[1].content
        assert messages[1].additional_kwargs["source_cues"] == []
    assert store.get("b").messages == []


@pytest.mark.asyncio
async def test_followup_with_no_current_evidence_returns_no_answer_instead_of_old_facts(monkeypatch):
    shortlist = service._build_balanced_shortlist
    _patch_generated_path(monkeypatch, None)
    monkeypatch.setattr(service, "_build_balanced_shortlist", shortlist)
    store = install_local_history(monkeypatch)
    chat.append_manual_history("a", "휴학 규정 알려줘", "OUTDATED ASSERTION", [{
        "source": "rules", "title": "휴학 규정", "chunk_id": "rules:1", "metadata": {"doc_id": "rules:1"}}])
    retrievals = []
    async def retrieve(**kwargs):
        retrievals.append(kwargs)
        return [], False, []
    monkeypatch.setattr(service, "_retrieve_frames_for_queries", retrieve)
    monkeypatch.setattr(service, "generate_langchain_answer", lambda **_kw: pytest.fail("no current evidence"))
    response = await run_api("ask", query="그 문서 다시 보여줘")
    assert any("휴학 규정" in q for q in retrievals[0]["queries"])
    assert "OUTDATED ASSERTION" not in json.dumps(retrievals, default=str)
    assert response["fallback_triggered"] and response["sources"] == []
    assert "OUTDATED ASSERTION" not in response["answer"]
    assert len(store.get("a").messages) == 4


def test_selected_second_document_remains_the_subject_of_next_slot_followup():
    history = scholarship_history() + "\n" + history_turn(
        "[문서2] 자세히 알려줘", "국가 장학금 2차 신청 안내",
        citation_number=2, source_ref="sha256:two", document_key="notices:2")
    rewritten = rewrite_with_context("신청 조건도?", history)
    assert "국가 장학금 2차 신청 안내" in rewritten
    assert "교내" not in rewritten


def test_numbered_selection_and_later_slot_keep_user_cohort_even_when_title_omits_it():
    history = scholarship_history().replace("교내 장학금과", "2026학번 교내 장학금과")
    history += "\n" + history_turn("[문서2] 자세히 알려줘", "국가 장학금 2차 신청 안내",
                                  citation_number=2, document_key="notices:2")
    for query in ("신청 조건도?", "[문서2] 자세히 알려줘"):
        assert "2026학번" in rewrite_with_context(query, history)
        assert "2026학번" in relevant_history(query, history)


def test_explicit_negated_cohort_correction_preserves_literal_but_filters_new_cohort():
    history = history_turn("2024학번 통계학과 졸업요건 알려줘", "2024학번 졸업요건")
    query = "2024학번 말고 2026학번 기준으로"
    assert needs_context_rewrite(query, history)
    rewritten = rewrite_with_context(query, history)
    assert query in rewritten and "통계학과 졸업요건" in rewritten
    assert "2024" not in effective_scope_query(rewritten)
    assert "2026학번" in effective_scope_query(rewritten)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ask", "stream"])
async def test_primary_provider_failure_falls_back_without_premature_history_commit(monkeypatch, mode):
    store = install_local_history(monkeypatch)
    calls = []
    class LLM:
        def __init__(self, provider):
            self.provider = provider
        async def ainvoke(self, _messages):
            calls.append(self.provider)
            if self.provider == "openai":
                raise RuntimeError("provider offline")
            return AIMessage(content="fallback candidate")
        async def astream(self, _messages):
            calls.append(self.provider)
            if self.provider == "openai":
                raise RuntimeError("provider offline before first token")
            yield AIMessageChunk(content="fallback candidate")
    monkeypatch.setattr(chat, "_primary_provider", lambda: "openai")
    monkeypatch.setattr(chat, "LLM_FALLBACK_ENABLED", True)
    monkeypatch.setattr(chat, "_get_chat_llm", LLM)
    if mode == "ask":
        candidate = await chat.generate_langchain_answer("질문", "현재 공식 자료", "a")
    else:
        candidate = "".join([chunk async for chunk in chat.generate_langchain_answer_stream("질문", "현재 공식 자료", "a")])
    assert candidate == "fallback candidate" and calls == ["openai", "ollama"]
    assert store.get("a").messages == []


@pytest.mark.asyncio
async def test_interrupted_stream_does_not_store_an_incomplete_turn(monkeypatch):
    store = install_local_history(monkeypatch)
    class LLM:
        async def astream(self, _messages):
            yield AIMessageChunk(content="incomplete candidate")
            raise RuntimeError("stream disconnected")
    monkeypatch.setattr(chat, "_get_chat_llm", lambda _provider: LLM())
    with pytest.raises(RuntimeError, match="stream disconnected"):
        async for _chunk in chat.generate_langchain_answer_stream("질문", "공식 자료", "a"):
            pass
    assert store.get("a").messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ask", "stream"])
async def test_generation_helpers_read_history_but_never_commit_unvalidated_candidates(monkeypatch, mode):
    store = install_local_history(monkeypatch)
    chat.append_manual_history("a", "첫 질문", "완료된 답변")
    captured = []
    class LLM:
        async def ainvoke(self, messages):
            captured.extend(messages)
            return AIMessage(content="검증 전 후보")
        async def astream(self, messages):
            captured.extend(messages)
            yield AIMessageChunk(content="검증 전 후보")
    monkeypatch.setattr(chat, "_get_chat_llm", lambda _provider: LLM())
    if mode == "ask":
        assert await chat.generate_langchain_answer("다음 질문", "현재 공식 자료", "a") == "검증 전 후보"
    else:
        assert "".join([chunk async for chunk in chat.generate_langchain_answer_stream("다음 질문", "현재 공식 자료", "a")]) == "검증 전 후보"
    assert any(message.content == "완료된 답변" for message in captured)
    assert len(store.get("a").messages) == 2
