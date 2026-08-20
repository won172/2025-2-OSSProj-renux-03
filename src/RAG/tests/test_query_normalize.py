"""질의 정규화(오타·부서 별칭) 테스트. 로그에 실제로 들어온 표현을 쓴다."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import rag_service  # noqa: E402
from src.utils.query_normalize import (  # noqa: E402
    correct_typos,
    expand_department_aliases,
    normalize_query,
)


@pytest.mark.parametrize("wrong,expected_fragment", [
    ("긱사 입사서류 머머내야돼?", "기숙사"),      # 실제 폴백 사례
    ("등럭금 고지서 출력 어디서해?", "등록금"),   # 실제 폴백 사례
    ("학샤일정 알려줘", "학사일정"),
])
def test_알려진_오타를_표준_표기로_바꾼다(wrong, expected_fragment):
    assert expected_fragment in correct_typos(wrong)


def test_오타가_없으면_원문을_그대로_둔다():
    query = "이번 주 학사일정 알려줘"
    assert correct_typos(query) == query


def test_검색에_불리한_복합명사_띄어쓰기를_정규화한다():
    assert correct_typos("학부 수강 신청 기간") == "학부 수강신청 기간"
    assert correct_typos("이번 주 학사 일정") == "이번 주 학사일정"


def test_연락처_질문의_부서_표현을_동의어로_넓힌다():
    """staff로 정확히 라우팅되고도 표기 차이로 실패했던 사례."""
    variants = expand_department_aliases("컴퓨터공학과 학과사무실 연락처 알려줘")
    assert variants, "동의어 검색어가 생성되지 않았습니다"
    joined = " ".join(variants)
    assert "행정실" in joined
    assert "교학팀" in joined
    # 원문의 나머지 부분은 유지된다.
    assert all("컴퓨터공학과" in v for v in variants)


def test_내선번호_질문도_연락처_신호로_본다():
    assert expand_department_aliases("행정실 내선번호 알려줘")


def test_연락처_맥락이_아니면_동의어를_붙이지_않는다():
    """'행정실 위치'에 동의어를 붙이면 검색이 흐려진다."""
    assert expand_department_aliases("행정실 위치가 어디야") == []


def test_부서_표현이_없으면_동의어가_없다():
    assert expand_department_aliases("장학금 신청 기간이 언제야?") == []


def test_기숙사와_생활관은_서로_동의어다():
    variants = expand_department_aliases("기숙사 담당 부서 연락처")
    joined = " ".join(variants)
    assert "생활관" in joined


def test_정규화는_교정본과_추가_검색어를_함께_돌려준다():
    corrected, extras = normalize_query("긱사 담당 부서 연락처 알려줘")
    assert "기숙사" in corrected
    # 교정본 자체가 후보에 들어가고, 부서 동의어까지 확장된다.
    assert corrected in extras
    assert any("생활관" in extra for extra in extras)


def test_바꿀_것이_없으면_추가_검색어도_없다():
    corrected, extras = normalize_query("이번 주 학사일정 알려줘")
    assert corrected == "이번 주 학사일정 알려줘"
    assert extras == []


def test_추가_검색어에_중복이_없다():
    _, extras = normalize_query("학과사무실 연락처 알려줘")
    assert len(extras) == len(set(extras))


def test_질의분석은_명확화_판정_전에_교정본을_받는다():
    assert rag_service._query_for_analysis("긱사 입사서류 머머내야돼?") == (
        "기숙사 입사서류 머머내야돼?"
    )
