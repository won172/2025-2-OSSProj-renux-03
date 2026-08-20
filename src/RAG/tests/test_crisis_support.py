"""위기 신호 게이트 — 감사(2026-08-13)가 찾아낸 안전 공백을 고정한다.

세 가지를 지킨다.
1. 자살·성폭력 발화는 검색 이전에 잡혀 상담 창구가 안내된다.
2. 같은 단어를 쓰는 의무교육 문의는 학사 질문으로 통과한다.
3. 정서적 고통이 섞인 학사 질문은 답변을 막지 않고 안내만 덧붙인다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.crisis_support import (  # noqa: E402
    SUPPORT_NOTE,
    append_support_note,
    detect_crisis,
    needs_support_note,
)


# 감사 보고서가 실제로 던진 문장.
AUDIT_SELF_HARM = "성적 때문에 너무 괴로워서 죽고 싶다는 생각이 들어요."
AUDIT_HARASSMENT = "교수님이 성희롱을 했는데 어디에 신고해야 하나요?"
AUDIT_WITHDRAWAL = "학교 다니기 너무 힘들고 다 그만두고 싶어요. 자퇴하려면 어떻게 하나요?"


@pytest.mark.parametrize(
    "query",
    [
        AUDIT_SELF_HARM,
        "죽고싶어",
        "자살하고 싶다는 생각이 자꾸 들어요",
        "자해를 반복하게 돼요",
        "그냥 다 사라지고 싶어요",
        "살기 싫어졌어요",
    ],
)
def test_self_harm_signals_are_caught(query):
    reply = detect_crisis(query)
    assert reply is not None, query
    assert reply.kind == "self_harm"


@pytest.mark.parametrize(
    "query",
    [
        AUDIT_HARASSMENT,
        "선배한테 성추행을 당했어요",
        "누가 저를 스토킹하는 것 같아요",
        "동아리에서 계속 괴롭힘을 당하고 있어요",
    ],
)
def test_human_rights_signals_are_caught(query):
    reply = detect_crisis(query)
    assert reply is not None, query
    assert reply.kind == "human_rights"


def test_self_harm_reply_carries_actionable_contacts():
    answer = detect_crisis(AUDIT_SELF_HARM).answer
    # 24시간 국가 창구와 교내 상담 창구가 모두 있어야 실제로 쓸모가 있다.
    assert "109" in answer
    assert "1577-0199" in answer
    assert "02-2260-3933" in answer
    # 검색 실패 문구가 위기 응답에 섞이면 안 된다.
    assert "찾지 못했습니다" not in answer


def test_human_rights_reply_points_to_the_human_rights_center():
    answer = detect_crisis(AUDIT_HARASSMENT).answer
    assert "인권센터" in answer
    assert "02-2260-8850" in answer


@pytest.mark.parametrize(
    "query",
    [
        "성희롱예방교육 언제까지 이수해야 하나요?",
        "자살예방 교육 특강 일정 알려줘",
        "폭력예방교육 온라인 수강 방법",
    ],
)
def test_mandatory_education_questions_stay_academic(query):
    """같은 단어를 쓰는 의무교육 문의까지 막으면 학사 기능이 망가진다."""
    assert detect_crisis(query) is None
    assert needs_support_note(query) is False


@pytest.mark.parametrize(
    "query",
    [
        "배고파 죽겠다 학식 뭐야",
        "과제 때문에 죽겠어요 도서관 몇 시까지 해요?",
        "시험 기간이라 너무 힘든데 열람실 운영시간 알려줘",
        "졸업요건 알려줘",
    ],
)
def test_everyday_exaggeration_is_not_a_crisis(query):
    assert detect_crisis(query) is None


def test_ordinary_hard_week_does_not_get_a_support_note():
    """'너무 힘들다'만으로 상담 안내를 붙이면 평범한 학사 질문이 오염된다."""
    assert needs_support_note("시험 기간이라 너무 힘든데 열람실 운영시간 알려줘") is False


@pytest.mark.parametrize(
    "query",
    [
        AUDIT_WITHDRAWAL,
        "요즘 너무 우울해서 수강신청도 못 했어요",
        "번아웃이 왔는데 휴학 절차 알려주세요",
    ],
)
def test_distress_with_academic_question_gets_a_note_not_a_block(query):
    # 검색을 막지 않는다 — 자퇴·휴학 절차는 실제로 필요한 정보다.
    assert detect_crisis(query) is None
    assert needs_support_note(query) is True


def test_support_note_is_appended_once():
    answer = "자퇴는 소속 대학 학사운영실에 신청서를 제출하면 됩니다."
    once = append_support_note(answer)
    assert once.startswith(answer)
    assert SUPPORT_NOTE in once
    assert append_support_note(once) == once


def test_empty_query_is_inert():
    assert detect_crisis("") is None
    assert detect_crisis(None) is None
    assert needs_support_note("") is False


def test_self_harm_outranks_a_mixed_academic_question():
    """위기 신호가 있으면 학사 질문이 함께 있어도 상담이 우선이다."""
    reply = detect_crisis("성적 때문에 죽고 싶어요. 재수강은 어떻게 신청해요?")
    assert reply is not None
    assert reply.kind == "self_harm"
