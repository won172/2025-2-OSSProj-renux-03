from __future__ import annotations

from datetime import date
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import rag_service  # noqa: E402
from src.search import hybrid  # noqa: E402
from src.services.answer import extract_title  # noqa: E402
from src.services.langchain_chat import _get_system_prompt  # noqa: E402
from src.services.query_analysis import QueryAnalysisResult  # noqa: E402


@pytest.mark.parametrize(
    "question",
    [
        "현재 진행 중인 공모전 알려줘",
        "모집 중인 장학금 있어?",
        "지금 신청 가능한 교내 프로그램 알려줘",
        "접수 가능한 채용 공고만 보여줘",
        "열려 있는 동아리 모집 있어?",
        "지금 모집 중인 공모전 뭐있어?",
        "지금 진행 중인 공모전 알려줘",
        "지금 진행중인 공모전 뭐야",
        "최근 진행하는 공모전 뭐야?",
        "요즘 신청 가능한 장학금 있어?",
    ],
)
def test_active_notice_state_signals_are_detected(question: str):
    assert rag_service._is_active_notice_state_query(question, ["notices"])


@pytest.mark.parametrize(
    "question,route",
    [
        ("최근 장학 공지 보여줘", ["notices"]),
        ("오늘 올라온 공모전 공지", ["notices"]),
        ("지금 학식 뭐야", ["meals"]),
        ("2024년 공모전 결과 알려줘", ["notices"]),
    ],
)
def test_non_state_queries_do_not_activate_deadline_filter(
    question: str,
    route: list[str],
):
    assert not rag_service._is_active_notice_state_query(question, route)


def test_active_notice_filter_hard_excludes_expired_and_stale_unknown_rows():
    notices = pd.DataFrame(
        [
            {
                "dataset": "notices",
                "chunk_id": "expired-known",
                "title": "지난 공모전",
                "published_at": "2025-10-01",
                "apply_deadline": "2025-11-30",
                "chunk_text": "지난 공모전",
            },
            {
                "dataset": "notices",
                "chunk_id": "open-title-parsed",
                "title": "L-HUSS 여행코스 공모전 (2026.08.06까지)",
                "published_at": "2026-07-20",
                "apply_deadline": "",
                "chunk_text": "여행코스 공모전",
            },
            {
                "dataset": "notices",
                "chunk_id": "today",
                "title": "제2회 미당 학술상 공모",
                "published_at": "2026-07-01",
                "apply_deadline": "2026-07-31",
                "chunk_text": "미당 학술상",
            },
            {
                "dataset": "notices",
                "chunk_id": "unknown-recent",
                "title": "최근 아이디어 공모전",
                "published_at": "2026-07-20",
                "apply_deadline": "",
                "chunk_text": "세부 마감일은 원문 확인",
            },
            {
                "dataset": "notices",
                "chunk_id": "unknown-stale",
                "title": "오래된 모집 공고",
                "published_at": "2026-03-01",
                "apply_deadline": "",
                "chunk_text": "마감일 미상",
            },
            {
                "dataset": "notices",
                "chunk_id": "future-publication",
                "title": "아직 게시되지 않은 공고",
                "published_at": "2026-08-01",
                "apply_deadline": "2026-08-10",
                "chunk_text": "미래 공고",
            },
        ]
    )

    filtered, stats = rag_service._filter_active_notice_frames(
        [notices],
        date(2026, 7, 31),
        unknown_max_age_days=90,
    )

    kept = filtered[0].set_index("chunk_id")
    assert set(kept.index) == {
        "open-title-parsed",
        "today",
        "unknown-recent",
    }
    assert kept.loc["open-title-parsed", "apply_deadline"] == "2026-08-06"
    assert kept.loc["today", "deadline_status"] == "open"
    assert kept.loc["unknown-recent", "deadline_status"] == "unknown"
    assert set(kept["deadline_as_of"]) == {"2026-07-31"}
    assert stats.expired_deadline == 1
    assert stats.stale_unknown_deadline == 1
    assert stats.future_publication == 1
    assert stats.kept_known_deadline == 2
    assert stats.kept_unknown_deadline == 1


def test_active_notice_retrieval_filters_before_top_k_truncation():
    notices = pd.DataFrame(
        [
            {
                "chunk_id": "expired",
                "doc_id": "expired",
                "title": "2024 DB 이노베이션챌린지 공모전",
                "published_at": "2024-08-01",
                "apply_deadline": "2024-09-20",
                "chunk_text": "[2024 DB 이노베이션챌린지 공모전]\n\n종료",
            },
            {
                "chunk_id": "today",
                "doc_id": "today",
                "title": "제2회 미당 학술상 공모 (4/1~7/31)",
                "published_at": "2026-04-01",
                "apply_deadline": "2026-07-31",
                "chunk_text": "[제2회 미당 학술상 공모 (4/1~7/31)]\n\n접수",
            },
            {
                "chunk_id": "future-title-parsed",
                "doc_id": "future-title-parsed",
                "title": "[L-HUSS] 여행코스 공모전 (2026.08.06까지)",
                "published_at": "2026-07-20",
                "apply_deadline": "",
                "chunk_text": "[[L-HUSS] 여행코스 공모전 (2026.08.06까지)]\n\n접수",
            },
            {
                "chunk_id": "recent-unknown",
                "doc_id": "recent-unknown",
                "title": "학생 아이디어 공모전",
                "published_at": "2026-07-25",
                "apply_deadline": "",
                "chunk_text": "[학생 아이디어 공모전]\n\n원문 확인",
            },
            {
                "chunk_id": "stale-unknown",
                "doc_id": "stale-unknown",
                "title": "예전 아이디어 공모전",
                "published_at": "2025-11-01",
                "apply_deadline": "",
                "chunk_text": "[예전 아이디어 공모전]\n\n원문 확인",
            },
        ]
    )

    hits = rag_service._active_notice_hits(
        chunks_df=notices,
        query="현재 진행 중인 공모전 알려줘",
        as_of=date(2026, 7, 31),
        top_k=10,
    )

    assert hits["chunk_id"].tolist() == [
        "today",
        "future-title-parsed",
        "recent-unknown",
    ]
    assert hits.set_index("chunk_id").loc[
        "future-title-parsed",
        "apply_deadline",
    ] == "2026-08-06"


def test_deadline_range_ranking_supports_bm25_artifacts(monkeypatch, tmp_path):
    chunks = pd.DataFrame([
        {
            "chunk_id": "career",
            "title": "교내 추천채용 공고",
            "chunk_text": "[교내 추천채용 공고]\n\n이번 주 지원 마감",
            "apply_deadline": "2026-08-02",
            "published_at": "2026-07-28",
            "source": "notices",
        },
        {
            "chunk_id": "international",
            "title": "해외파견 프로그램 모집",
            "chunk_text": "[해외파견 프로그램 모집]\n\n이번 달 지원 마감",
            "apply_deadline": "2026-08-20",
            "published_at": "2026-07-27",
            "source": "notices",
        },
        {
            "chunk_id": "scholarship",
            "title": "교내 장학금 신청",
            "chunk_text": "[교내 장학금 신청]\n\n장학생 지원 마감",
            "apply_deadline": "2026-08-25",
            "published_at": "2026-07-26",
            "source": "notices",
        },
    ])
    monkeypatch.setattr(hybrid, "VECTORIZER_DIR", tmp_path)
    monkeypatch.setattr(hybrid, "TFIDF_TOKENIZER", "default")
    index, matrix = hybrid.train_bm25(
        "notices",
        chunks["chunk_text"].tolist(),
        chunk_ids=chunks["chunk_id"].tolist(),
    )

    hits = rag_service._deadline_filter_rank_notices(
        chunks_df=chunks,
        vectorizer=index,
        matrix=matrix,
        tfidf_chunk_ids=chunks["chunk_id"].tolist(),
        query="이번 주 마감하는 교내 추천채용 공고",
        date_filter=rag_service.QueryDateFilter(
            start=date(2026, 8, 1),
            end=date(2026, 8, 31),
            label="this_month",
            kind="deadline",
        ),
        top_k=10,
    )

    assert hits.iloc[0]["chunk_id"] == "career"
    assert hits.iloc[0]["sparse_score"] > hits.iloc[1]["sparse_score"]


@pytest.mark.parametrize(
    "question",
    [
        "이번 주 마감하는 교내 추천채용 공고를 알려줘",
        "이번 달 마감인 해외파견 프로그램만 찾아줘",
        "8월에 마감하는 공모전 알려줘",
    ],
)
def test_opportunity_deadline_range_uses_notice_deadline_index_only(question):
    analysis = rag_service.QueryAnalysisMeta(
        result=QueryAnalysisResult(
            normalized_question=question,
            intent="notices",
        ),
        used=True,
    )

    assert rag_service._resolve_retrieval_route(question, analysis) == ["notices"]


def test_academic_registration_deadline_keeps_schedule_route():
    question = "이번 주 수강신청 마감이 언제야?"
    analysis = rag_service.QueryAnalysisMeta(
        result=QueryAnalysisResult(
            normalized_question=question,
            intent="schedule",
        ),
        used=True,
    )

    route = rag_service._resolve_retrieval_route(question, analysis)
    assert route[0] == "schedule"


def test_citation_title_keeps_nested_brackets():
    assert extract_title(
        "[공지 [L-HUSS] 2026 여행코스 공모전]\n\n본문"
    ) == "공지 [L-HUSS] 2026 여행코스 공모전"


def test_active_notice_generation_context_exposes_deadline_status():
    known = pd.Series(
        {
            "dataset": "notices",
            "source": "notices",
            "title": "여행코스 공모전",
            "published_at": "2026-07-20",
            "apply_deadline": "2026-08-06",
            "deadline_status": "open",
            "deadline_as_of": "2026-07-31",
            "chunk_text": "접수 안내",
        }
    )
    unknown = known.copy()
    unknown["title"] = "마감일 미상 공모전"
    unknown["apply_deadline"] = ""
    unknown["deadline_status"] = "unknown"

    known_context = rag_service._build_document_context_part(known, 1)
    unknown_context = rag_service._build_document_context_part(unknown, 2)

    assert "신청 마감일: 2026-08-06" in known_context
    assert "접수 상태: 마감 전 또는 마감 당일 (2026-07-31 기준)" in known_context
    assert "접수 상태: 마감일 확인 필요" in unknown_context


def test_r14_prompt_and_dynamic_instruction_forbid_expired_as_open():
    system_prompt = _get_system_prompt("rag").format(
        current_date="2026년 7월 31일"
    )
    selected = pd.DataFrame(
        [
            {
                "dataset": "notices",
                "citation_number": 1,
                "apply_deadline": "2025-11-30",
            },
            {
                "dataset": "notices",
                "citation_number": 2,
                "apply_deadline": "",
            },
        ]
    )
    instruction = rag_service._active_notice_response_instruction(
        "현재 진행 중인 공모전 알려줘",
        selected,
        date(2026, 7, 31),
    )

    assert "현재 날짜보다 이전" in system_prompt
    assert "현재 접수 중인 것은 확인되지 않습니다" in system_prompt
    assert instruction is not None
    assert "문서1=2025-11-30" in instruction
    assert "마감일 미상: 문서2" in instruction
    assert "진행 중·모집 중·신청 가능·접수 가능이라고 표현하지 마세요" in instruction


def test_active_notice_evidence_prefers_verified_deadlines_and_deduplicates():
    shortlist = pd.DataFrame(
        [
            {
                "dataset": "notices",
                "candidate_id": "c1",
                "doc_id": "unknown",
                "published_at": "2026-07-30",
                "apply_deadline": "",
                "final_score": 1.0,
            },
            {
                "dataset": "notices",
                "candidate_id": "c2",
                "doc_id": "known-today",
                "published_at": "2026-02-10",
                "apply_deadline": "2026-07-31",
                "final_score": 0.7,
            },
            {
                "dataset": "notices",
                "candidate_id": "c3",
                "doc_id": "known-future",
                "published_at": "2026-07-21",
                "apply_deadline": "2026-08-06",
                "final_score": 0.8,
            },
            {
                "dataset": "notices",
                "candidate_id": "c4",
                "doc_id": "known-future",
                "published_at": "2026-07-21",
                "apply_deadline": "2026-08-06",
                "final_score": 0.6,
            },
        ]
    )

    selected = rag_service._select_active_notice_evidence(shortlist)

    assert selected["candidate_id"].tolist() == ["c2", "c3", "c1"]
    assert selected["citation_number"].tolist() == [1, 2, 3]


def test_active_notice_answer_contract_lists_open_and_labels_unknown():
    selected = pd.DataFrame(
        [
            {
                "dataset": "notices",
                "citation_number": 1,
                "doc_id": "expired",
                "title": "지난 공모전",
                "apply_deadline": "2025-11-30",
                "chunk_text": "[지난 공모전]\n\n종료",
            },
            {
                "dataset": "notices",
                "citation_number": 2,
                "doc_id": "today",
                "title": "미당 학술상",
                "apply_deadline": "2026-07-31",
                "chunk_text": "[제2회 미당 학술상 공모(4/1~7/31)]\n\n접수",
            },
            {
                "dataset": "notices",
                "citation_number": 3,
                "doc_id": "unknown",
                "title": "최근 공모전",
                "apply_deadline": "",
                "chunk_text": "[최근 공모전]\n\n마감일 미상",
            },
        ]
    )

    answer = rag_service._enforce_active_notice_answer_contract(
        "현재 진행 중인 공모전 알려줘",
        "지난 공모전도 진행 중입니다.",
        selected,
        date(2026, 7, 31),
    )

    assert "지난 공모전" not in answer
    assert "제2회 미당 학술상 공모" in answer
    assert "2026-07-31" in answer
    assert "최근 공모전 — 마감일 확인 필요" in answer


def test_clear_active_notice_question_bypasses_llm_clarification():
    analysis = rag_service.QueryAnalysisMeta(
        result=SimpleNamespace(
            needs_clarification=True,
            clarification_reason="확인하려는 대상이나 조건",
        ),
        used=True,
        failed=False,
    )

    assert rag_service._first_turn_clarification_fields(
        "지금 신청 가능한 공모전 뭐 있어?",
        analysis,
        "",
    ) == []


def test_active_deadline_fallback_is_specific_and_not_cached():
    answer = rag_service._build_retrieval_fallback_answer(
        route=["notices"],
        reason=rag_service.FALLBACK_REASON_ACTIVE_DEADLINE_ELIMINATED_ALL,
        query="현재 진행 중인 공모전 알려줘",
    )

    assert "현재 접수 중인 것은" in answer
    assert "마감일이 확인된 공고 중" in answer
    assert "확인되지 않습니다" in answer
    assert not rag_service._should_cache_answer(
        ["notices"],
        False,
        False,
        True,
        "임시 답변",
        active_notice_query=True,
    )
