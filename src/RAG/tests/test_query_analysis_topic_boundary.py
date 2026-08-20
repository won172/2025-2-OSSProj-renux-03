from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services import query_analysis  # noqa: E402
from src.services.query_analysis import (  # noqa: E402
    QueryAnalysisResult,
    SubQuery,
    enforce_original_query_boundary,
)


def test_unrelated_normalized_question_is_rejected_with_its_route_metadata():
    stolen = QueryAnalysisResult(
        normalized_question="현재 모집 중인 공모전",
        intent="notices",
        entities={"topic": ["공모전"]},
        time_focus="recent",
        search_queries=["현재 진행 중인 공모전"],
        is_compound=True,
        sub_queries=[
            SubQuery(query="공모전 신청 기간", dataset="notices")
        ],
    )

    safe = enforce_original_query_boundary(stolen, query="샤갈")

    assert safe.normalized_question == "샤갈"
    assert safe.intent == "unknown"
    assert safe.entities == {}
    assert safe.time_focus == "none"
    assert safe.search_queries == []
    assert safe.sub_queries == []
    assert safe.is_compound is False


def test_relevant_rewrites_are_additive_and_keep_exact_original_text():
    analyzed = QueryAnalysisResult(
        normalized_question="수강신청 일정",
        intent="schedule",
        search_queries=["수강신청 날짜"],
        is_compound=True,
        sub_queries=[
            SubQuery(query="수강신청 기간", dataset="schedule")
        ],
    )

    safe = enforce_original_query_boundary(
        analyzed,
        query="수강신청 기간은?",
    )

    assert safe.normalized_question.startswith("수강신청 기간은?")
    assert all(
        candidate.startswith("수강신청 기간은?")
        for candidate in safe.search_queries
    )
    assert safe.sub_queries[0].query.startswith("수강신청 기간은?")
    assert safe.intent == "schedule"


@pytest.mark.asyncio
async def test_analysis_hides_history_when_previous_topic_does_not_overlap(monkeypatch):
    captured: dict[str, str] = {}

    class FakeChain:
        async def ainvoke(self, payload):
            captured.update(payload)
            return QueryAnalysisResult(
                normalized_question="현재 모집 중인 공모전",
                intent="notices",
                search_queries=["현재 진행 중인 공모전"],
            )

    monkeypatch.setattr(query_analysis, "analysis_chain", FakeChain())
    result = await query_analysis.analyze_query(
        "샤갈",
        "사용자: 현재 모집 중인 공모전 알려줘\n동똑이: 코오롱 챌린지입니다.",
    )

    assert captured["history"] == "(없음)"
    assert result is not None
    assert result.normalized_question == "샤갈"
    assert result.intent == "unknown"
    assert result.search_queries == []
