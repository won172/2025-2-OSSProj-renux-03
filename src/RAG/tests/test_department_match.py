"""질문에 등장한 학과명을 색인된 major 값으로 해석하는 규칙.

실측 실패에서 출발한다. `컴퓨터·AI학부`는 정확 표기로만 검색이 성공했고,
`컴퓨터AI학부`·`컴퓨터 AI학부`는 각각 경영정보학과·정보통신공학전공을 돌려줬다.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import department_match
from src.utils.department_match import resolve_departments


@pytest.fixture()
def 학과_사전(tmp_path, monkeypatch):
    """실제 데이터 파일과 무관하게 규칙만 검증하도록 최소 사전을 꾸린다."""
    courses = tmp_path / "dongguk_courses_all.csv"
    with courses.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["department_name", "title"])
        for name in (
            "컴퓨터·AI학부",
            "영어영문학부 영어문학전공",
            "영어영문학부 영어통번역학전공",
            "약학과",
            "화학과",
            "통계학과",
            "불교학부",
        ):
            writer.writerow([name, "샘플교과목"])

    aliases = tmp_path / "dongguk_department_aliases.csv"
    with aliases.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["alias", "canonical_department_name"])
        writer.writerow(["불교학과", "불교학부"])

    monkeypatch.setitem(department_match.DATA_SOURCES, "courses_all", courses)
    department_match._department_entries.cache_clear()
    yield
    department_match._department_entries.cache_clear()


@pytest.mark.parametrize(
    "질문",
    ["컴퓨터·AI학부 교과과정", "컴퓨터AI학부 교과과정", "컴퓨터 AI학부 전공과목"],
)
def test_가운뎃점_표기가_달라도_같은_학과로_해석한다(학과_사전, 질문):
    assert resolve_departments(질문) == ("컴퓨터·AI학부",)


def test_학부만_말하면_소속_전공을_모두_돌려준다(학과_사전):
    assert resolve_departments("영어영문학부 교과과정") == (
        "영어영문학부 영어문학전공",
        "영어영문학부 영어통번역학전공",
    )


def test_별칭은_색인에_있는_표준명으로_이어진다(학과_사전):
    assert resolve_departments("불교학과 전공필수") == ("불교학부",)


@pytest.mark.parametrize(
    "질문",
    ["화학과제 제출 기한", "약학과목 시간표", "수학과목 추천해줘"],
)
def test_더_긴_낱말의_일부는_학과로_보지_않는다(학과_사전, 질문):
    # "화학과제"에서 `화학과`를 떼어내면 엉뚱한 학과로 검색 범위가 좁혀진다.
    assert resolve_departments(질문) == ()


@pytest.mark.parametrize("질문", ["졸업요건이 뭐야", "오늘 학식 뭐야", "수강신청 언제야?", ""])
def test_학과가_없는_질문에는_아무것도_돌려주지_않는다(학과_사전, 질문):
    assert resolve_departments(질문) == ()


def test_짧은_학과명도_경계가_맞으면_해석한다(학과_사전):
    assert resolve_departments("약학과 교과과정") == ("약학과",)


def test_데이터_파일이_없으면_조용히_비어_있다(tmp_path, monkeypatch):
    monkeypatch.setitem(
        department_match.DATA_SOURCES, "courses_all", tmp_path / "없는파일.csv"
    )
    department_match._department_entries.cache_clear()
    try:
        assert resolve_departments("통계학과 교과과정") == ()
    finally:
        department_match._department_entries.cache_clear()


# --- 조사 경계 ---------------------------------------------------------------
#
# 한국어 조사는 명사에 띄어쓰기 없이 붙는다. 오른쪽 경계를 `(?![가-힣])`로만 두면
# "화학과의"·"학부가"·"학과에서"가 전부 인식에 실패했다. 실측: 조사 25개 전부 실패,
# 실제 로그 16건(폴백 3건)이 여기 걸렸다.
#
# 경계를 없애는 것은 답이 아니다 — "수학과목"이 "수학과"로 잡힌다.
# 알려진 조사만 흘려보내고 그 뒤에 다시 한글이 오면 거부한다.


@pytest.mark.parametrize(
    "질문",
    [
        "화학과의 졸업학점은 몇 학점이야?",
        "화학과는 어디 있어",
        "화학과가 취업하는 분야",
        "화학과에서 전과할 때 조건",
        "화학과까지 몇 분 걸려",
        "화학과로 전과하려면",
        "화학과와 비교해줘",
        "난 화학과인데 2학년 때 뭐들어?",
        "화학과야",
        "화학과도 복수전공 되나요",
        "화학과만 해당되나요",
        "화학과부터 알려줘",
    ],
)
def test_조사가_붙어도_학과를_인식한다(학과_사전, 질문):
    assert department_match.resolve_departments(질문) == ("화학과",)


@pytest.mark.parametrize(
    "질문",
    ["수학과목 추천해줘", "화학과목 알려줘", "화학과학생회 연락처", "화학과교수님 누구야"],
)
def test_조사가_아닌_한글이_이어지면_학과로_보지_않는다(학과_사전, 질문):
    """조사 허용이 '더 긴 낱말의 일부' 가드를 뚫으면 안 된다.

    "목"·"학생회"·"교수님"은 조사가 아니므로 경계에서 계속 거부돼야 한다.
    """
    assert department_match.resolve_departments(질문) == ()


def test_학부에_조사가_붙어도_전공을_모두_돌려준다(학과_사전):
    """조사 허용이 학부→전공 전개를 깨지 않아야 한다."""
    assert department_match.resolve_departments("영어영문학부가 어떤 전공이 있어") == (
        "영어영문학부 영어문학전공",
        "영어영문학부 영어통번역학전공",
    )


def test_별칭에도_조사가_붙을_수_있다(학과_사전):
    assert department_match.resolve_departments("불교학과의 사무실 번호") == ("불교학부",)


@pytest.mark.parametrize(
    "질문",
    [
        # 실제 로그에서 폴백했거나 학과 필터를 놓쳤던 질문들.
        "통계학과인데 졸업하려면 세미나 2개 꼭 들어야되냐",
        "통계학과의 교수진에 대한 정보가 더 필요해요.",
        "난 통계학과와 소프트웨어AI 연계전공을 하고 있어",
        "통계학과가 취업하는 분야",
        "통계학과에선 보통 뭐 듣지?",
    ],
)
def test_실측_실패_질문이_해석된다(학과_사전, 질문):
    assert department_match.resolve_departments(질문) == ("통계학과",)
