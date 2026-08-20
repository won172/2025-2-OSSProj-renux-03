from __future__ import annotations

import inspect
import asyncio
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api.rag_service as rag_service  # noqa: E402
import src.services.evidence_selector as evidence_selector  # noqa: E402
import src.services.langchain_chat as langchain_chat  # noqa: E402
from src.services.evidence_selector import (  # noqa: E402
    EvidenceGroupDecision,
    EvidenceSelectionDecision,
)


DATASETS = ["notices", "rules", "schedule", "courses", "staff", "meals"]


def _frame(dataset: str, count: int = 5, query: str = "question") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "chunk_id": f"{dataset}-{index}",
                "chunk_text": f"{dataset} evidence {index}",
                "dataset": dataset,
                "source": dataset,
                "title": f"{dataset} title {index}",
                "hybrid_score": 0.9 - index * 0.1,
                "matched_query": query,
            }
            for index in range(count)
        ]
    )


def test_intent_scoped_route_is_used_by_both_endpoints(monkeypatch):
    monkeypatch.setattr(rag_service.rag_config, "RAG_SEARCH_ALL_DATASETS", False)
    schedule_analysis = rag_service.QueryAnalysisMeta(
        result=rag_service.QueryAnalysisResult(
            normalized_question="이번 달 학사일정 알려줘",
            intent="schedule",
        ),
        used=True,
    )
    application_analysis = rag_service.QueryAnalysisMeta(
        result=rag_service.QueryAnalysisResult(
            normalized_question="남산학사 2학기 입사 신청 기간",
            intent="schedule",
        ),
        used=True,
    )

    assert rag_service._resolve_retrieval_route("이번 달 학사일정 알려줘", schedule_analysis) == ["schedule"]
    assert rag_service._resolve_retrieval_route("남산학사 2학기 입사 신청 기간", application_analysis) == ["schedule", "notices"]
    assert rag_service._resolve_retrieval_route(
        "재수강을 하고 싶은데 어떻게 해야 해?",
        rag_service.QueryAnalysisMeta(
            result=rag_service.QueryAnalysisResult(normalized_question="재수강 방법", intent="rules"),
            used=True,
        ),
    ) == ["rules", "notices"]

    ask_source = inspect.getsource(rag_service.ask)
    stream_source = inspect.getsource(rag_service.ask_stream)

    assert "_resolve_retrieval_route(" in ask_source
    assert "_resolve_retrieval_route(" in stream_source
    assert "_routerless_retrieval_route()" not in ask_source
    assert "_routerless_retrieval_route()" not in stream_source
    assert "_select_answer_evidence" in ask_source
    assert "_select_answer_evidence" in stream_source


def test_full_corpus_route_requires_explicit_override(monkeypatch):
    monkeypatch.setattr(rag_service.rag_config, "RAG_SEARCH_ALL_DATASETS", True)

    assert rag_service._resolve_retrieval_route(
        "이번 달 학사일정 알려줘",
        rag_service.QueryAnalysisMeta(result=None),
    ) == DATASETS


def test_balanced_shortlist_collects_all_six_datasets_with_equal_quota():
    shortlist = rag_service._build_balanced_shortlist([_frame(dataset) for dataset in DATASETS])

    assert set(shortlist["dataset"]) == set(DATASETS)
    assert len(shortlist) == 18
    assert shortlist.groupby("dataset").size().to_dict() == {dataset: 3 for dataset in DATASETS}
    assert shortlist[shortlist["dataset_rank"] == 1]["dataset"].tolist() == DATASETS


def test_balanced_shortlist_keeps_each_datasets_strongest_sparse_candidate():
    frame = _frame("schedule", count=5)
    frame["sparse_score"] = [0.05, 0.04, 0.03, 0.92, 0.02]

    shortlist = rag_service._build_balanced_shortlist([frame], per_dataset=3)

    assert "schedule-3" in shortlist["chunk_id"].tolist()
    assert shortlist.iloc[0]["chunk_id"] == "schedule-0"


def test_current_notice_query_deterministically_excludes_superseded_versions():
    frame = _frame("notices", count=3)
    frame["title"] = [
        "2024학년도 장학금 신청 안내",
        "2026학년도 장학금 신청 안내",
        "분류되지 않은 장학금 안내",
    ]
    frame["is_latest"] = [False, True, None]

    shortlist = rag_service._build_balanced_shortlist(
        [frame],
        query="장학금 신청 안내",
        per_dataset=3,
    )

    assert "notices-0" not in shortlist["chunk_id"].tolist()
    assert {"notices-1", "notices-2"} <= set(shortlist["chunk_id"])


def test_explicit_historical_year_preserves_superseded_notice_versions():
    frame = _frame("notices", count=2)
    frame["title"] = [
        "2023학년도 장학금 신청 안내",
        "2026학년도 장학금 신청 안내",
    ]
    frame["is_latest"] = [False, True]

    shortlist = rag_service._build_balanced_shortlist(
        [frame],
        query="2023학년도 장학금 신청 안내",
        per_dataset=2,
    )

    assert set(shortlist["chunk_id"]) == {"notices-0", "notices-1"}


def test_registration_search_defaults_to_undergraduate_audience():
    frame = _frame("schedule", count=3)
    frame["audience"] = ["graduate", "undergraduate", "common"]
    frame["hybrid_score"] = [0.99, 0.60, 0.50]

    shortlist = rag_service._build_balanced_shortlist(
        [frame],
        query="수강신청 언제야?",
        per_dataset=3,
    )

    assert "schedule-0" not in shortlist["chunk_id"].tolist()
    assert shortlist.iloc[0]["chunk_id"] == "schedule-1"


def test_explicit_graduate_query_excludes_undergraduate_candidates():
    frame = _frame("schedule", count=3)
    frame["audience"] = ["undergraduate", "graduate", "common"]
    frame["hybrid_score"] = [0.99, 0.60, 0.50]

    shortlist = rag_service._build_balanced_shortlist(
        [frame],
        query="대학원 수강신청 언제야?",
        per_dataset=3,
    )

    assert "schedule-0" not in shortlist["chunk_id"].tolist()
    assert shortlist.iloc[0]["chunk_id"] == "schedule-1"


def test_schedule_alignment_prefers_requested_year_and_semester():
    hits = pd.DataFrame([
        {"chunk_id": "march", "schedule_start": "2026-03-03", "hybrid_score": 0.52},
        {"chunk_id": "september", "schedule_start": "2026-09-01", "hybrid_score": 0.46},
        {"chunk_id": "old", "schedule_start": "2025-09-01", "hybrid_score": 0.55},
    ])

    aligned = rag_service._apply_schedule_calendar_alignment(
        hits, "2026학년도 2학기 개강일이 언제야?"
    )

    assert aligned.iloc[0]["chunk_id"] == "september"
    assert aligned.iloc[-1]["chunk_id"] == "march"


def _registration_scope_candidates() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "candidate_id": "c-current",
            "chunk_id": "schedule-current",
            "dataset": "schedule",
            "dataset_rank": 1,
            "title": "2026학년도 2학기 수강신청 일정",
            "chunk_text": "2026학년도 2학기 수강신청 기간 안내",
            "schedule_start": "2026-08-10",
            "published_at": "2026-07-01",
        },
        {
            "candidate_id": "c-old-entry",
            "chunk_id": "rules-2022-entry",
            "dataset": "rules",
            "dataset_rank": 1,
            "title": "2022학번 학적 및 학생 관련",
            "chunk_text": "본교 수강신청과 국내 학점교류 학점 인정 기준",
            "published_at": "2022-01-01",
        },
        {
            "candidate_id": "c-external",
            "chunk_id": "notice-uos",
            "dataset": "notices",
            "dataset_rank": 1,
            "title": "2026학년도 여름 계절학기 서울시립대학교 수학 안내",
            "chunk_text": "서울시립대학교 학점교류 신청 안내",
            "published_at": "2026-05-04",
        },
        {
            "candidate_id": "c-timeless",
            "chunk_id": "rules-current",
            "dataset": "rules",
            "dataset_rank": 2,
            "title": "수강신청 시행세칙",
            "chunk_text": "수강신청 정정 및 취소에 관한 현재 시행세칙",
            "published_at": "2022-03-01",
        },
    ])


def test_final_scope_removes_old_entry_year_and_partner_exchange_from_current_registration():
    refined = rag_service._refine_final_candidate_scope(
        "2026-2 수강신청 기간을 알려줘",
        _registration_scope_candidates(),
    )

    assert refined["candidate_id"].tolist() == ["c-current", "c-timeless"]
    assert "c-old-entry" not in refined["candidate_id"].tolist()
    assert "c-external" not in refined["candidate_id"].tolist()


def test_period_mismatch_is_only_removed_when_an_explicit_match_exists():
    candidates = _registration_scope_candidates().query("candidate_id == 'c-current'").copy()
    candidates.loc[:, "title"] = "2026학년도 2학기 수강신청 일정"
    candidates.loc[:, "schedule_start"] = "2026-08-10"

    refined = rag_service._refine_final_candidate_scope(
        "2027-1 수강신청 기간을 알려줘",
        candidates,
    )

    assert refined["candidate_id"].tolist() == ["c-current"]


def test_explicit_partner_exchange_question_keeps_named_institution_evidence():
    candidates = _registration_scope_candidates()
    refined = rag_service._refine_final_candidate_scope(
        "2026학년도 서울시립대학교 여름 계절학기 학점교류 신청 방법",
        candidates,
    )

    assert "c-external" in refined["candidate_id"].tolist()


def test_course_basket_scope_excludes_generic_graduate_registration_schedule():
    candidates = pd.DataFrame([
        {
            "candidate_id": "c-graduate",
            "chunk_id": "schedule-graduate",
            "dataset": "schedule",
            "dataset_rank": 1,
            "title": "2026학년도 2학기 대학원 수강신청",
            "chunk_text": "대학원 수강신청 기간은 7월 31일부터 8월 5일까지",
            "schedule_start": "2026-07-31",
        },
        {
            "candidate_id": "c-basket",
            "chunk_id": "notice-basket",
            "dataset": "rules",
            "dataset_rank": 1,
            "title": "2026학번 수강신청 및 수업관련제도",
            "chunk_text": "학부 재학생 장바구니 기간과 신청 방법",
            "published_at": "2026-07-01",
        },
        {
            "candidate_id": "c-generic",
            "chunk_id": "rules-generic",
            "dataset": "rules",
            "dataset_rank": 1,
            "title": "수강신청 시행세칙",
            "chunk_text": "일반 수강신청 정정 및 취소 안내",
        },
    ])

    refined = rag_service._refine_final_candidate_scope(
        "2학기 수강신청 장바구니 기간은 언제야?",
        candidates,
    )

    assert refined["candidate_id"].tolist() == ["c-basket"]


def test_explicit_graduate_question_keeps_graduate_schedule():
    candidates = pd.DataFrame([
        {
            "candidate_id": "c-graduate",
            "chunk_id": "schedule-graduate",
            "dataset": "schedule",
            "dataset_rank": 1,
            "title": "2026학년도 2학기 대학원 수강신청",
            "chunk_text": "대학원 수강신청 기간 안내",
            "schedule_start": "2026-07-31",
        }
    ])

    refined = rag_service._refine_final_candidate_scope(
        "대학원 2학기 수강신청 기간은 언제야?",
        candidates,
    )

    assert refined["candidate_id"].tolist() == ["c-graduate"]


def test_title_declared_cohort_overrides_incidental_body_year():
    candidates = pd.DataFrame([
        {
            "candidate_id": "c-wrong-title",
            "chunk_id": "rules-2024",
            "dataset": "rules",
            "dataset_rank": 1,
            "title": "2024학번 학적 및 학생 관련",
            "chunk_text": "2022학번 예시와 과거 공과대학 기준을 함께 설명합니다.",
        },
        {
            "candidate_id": "c-correct-title",
            "chunk_id": "rules-2022",
            "dataset": "rules",
            "dataset_rank": 2,
            "title": "2022학번 단과대학별 졸업기준 - 공과대학",
            "chunk_text": "공과대학 졸업 최저학점 기준",
        },
    ])

    refined = rag_service._refine_final_candidate_scope(
        "2022학번 공과대학 졸업 최저학점은 몇 점이야?",
        candidates,
    )

    assert refined["candidate_id"].tolist() == ["c-correct-title"]


def test_restricted_foreign_student_notice_cannot_answer_general_audience():
    foreign_only = pd.DataFrame([
        {
            "candidate_id": "c-foreign-dorm",
            "chunk_id": "notice-foreign-dorm",
            "dataset": "notices",
            "dataset_rank": 1,
            "title": "2026학년도 2학기 외국인 유학생 기숙사 신청 안내",
            "chunk_text": "남산학사 외국인 유학생 입사 신청 기간",
        }
    ])

    general = rag_service._refine_final_candidate_scope(
        "남산학사 2학기 입사 신청 기간은 언제야?",
        foreign_only,
    )
    foreign = rag_service._refine_final_candidate_scope(
        "외국인 유학생 남산학사 2학기 입사 신청 기간은 언제야?",
        foreign_only,
    )

    assert general.empty
    assert foreign["candidate_id"].tolist() == ["c-foreign-dorm"]


def test_contact_question_removes_numberless_staff_when_phone_candidate_exists():
    candidates = pd.DataFrame([
        {
            "candidate_id": "c-manager",
            "chunk_id": "staff-manager",
            "dataset": "staff",
            "dataset_rank": 1,
            "title": "학사지원팀 이** 과장",
            "chunk_text": "학사지원팀 수강신청 담당",
        },
        {
            "candidate_id": "c-phone",
            "chunk_id": "staff-phone",
            "dataset": "staff",
            "dataset_rank": 2,
            "title": "학사지원팀 문의처",
            "chunk_text": "전화번호: 02-2260-3618 / 02-2260-3619",
        },
        {
            "candidate_id": "c-rule",
            "chunk_id": "rules-registration",
            "dataset": "rules",
            "dataset_rank": 1,
            "title": "수강신청 안내",
            "chunk_text": "수강신청 오류 문의는 학사지원팀 담당",
        },
    ])

    refined = rag_service._refine_final_candidate_scope(
        "수강신청 오류는 어느 부서에 전화해야 해?",
        candidates,
    )

    assert refined["candidate_id"].tolist() == ["c-phone", "c-rule"]


def test_parent_context_includes_fact_two_chunks_after_selected_label(monkeypatch):
    cached = pd.DataFrame([
        {
            "chunk_id": f"rule-{position}",
            "doc_id": "scholarship-rule",
            "position": position,
            "title": "장학금 지급에 관한 시행세칙",
            "chunk_text": text,
            "source": "rules",
        }
        for position, text in [
            (1, "장학금 총칙"),
            (2, "장학금 적용 대상"),
            (3, "[장학금 지급에 관한 시행세칙]\n\n우수장학 종류"),
            (4, "동국인재육성장학 수혜자"),
            (5, "수혜자는 평균평점 3.5 이상"),
        ]
    ])
    cache = rag_service.DatasetCache(
        chunks=cached,
        vectorizer=object(),
        matrix=object(),
        chunk_path=Path("unused.parquet"),
        chunk_mtime=0,
        tfidf_mtime=0,
    )
    monkeypatch.setitem(rag_service._datasets, "rules", cache)
    monkeypatch.setattr(rag_service, "PARENT_CONTEXT_ENABLED", True)

    selected = cached.iloc[2].copy()
    selected["dataset"] = "rules"
    selected["campus_allow_wise"] = False
    expanded = rag_service._expand_chunk_with_neighbors(selected)

    assert "우수장학 종류" in expanded
    assert "평균평점 3.5 이상" in expanded


def test_selector_only_receives_refined_candidates_and_keeps_equal_groups(monkeypatch):
    captured: list[str] = []

    async def select_all_remaining(_question, candidates, _usage):
        captured.extend(candidate["candidate_id"] for candidate in candidates)
        return EvidenceSelectionDecision(
            groups=[
                EvidenceGroupDecision(document_ids=[candidate["candidate_id"]], distinction="valid")
                for candidate in candidates
            ]
        )

    monkeypatch.setattr(rag_service, "select_evidence_groups", select_all_remaining)
    selected, did_fallback = asyncio.run(
        rag_service._select_evidence_for_answer(
            "2026-2 수강신청 기간을 알려줘",
            _registration_scope_candidates(),
            [],
        )
    )

    assert did_fallback is False
    assert captured == ["c-current", "c-timeless"]
    assert selected["candidate_id"].tolist() == ["c-current", "c-timeless"]
    assert selected["evidence_group"].tolist() == [1, 2]
    assert selected["citation_number"].tolist() == [1, 2]


def test_staff_contact_enrichment_uses_departments_from_first_hop_evidence():
    schedule = pd.DataFrame([
        {
            "dataset": "schedule",
            "department": "학사지원팀/ 대학원실",
            "hybrid_score": 0.9,
        }
    ])

    queries = rag_service._staff_enrichment_queries(
        "수강신청 오류는 어느 부서에 전화해야 해?", [schedule]
    )

    assert queries == ["학사지원팀 연락처", "대학원실 연락처"]
    assert rag_service._staff_enrichment_queries("수강신청 기간이 언제야?", [schedule]) == []


def test_both_endpoints_apply_staff_contact_enrichment():
    for endpoint in (rag_service.ask, rag_service.ask_stream):
        assert "_enrich_staff_lookup_frames" in inspect.getsource(endpoint)


def test_irrelevant_documents_are_excluded_by_structured_decision():
    shortlist = rag_service._build_balanced_shortlist([_frame("rules", count=3)])
    decision = EvidenceSelectionDecision(
        groups=[EvidenceGroupDecision(document_ids=["c2"], distinction="direct evidence")]
    )

    groups = rag_service._normalize_evidence_groups(decision, set(shortlist["candidate_id"]))
    selected = rag_service._materialize_evidence_groups(shortlist, groups)

    assert groups == [["c2"]]
    assert selected["candidate_id"].tolist() == ["c2"]


def test_duplicate_evidence_stays_in_one_group_and_single_answer():
    shortlist = rag_service._build_balanced_shortlist([_frame("rules", count=3)])
    decision = EvidenceSelectionDecision(
        groups=[EvidenceGroupDecision(document_ids=["c1", "c2"], distinction="same proposition")]
    )

    groups = rag_service._normalize_evidence_groups(decision, set(shortlist["candidate_id"]))
    selected = rag_service._materialize_evidence_groups(shortlist, groups)

    assert selected["evidence_group"].nunique() == 1
    assert rag_service._multiple_evidence_response_instructions(1) is None


def test_distinct_evidence_groups_are_equal_sections_capped_at_five():
    shortlist = rag_service._build_balanced_shortlist([_frame("rules", count=6)], per_dataset=6)
    decision = EvidenceSelectionDecision(
        groups=[
            EvidenceGroupDecision(document_ids=[f"c{index}"], distinction=f"distinct {index}")
            for index in range(1, 7)
        ]
    )

    groups = rag_service._normalize_evidence_groups(decision, set(shortlist["candidate_id"]))
    selected = rag_service._materialize_evidence_groups(shortlist, groups)
    instructions = rag_service._multiple_evidence_response_instructions(len(groups))

    assert len(groups) == 5
    assert selected["evidence_group"].tolist() == [1, 2, 3, 4, 5]
    assert instructions is not None
    # 지시의 목적은 그룹을 섞지 않는 것이다 — 섹션 수와 동등 위상이 보장의 핵심이고,
    # 섹션 제목은 대상 이름으로 쓴다. '확인된 정보 N' 같은 내부 처리 용어가 사용자에게
    # 그대로 노출되면 안 된다.
    assert f"정확히 {len(groups)}개 섹션" in instructions
    assert "동등한 위상" in instructions
    assert "확인된 정보 1', '근거 그룹 2'처럼" in instructions  # 금지 지시로만 등장
    assert "## 확인된 정보 1" not in instructions


def test_selector_failure_uses_safe_single_group_fallback(monkeypatch):
    shortlist = rag_service._build_balanced_shortlist([_frame(dataset, count=1) for dataset in DATASETS])

    async def failed_selector(*_args, **_kwargs):
        return None

    monkeypatch.setattr(rag_service, "select_evidence_groups", failed_selector)
    selected, did_fallback = asyncio.run(
        rag_service._select_evidence_for_answer("evidence", shortlist, [])
    )

    assert did_fallback is True
    assert selected["evidence_group"].nunique() == 1
    assert selected["dataset"].tolist() == DATASETS
    fallback_context = rag_service._build_selected_evidence_context(selected)
    assert all(f"문서 {index}" in fallback_context for index in range(1, 7))


def test_empty_selector_decision_uses_safe_single_group_fallback(monkeypatch):
    shortlist = rag_service._build_balanced_shortlist([_frame("rules", count=3)])
    shortlist.loc[shortlist["candidate_id"] == "c1", "title"] = "재수강 신청 안내"

    async def empty_selector(*_args, **_kwargs):
        return EvidenceSelectionDecision(groups=[])

    monkeypatch.setattr(rag_service, "select_evidence_groups", empty_selector)
    selected, did_fallback = asyncio.run(
        rag_service._select_evidence_for_answer("재수강 조건이 어떻게 돼?", shortlist, [])
    )

    assert did_fallback is True
    assert selected["candidate_id"].tolist() == ["c1"]
    assert selected["selector_fallback"].tolist() == [1]


def test_empty_selector_decision_does_not_keep_lexically_unrelated_evidence(monkeypatch):
    shortlist = rag_service._build_balanced_shortlist([_frame("rules", count=3)])

    async def empty_selector(*_args, **_kwargs):
        return EvidenceSelectionDecision(groups=[])

    monkeypatch.setattr(rag_service, "select_evidence_groups", empty_selector)
    selected, did_fallback = asyncio.run(
        rag_service._select_evidence_for_answer("재수강을 하고 싶은데 어떻게 해야 해?", shortlist, [])
    )

    assert did_fallback is True
    assert selected.empty


def test_historical_notice_requires_period_bound_answer_instruction():
    selected = pd.DataFrame(
        [
            {
                "dataset": "notices",
                "citation_number": 1,
                "title": "2024학년도 여름계절학기 재수강 신청 안내",
                "chunk_text": "2024학년도 여름계절학기 재수강신청 확인서 제출",
            }
        ]
    )

    instruction = rag_service._period_bound_response_instruction(
        "재수강을 하고 싶은데 어떻게 해야 해?",
        selected,
    )

    assert instruction is not None
    assert "문서1(2024학년도 계절학기)" in instruction
    assert "현재 학기에도 같은 절차가 적용되는지는" in instruction


def test_openai_selector_timeout_is_observable_and_returns_fallback_signal(monkeypatch):
    class BrokenSelector:
        async def ainvoke(self, _messages):
            raise TimeoutError("timed out")

    monkeypatch.setattr(evidence_selector, "_structured_selector", lambda: BrokenSelector())
    usage: list[dict] = []
    decision = asyncio.run(
        evidence_selector.select_evidence_groups(
            "question",
            [{"candidate_id": "c1", "dataset": "rules", "text": "evidence"}],
            usage,
        )
    )

    assert decision is None
    assert usage[-1]["stage"] == "evidence_selection"
    assert usage[-1]["failed"] is True


def test_openai_selector_disables_sdk_retries(monkeypatch):
    captured: dict = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def with_structured_output(self, *_args, **_kwargs):
            return object()

    evidence_selector._structured_selector.cache_clear()
    monkeypatch.setattr(evidence_selector, "ChatOpenAI", FakeChatOpenAI)
    try:
        evidence_selector._structured_selector()
    finally:
        evidence_selector._structured_selector.cache_clear()

    assert captured["timeout"] == evidence_selector.RAG_EVIDENCE_TIMEOUT_SECONDS
    assert captured["max_retries"] == 0


def test_invalid_structured_response_returns_fallback_signal(monkeypatch):
    class InvalidSelector:
        async def ainvoke(self, _messages):
            return {"raw": None, "parsed": None, "parsing_error": ValueError("invalid JSON")}

    monkeypatch.setattr(evidence_selector, "_structured_selector", lambda: InvalidSelector())
    usage: list[dict] = []
    decision = asyncio.run(
        evidence_selector.select_evidence_groups(
            "question",
            [{"candidate_id": "c1", "dataset": "rules", "text": "evidence"}],
            usage,
        )
    )

    assert decision is None
    assert usage[-1]["stage"] == "evidence_selection"
    assert usage[-1]["failed"] is True


def test_system_prompt_preserves_multiple_evidence_groups():
    prompt = langchain_chat._get_system_prompt("rag")

    assert "[근거 그룹]이 둘 이상" in prompt
    assert "동일 그룹 안에서만" in prompt
    assert "각 그룹을 별도의 동등한 섹션으로 유지" in prompt


def test_source_metadata_excludes_evidence_selection_bookkeeping():
    internal_fields = {
        "candidate_id",
        "dataset_rank",
        "retrieval_fusion_score",
        "evidence_group",
        "citation_number",
        "selector_fallback",
    }
    row = pd.Series(
        {
            "title": "public title",
            "url": "https://example.edu/document",
            **{field: 1 for field in internal_fields},
        }
    )

    metadata = rag_service._source_metadata(row)

    assert metadata["title"] == "public title"
    assert metadata["url"] == "https://example.edu/document"
    assert internal_fields.isdisjoint(metadata)
    assert "metadata=_source_metadata(row)" in inspect.getsource(rag_service._source_chunk_from_row)
    assert "_source_chunk_from_row" in inspect.getsource(rag_service.ask)
    assert "_source_chunk_from_row" in inspect.getsource(rag_service.ask_stream)


def test_new_accuracy_path_contains_no_domain_specific_exception_terms():
    selector_source = Path(rag_service.__file__).parents[1] / "src" / "services" / "evidence_selector.py"
    source = selector_source.read_text(encoding="utf-8")

    assert "일반휴학" not in source
    assert "창업휴학" not in source
