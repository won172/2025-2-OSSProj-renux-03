"""스몰톡 분기·후속 발화 재구성 테스트.

로그에 실제로 들어온 발화를 그대로 입력으로 쓴다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.conversation import (  # noqa: E402
    detect_smalltalk,
    extract_last_user_question,
    has_lexical_overlap,
    history_allows_context_rewrite,
    needs_context_rewrite,
    preserve_original_query,
    rewrite_with_context,
)


# ---------------------------------------------------------------- 스몰톡

@pytest.mark.parametrize("query,kind", [
    ("안녕", "greeting"),          # 34회 — 최다 발화
    ("안녕하세요", "greeting"),
    ("하이", "greeting"),
    ("고마워", "thanks"),          # 7회
    ("감사합니다", "thanks"),
    ("너는 누구야", "identity"),     # 5회
    ("너는 이름이 뭐니", "identity"),  # 9회
    ("너 이름이 뭐야?", "identity"),
    ("언제 만들어졌어", "origin"),
    ("오늘 기분 어때?", "feeling"),
    ("ㅇㅇ", "ack"),
    ("야", "ack"),
    ("맞아!", "ack"),
    ("아니", "ack"),
])
def test_스몰톡은_검색_없이_바로_응답한다(query, kind):
    reply = detect_smalltalk(query)
    assert reply is not None, f"{query!r}가 스몰톡으로 인식되지 않았습니다"
    assert reply.kind == kind
    assert reply.answer.strip()


def test_정체성_질문에_일관된_이름으로_답한다():
    for query in ("너는 누구야", "너 이름이 뭐야?", "넌 누구니"):
        reply = detect_smalltalk(query)
        assert reply is not None
        assert "동똑이" in reply.answer


def test_기능_질문에는_할_수_있는_일을_안내한다():
    reply = detect_smalltalk("뭐 할 수 있어?")
    assert reply is not None and reply.kind == "capability"
    # 실제로 답할 수 있는 주제가 나열되어야 한다.
    for topic in ("학사일정", "공지", "학식", "연락처"):
        assert topic in reply.answer


@pytest.mark.parametrize("query", [
    "이번 주 학사일정 알려줘",
    "졸업요건이 뭐야",
    "오늘 학식 뭐 나와?",
    "컴퓨터공학과 사무실 전화번호 알려줘",
])
def test_실질_질문은_스몰톡으로_가로채지_않는다(query):
    assert detect_smalltalk(query) is None


def test_인사에_질문이_붙으면_검색으로_보낸다():
    """'안녕하세요, 졸업요건 알려주세요'를 인사로 처리하면 질문을 잃는다."""
    assert detect_smalltalk("안녕하세요 졸업요건 알려주세요") is None


def test_빈_입력은_스몰톡이_아니다():
    assert detect_smalltalk("") is None
    assert detect_smalltalk("   ") is None


# ---------------------------------------------------------------- 후속 발화

HISTORY = "사용자: 통계학과 전공필수 과목 알려줘\n동똑이: 통계학개론, 수리통계학 등이 있습니다."


@pytest.mark.parametrize("query", [
    "그럼 통계학과 과목 추천해줘",
    "그 과목은 몇 학점이야?",
])
def test_어휘가_겹치는_후속_발화만_재구성_대상이다(query):
    assert needs_context_rewrite(query, HISTORY) is True


@pytest.mark.parametrize("query", [
    "샤갈",
    "자세히 알려줘",
    "그 강의 신청해도 돼?",
    "거기 오늘 열어?",
    "너가 예시로 짜줘",
    "방금 뭐 물어봤지?",
])
def test_직전_질문과_어휘가_안_겹치면_새_주제로_취급한다(query):
    assert needs_context_rewrite(query, HISTORY) is False
    assert history_allows_context_rewrite(query, HISTORY) is False


def test_이력이_없으면_재구성하지_않는다():
    assert needs_context_rewrite("자세히 알려줘", "") is False


def test_스몰톡은_재구성_대상이_아니다():
    """'맞아'를 직전 질문과 합쳐 검색하면 엉뚱한 결과가 나온다."""
    assert needs_context_rewrite("맞아", HISTORY) is False
    assert needs_context_rewrite("안녕", HISTORY) is False


def test_내용어가_있는_짧은_질문은_그대로_검색한다():
    """'시험 언제 봐?'는 단독으로도 검색이 된다."""
    assert needs_context_rewrite("시험 언제 봐?", HISTORY) is False
    assert needs_context_rewrite("학식 뭐야", HISTORY) is False


def test_대화_이력에서_직전_사용자_질문을_찾는다():
    history = (
        "사용자: 첫 질문\n동똑이: 첫 답변\n"
        "사용자: 통계학과 전공필수 과목 알려줘\n동똑이: 통계학개론 등이 있습니다."
    )
    assert extract_last_user_question(history) == "통계학과 전공필수 과목 알려줘"


def test_이력_형식을_알_수_없으면_None을_준다():
    assert extract_last_user_question("형식이 다른 텍스트") is None
    assert extract_last_user_question("") is None


def test_후속_발화를_직전_질문과_합쳐_단독_검색_가능하게_만든다():
    rewritten = rewrite_with_context("그 과목 자세히 알려줘", HISTORY)
    assert "통계학과 전공필수 과목" in rewritten
    # 원문을 버리지 않는다.
    assert "그 과목 자세히 알려줘" in rewritten


def test_무관한_짧은_발화는_직전_질문으로_치환하지_않는다():
    history = (
        "사용자: 현재 모집 중인 공모전 알려줘\n"
        "동똑이: 현재 공모전은 코오롱 챌린지입니다."
    )

    assert rewrite_with_context("샤갈", history) == "샤갈"
    assert not has_lexical_overlap("샤갈", "현재 모집 중인 공모전")


def test_분석_재작성은_원문을_앞에_보존하고_무관한_치환은_거부한다():
    assert preserve_original_query(
        "수강신청 기간은?",
        "수강신청 일정",
    ) == "수강신청 기간은? 수강신청 일정"
    assert preserve_original_query(
        "샤갈",
        "현재 모집 중인 공모전",
    ) is None


def test_직전_질문을_찾지_못하면_원문을_그대로_쓴다():
    assert rewrite_with_context("자세히 알려줘", "형식 불명") == "자세히 알려줘"


def test_같은_질문을_반복하면_중복으로_합치지_않는다():
    history = "사용자: 오늘 학식 뭐 나와?\n동똑이: 제육덮밥입니다."
    assert rewrite_with_context("오늘 학식 뭐 나와?", history) == "오늘 학식 뭐 나와?"
