from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import rag_service  # noqa: E402


def _analysis(*, needs_clarification: bool, reason: str | None = None):
    return rag_service.QueryAnalysisMeta(
        result=SimpleNamespace(
            needs_clarification=needs_clarification,
            clarification_reason=reason,
        ),
        used=True,
        failed=False,
    )


def test_first_turn_ellipsis_requests_course_identity_before_retrieval():
    fields = rag_service._first_turn_clarification_fields(
        "그 강의 신청해도 돼?",
        _analysis(needs_clarification=False),
        "",
    )

    assert fields == ["강의명", "학수번호"]
    answer = rag_service._build_clarification_answer(fields)
    assert "강의명" in answer
    assert "학수번호" in answer
    assert answer.endswith("?")


def test_analyser_ambiguity_is_not_sent_to_retrieval_on_first_turn():
    fields = rag_service._first_turn_clarification_fields(
        "내가 받을 수 있는 장학금 뭐 있어?",
        _analysis(needs_clarification=True),
        "",
    )

    assert fields == ["학년", "성적", "소득 구간"]
    assert rag_service._build_clarification_answer(fields).endswith("소득 구간을 알려주실 수 있나요?")


def test_history_allows_a_resolved_followup_to_continue_to_retrieval():
    fields = rag_service._first_turn_clarification_fields(
        "그 강의 신청해도 돼?",
        _analysis(needs_clarification=True, reason="강의명이 필요합니다."),
        "직전 대화에서 데이터구조 강의와 학수번호를 확인했습니다.",
    )

    assert fields == []


@pytest.mark.parametrize(
    "question",
    [
        "F 받은 과목을 재수강하면 성적 상한이 어떻게 돼?",
        "등록금을 카드로 낼 수 있는지와 가능한 카드사를 확인해 줘",
        "전과 신청 자격과 선발 절차를 알려줘",
        "졸업하려면 영어 성적을 꼭 제출해야 해?",
        "성적확정일과 성적장학금 발표일을 함께 비교해 줘",
    ],
)
def test_generic_analyser_ambiguity_does_not_block_a_specified_question(question):
    fields = rag_service._first_turn_clarification_fields(
        question,
        _analysis(
            needs_clarification=True,
            reason="확인하려는 대상이나 조건",
        ),
        "",
    )

    assert fields == []
