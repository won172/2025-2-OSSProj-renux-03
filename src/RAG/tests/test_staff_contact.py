"""연락처 질의에서 교직원 행을 고르는 규칙.

실측한 오답에서 출발한다.
    "컴퓨터공학과 학과사무실 전화번호"  → 1위가 대학원학과주임교수였다
    "호텔관광외식경영학부 사무실 연락처"  → 1위가 조교수였고 전화번호가 없었다
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.staff_contact import (  # noqa: E402
    contact_sort_key,
    describe_contact_fallback,
    is_administrative,
    office_intent_in,
)


@pytest.mark.parametrize("직위", ["조교", "팀원", "팀장", "과장", "행정팀원"])
def test_행정_직위를_창구로_본다(직위):
    assert is_administrative(직위) is True


@pytest.mark.parametrize("직위", ["교수", "부교수", "대학원학과주임교수", "연구원", "", "학과장(B)"])
def test_교원과_빈값은_창구가_아니다(직위):
    assert is_administrative(직위) is False


def test_조교수는_조교가_아니다():
    """'조교수'에 '조교'가 들어 있어 부분일치로 잡히면 교원이 창구로 올라간다."""
    assert is_administrative("조교수") is False
    assert is_administrative("조교") is True


@pytest.mark.parametrize(
    "질문", ["컴퓨터공학과 학과사무실 전화번호", "통계학과 행정실 번호", "과사무실 어디야"]
)
def test_사무실_의도를_읽는다(질문):
    assert office_intent_in(질문) is True


def test_번호가_없는_행은_뒤로_간다():
    있음 = contact_sort_key("조교", "054-770-2522", office_intent=True)
    없음 = contact_sort_key("조교", "", office_intent=True)
    assert 있음 < 없음


def test_사무실_질의에서_행정직이_교원보다_먼저다():
    행정 = contact_sort_key("조교", "02-2260-3679", office_intent=True)
    교원 = contact_sort_key("대학원학과주임교수", "02-2260-3218", office_intent=True)
    assert 행정 < 교원


def test_사무실_질의가_아니면_직위로_가르지_않는다():
    행정 = contact_sort_key("조교", "02-1", office_intent=False)
    교원 = contact_sort_key("교수", "02-2", office_intent=False)
    assert 행정 == 교원


def test_번호_유무가_직위보다_우선한다():
    """번호를 물었는데 번호가 없으면 직위가 맞아도 답이 될 수 없다."""
    번호없는_행정 = contact_sort_key("조교", "", office_intent=True)
    번호있는_교원 = contact_sort_key("교수", "02-2260-3218", office_intent=True)
    assert 번호있는_교원 < 번호없는_행정


def test_행정직이_없는_학과에는_무엇을_주는지_밝힌다():
    # 통계학과는 6행이 전부 교원이라 사무실 번호가 존재하지 않는다.
    안내 = describe_contact_fallback(
        "통계학과", ["학과장(B)", "교수", "조교수"], office_intent=True
    )
    assert 안내 is not None
    assert "확인되지 않습니다" in 안내


def test_행정직이_있으면_안내를_붙이지_않는다():
    assert describe_contact_fallback("컴퓨터공학과", ["조교", "교수"], office_intent=True) is None


def test_사무실_질의가_아니면_안내가_없다():
    assert describe_contact_fallback("통계학과", ["교수"], office_intent=False) is None


def test_안내가_생성_지시로_전달된다(monkeypatch):
    """행정직이 없는 학과에서는 무엇을 주는지 답변 첫 문장에 밝히게 한다."""
    import pandas as pd

    import api.rag_service as rag_service

    selected = pd.DataFrame(
        [
            {"dataset": "staff", "topics": "통계학과", "staff_position": "학과장(B)"},
            {"dataset": "staff", "topics": "통계학과", "staff_position": "교수"},
        ]
    )
    지시 = rag_service._staff_contact_response_instruction("통계학과 사무실 전화번호", selected)
    assert 지시 is not None
    assert "확인되지 않습니다" in 지시
    assert "첫 문장" in 지시


def test_행정직이_있으면_지시를_붙이지_않는다():
    import pandas as pd

    import api.rag_service as rag_service

    selected = pd.DataFrame(
        [{"dataset": "staff", "topics": "컴퓨터공학과", "staff_position": "조교"}]
    )
    assert rag_service._staff_contact_response_instruction("컴퓨터공학과 사무실 번호", selected) is None


def test_사무실_질의가_아니면_지시가_없다():
    import pandas as pd

    import api.rag_service as rag_service

    selected = pd.DataFrame(
        [{"dataset": "staff", "topics": "통계학과", "staff_position": "교수"}]
    )
    assert rag_service._staff_contact_response_instruction("통계학과 교수님 누구야", selected) is None
