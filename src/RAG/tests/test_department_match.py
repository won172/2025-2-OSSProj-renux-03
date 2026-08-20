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
