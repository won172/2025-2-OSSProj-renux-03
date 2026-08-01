"""최신 공지는 의미 유사도가 아니라 게시판별 게시일 역순으로 조회한다."""
from __future__ import annotations

import asyncio
from datetime import date
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.rag_service import (  # noqa: E402
    _extract_notice_board_filter,
    _is_recent_notice_query,
    _latest_notice_hits,
    _resolve_notice_retrieval_controls,
    _retrieve_frames,
    _select_answer_evidence,
)
import api.rag_service as rag_service  # noqa: E402
from src.utils.date_parser import QueryDateFilter  # noqa: E402


def _notice_chunks() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "chunk_id": "scholarship-new-0",
                "doc_id": "scholarship-new",
                "notice_id": 30,
                "position": 0,
                "title": "신규 장학 공지",
                "chunk_text": "신규 장학 공지 본문 첫 청크",
                "topics": "장학공지",
                "published_at": "2026-06-20",
                "url": "https://example.com/30",
                "source": "notices",
            },
            {
                "chunk_id": "scholarship-new-1",
                "doc_id": "scholarship-new",
                "notice_id": 30,
                "position": 1,
                "title": "신규 장학 공지",
                "chunk_text": "신규 장학 공지 본문 두 번째 청크",
                "topics": "장학공지",
                "published_at": "2026-06-20",
                "url": "https://example.com/30",
                "source": "notices",
            },
            {
                "chunk_id": "academic-new",
                "doc_id": "academic-new",
                "notice_id": 20,
                "position": 0,
                "title": "더 최신인 학사 공지",
                "chunk_text": "학사 공지 본문",
                "topics": "학사공지",
                "published_at": "2026-06-22",
                "url": "https://example.com/20",
                "source": "notices",
            },
            {
                "chunk_id": "scholarship-old",
                "doc_id": "scholarship-old",
                "notice_id": 10,
                "position": 0,
                "title": "과거 장학 공지",
                "chunk_text": "과거 장학 공지 본문",
                "topics": "장학공지",
                "published_at": "2026-05-01",
                "url": "https://example.com/10",
                "source": "notices",
            },
        ]
    )


def test_latest_notice_hits_filters_board_sorts_by_date_and_deduplicates_chunks():
    hits = _latest_notice_hits(
        chunks_df=_notice_chunks(),
        top_k=5,
        where_filter={"topics": {"$eq": "장학공지"}},
    )

    assert hits["chunk_id"].tolist() == ["scholarship-new-0", "scholarship-old"]
    assert hits["published_at"].tolist() == ["2026-06-20", "2026-05-01"]
    assert hits["hybrid_score"].tolist() == [1.0, 1.0]


def test_latest_notice_hits_can_require_an_operational_title_term():
    chunks = pd.DataFrame(
        [
            {
                "chunk_id": "old-adjustment", "doc_id": "old-adjustment", "position": 0,
                "title": "2025학년도 수강정정 안내", "chunk_text": "수강정정 안내",
                "published_at": "2025-11-01", "url": "https://example.com/old",
            },
            {
                "chunk_id": "current-adjustment", "doc_id": "current-adjustment", "position": 0,
                "title": "2026학년도 여름계절학기 수강정정 안내", "chunk_text": "수강정정 안내",
                "published_at": "2026-05-19", "url": "https://example.com/current",
            },
            {
                "chunk_id": "unrelated-new", "doc_id": "unrelated-new", "position": 0,
                "title": "2026학년도 장학 신청 안내", "chunk_text": "장학 안내",
                "published_at": "2026-07-20", "url": "https://example.com/unrelated",
            },
        ]
    )

    hits = _latest_notice_hits(chunks_df=chunks, top_k=5, title_terms=["수강정정"])

    assert hits["chunk_id"].tolist() == ["current-adjustment", "old-adjustment"]


def test_operational_title_match_prefers_the_matching_body_chunk_over_title_chunk():
    chunks = pd.DataFrame(
        [
            {
                "chunk_id": "title", "doc_id": "notice-1", "position": 0,
                "title": "2026학년도 수강정정 안내",
                "chunk_text": "[2026학년도 수강정정 안내]\n\n게시일과 안내 제목",
                "published_at": "2026-05-19", "url": "https://example.com/current",
            },
            {
                "chunk_id": "details", "doc_id": "notice-1", "position": 2,
                "title": "2026학년도 수강정정 안내",
                "chunk_text": "[2026학년도 수강정정 안내]\n\n수강정정 기간은 5월 21일 10시부터입니다.",
                "published_at": "2026-05-19", "url": "https://example.com/current",
            },
        ]
    )

    hits = _latest_notice_hits(chunks_df=chunks, top_k=5, title_terms=["수강정정"])

    assert hits["chunk_id"].tolist() == ["details"]


def test_recent_date_label_means_latest_available_not_fixed_recent_days():
    recent_filter = QueryDateFilter(
        start=date(2026, 6, 17),
        end=date(2026, 6, 23),
        label="recent",
        is_relative=True,
    )

    hits = _latest_notice_hits(
        chunks_df=_notice_chunks(),
        top_k=5,
        where_filter={"topics": {"$eq": "장학공지"}},
        date_filter=recent_filter,
    )

    assert hits["chunk_id"].tolist() == ["scholarship-new-0", "scholarship-old"]


def test_recent_date_label_never_leaks_notices_after_as_of():
    chunks = pd.concat(
        [
            _notice_chunks(),
            pd.DataFrame(
                [
                    {
                        "chunk_id": "future-scholarship",
                        "doc_id": "future-scholarship",
                        "notice_id": 99,
                        "position": 0,
                        "title": "기준일 다음 날 장학 공지",
                        "chunk_text": "미래 장학 공지 본문",
                        "topics": "장학공지",
                        "published_at": "2026-06-24",
                        "url": "https://example.com/future",
                        "source": "notices",
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    recent_filter = QueryDateFilter(
        start=date(2026, 6, 17),
        end=date(2026, 6, 23),
        label="recent",
        is_relative=True,
    )

    hits = _latest_notice_hits(
        chunks_df=chunks,
        top_k=5,
        where_filter={"topics": {"$eq": "장학공지"}},
        date_filter=recent_filter,
    )

    assert "future-scholarship" not in hits["chunk_id"].tolist()
    assert hits["chunk_id"].tolist() == ["scholarship-new-0", "scholarship-old"]


def test_explicit_date_filter_is_applied_before_latest_sort():
    may_filter = QueryDateFilter(
        start=date(2026, 5, 1),
        end=date(2026, 5, 31),
        label="specific_month",
    )

    hits = _latest_notice_hits(
        chunks_df=_notice_chunks(),
        top_k=5,
        where_filter={"topics": {"$eq": "장학공지"}},
        date_filter=may_filter,
    )

    assert hits["chunk_id"].tolist() == ["scholarship-old"]


def test_recent_notice_intent_and_board_alias_are_detected():
    route = ["notices"]

    assert _is_recent_notice_query("최근 장학 공지 알려줘", route) is True
    assert _extract_notice_board_filter("최근 장학 공지 알려줘", route) == "장학공지"
    assert _extract_notice_board_filter("오늘 올라온 장학금 뭐있지", route) == "장학공지"
    assert _extract_notice_board_filter("최신 학사공지 보여줘", route) == "학사공지"


def test_latest_notice_controls_are_enabled_for_full_corpus_route():
    recent, board, policy = _resolve_notice_retrieval_controls(
        raw_query="최근 장학 공지 보여줘",
        # 질의 분석이 시간 표현을 생략해도 원문을 우선 보존해야 한다.
        semantic_query="장학 관련 공지 보여줘",
        route=["notices", "rules", "schedule", "courses", "staff", "meals"],
    )

    assert recent is True
    assert board == "장학공지"
    assert policy.name == "recent_notices"
    assert policy.allow_recency_override is True


def test_latest_notice_selection_keeps_newest_rows_without_llm(monkeypatch):
    shortlist = _notice_chunks().copy()
    shortlist["dataset"] = "notices"
    shortlist["candidate_id"] = [f"c{index}" for index in range(1, len(shortlist) + 1)]
    shortlist = pd.concat(
        [
            shortlist,
            pd.DataFrame(
                [
                    {
                        "chunk_id": "rule-1",
                        "candidate_id": "c5",
                        "dataset": "rules",
                        "title": "무관한 규정",
                        "published_at": "2026-06-30",
                    }
                ]
            ),
        ],
        ignore_index=True,
    )

    async def fail_if_selector_runs(*args, **kwargs):
        raise AssertionError("최신 공지 목록에 LLM 근거 선택기가 실행되면 안 됩니다")

    monkeypatch.setattr(rag_service, "_select_evidence_for_answer", fail_if_selector_runs)
    selected, selector_fallback = asyncio.run(
        _select_answer_evidence(
            "최근 장학 공지 알려줘",
            shortlist,
            [],
            recent_notice_query=True,
        )
    )

    assert selector_fallback is False
    assert selected["title"].tolist() == ["더 최신인 학사 공지", "신규 장학 공지", "과거 장학 공지"]
    assert selected["dataset"].tolist() == ["notices", "notices", "notices"]


def test_recent_notice_retrieval_bypasses_hybrid_search(monkeypatch):
    monkeypatch.setattr(
        rag_service,
        "_ensure_dataset",
        lambda dataset: (_notice_chunks(), object(), object(), None),
    )

    def fail_if_hybrid_search_runs(*args, **kwargs):
        raise AssertionError("최신 공지 조회에서 하이브리드 검색이 실행되면 안 됩니다")

    monkeypatch.setattr(rag_service, "hybrid_search_with_meta", fail_if_hybrid_search_runs)

    frames, eliminated, unavailable = asyncio.run(
        _retrieve_frames(
            route=["notices"],
            query="최근 장학 공지 알려줘",
            final_where_filter={},
            notice_board_filter="장학공지",
            date_filter=QueryDateFilter(
                start=date(2026, 6, 17),
                end=date(2026, 6, 23),
                label="recent",
                is_relative=True,
            ),
            entry_year=None,
            request_id="test-latest-notices",
            recent_notice_query=True,
        )
    )

    assert eliminated is False
    assert unavailable == []
    assert len(frames) == 1
    assert frames[0]["chunk_id"].tolist() == ["scholarship-new-0", "scholarship-old"]
