from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.rag_service import (  # noqa: E402
    RetrievalPolicy,
    _prepare_merged_results,
    _rrf_relevance_floor,
)


def test_prepare_merged_results_keeps_courses_neutral_for_recency():
    merged = pd.DataFrame(
        [
            {
                "chunk_id": "notice-1",
                "chunk_text": "오래된 공지",
                "dataset": "notices",
                "hybrid_score": 0.5,
                "published_at": "2020-01-01",
            },
            {
                "chunk_id": "course-1",
                "chunk_text": "전공필수 교과목",
                "dataset": "courses",
                "hybrid_score": 0.5,
                "published_at": "",
            },
        ]
    )

    result = _prepare_merged_results(
        merged,
        recent_notice_query=False,
        policy=RetrievalPolicy(name="courses", min_score=0.12),
        query="전공필수 교과목 알려줘",
    )

    course_score = result.loc[result["chunk_id"] == "course-1", "final_score"].iloc[0]
    notice_score = result.loc[result["chunk_id"] == "notice-1", "final_score"].iloc[0]

    assert course_score > notice_score
    assert result.iloc[0]["chunk_id"] == "course-1"


def test_prepare_merged_results_single_candidate_uses_raw_hybrid_score():
    merged = pd.DataFrame(
        [
            {
                "chunk_id": "staff-1",
                "chunk_text": "학과 사무실 연락처",
                "dataset": "staff",
                "hybrid_score": 0.2,
            }
        ]
    )

    result = _prepare_merged_results(
        merged,
        recent_notice_query=False,
        policy=RetrievalPolicy(name="staff_lookup", min_score=0.12),
        query="학과 사무실 연락처",
    )

    assert result.iloc[0]["norm_hybrid"] == 0.2
    assert result.iloc[0]["final_score"] < 1.0


def test_rrf_floor_rejects_unaligned_rank_score_without_absolute_relevance():
    passed, aligned, threshold = _rrf_relevance_floor(
        "샤갈",
        pd.DataFrame(
            [{
                "title": "2026학년도 교환학생 모집 안내",
                "chunk_text": "교환학생 지원 기간과 제출 서류",
                "hybrid_score": 0.5,
                "vector_score": 0.349,
                "sparse_score": 0.0,
            }]
        ),
        RetrievalPolicy(name="default", min_score=0.12),
    )

    assert passed is False
    assert aligned is False
    assert threshold == 0.40


def test_rrf_floor_preserves_lexically_aligned_short_school_queries():
    passed, aligned, threshold = _rrf_relevance_floor(
        "총학생회비 사용 내역",
        pd.DataFrame(
            [{
                "title": "총학생회비 사용 내역 공개",
                "chunk_text": "회계 내역을 공개합니다.",
                "hybrid_score": 0.32,
                "vector_score": 0.0,
                "sparse_score": 0.21,
            }]
        ),
        RetrievalPolicy(name="default", min_score=0.12),
    )

    assert passed is True
    assert aligned is True
    assert threshold == 0.12
