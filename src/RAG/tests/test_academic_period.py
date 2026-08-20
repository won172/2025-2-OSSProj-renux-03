"""학년도·학기 추출.

세 군데에 흩어져 서로 다르던 정규식을 한 모듈로 모았다. 표기 변형이 하나라도
빠지면 그 형태의 문서만 조용히 시기 미상이 되고, 다른 학기 질문에 섞여 들어온다.
실측 사례: "2026학년도 2학기 개강일"에 1학기 개강일(2026-03-03)이 정답보다
위에 올라왔다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils.academic_period import (  # noqa: E402
    SEASONAL,
    AcademicPeriod,
    extract_declared_period,
    extract_period,
    extract_requested_period,
    period_from_date,
)


@pytest.mark.parametrize(
    ("text", "year", "semester"),
    [
        ("2026학년도 2학기 수강신청 안내", 2026, "2"),
        ("2026년 2학기 등록 안내", 2026, "2"),
        ("2026-2학기 장학금 신청", 2026, "2"),
        ("2026-2 수강신청", 2026, "2"),
        ("26-2 수강신청 안내", 2026, "2"),
        ("2학기 개강일", None, "2"),
        ("2026학년도 신입생 안내", 2026, None),
        ("2026학년도 여름계절학기 재수강", 2026, SEASONAL),
        ("겨울 계절학기 수강신청", None, SEASONAL),
        ("일반 공지사항", None, None),
    ],
)
def test_extract_period_handles_every_notation_in_the_corpus(text, year, semester):
    period = extract_period(text)
    assert period.year == year
    assert period.semester == semester


def test_cohort_numbers_are_not_read_as_an_academic_year():
    """"2024학번"은 입학 코호트지 적용 학년도가 아니다.

    학년도로 읽으면 2024학번 대상 안내가 2024학년도 질문의 근거로 잘못 붙는다.
    """
    assert extract_period("2024학번 졸업요건 안내").year is None
    assert extract_period("22학번 이수기준").year is None


def test_compact_notation_is_not_confused_with_a_date():
    """"2026.2.15" 같은 날짜를 2026-2학기로 읽으면 안 된다."""
    assert extract_period("공지 2026.2.15 시행").semester is None


def test_declared_period_prefers_the_title_over_the_body():
    """제목은 문서가 스스로 밝힌 적용 범위다. 본문 예시로 넓히면 안 된다."""
    period = extract_declared_period(
        "2024학년도 1학기 이수 안내",
        "참고: 2022학년도 2학기에는 기준이 달랐습니다.",
    )
    assert (period.year, period.semester) == (2024, "1")


def test_declared_period_falls_back_to_the_body_when_the_title_is_silent():
    period = extract_declared_period("수강신청 안내", "2026학년도 2학기 수강신청을 시작합니다.")
    assert (period.year, period.semester) == (2026, "2")


@pytest.mark.parametrize(
    ("date", "year", "semester"),
    [
        ("2026-03-03", 2026, "1"),
        ("2026-09-01", 2026, "2"),
        ("2026-12-15", 2026, "2"),
        # 1·2월 일정은 직전 학년도 2학기에 속한다(2027-01 기말 = 2026학년도 2학기).
        ("2027-01-15", 2026, "2"),
        ("2027-02-19", 2026, "2"),
        ("", None, None),
    ],
)
def test_period_from_date_maps_the_academic_calendar(date, year, semester):
    period = period_from_date(date)
    assert (period.year, period.semester) == (year, semester)


# --------------------------------------------------------------------------- #
# 충돌 판정 — 배제형으로만 쓴다
# --------------------------------------------------------------------------- #
def test_unknown_period_never_conflicts():
    """공지 제목의 37.2%에는 학기 표기가 없다. 그것들을 떨어뜨리면 안 된다."""
    silent = AcademicPeriod()
    asked = AcademicPeriod(2026, "2")
    assert not silent.conflicts_with(asked)
    assert not asked.conflicts_with(silent)


def test_partially_known_period_only_conflicts_on_the_known_part():
    """연도만 아는 문서는 학기가 달라도 충돌이 아니다."""
    year_only = AcademicPeriod(2026, None)
    assert not year_only.conflicts_with(AcademicPeriod(2026, "1"))
    assert year_only.conflicts_with(AcademicPeriod(2025, "1"))


def test_declared_mismatch_conflicts():
    assert AcademicPeriod(2026, "1").conflicts_with(AcademicPeriod(2026, "2"))
    assert AcademicPeriod(2025, "2").conflicts_with(AcademicPeriod(2026, "2"))


def test_seasonal_conflicts_with_a_regular_semester():
    """여름계절학기 재수강 안내를 2학기 질문의 근거로 쓰면 안 된다."""
    assert AcademicPeriod(2026, SEASONAL).conflicts_with(AcademicPeriod(2026, "2"))


def test_the_ac001_regression_case():
    """실측 회귀: 1학기 개강일이 2학기 질문의 근거가 되면 안 된다.

    학사일정에는 학기 보정이 걸리지 않아 "개강"(2026-03-03)이 정답
    "개강/학기개시일"(2026-09-01)보다 위에 올라왔다.
    """
    asked = extract_requested_period("2026학년도 2학기 개강일이 언제야?")
    assert (asked.year, asked.semester) == (2026, "2")

    first_term = period_from_date("2026-03-03")
    second_term = period_from_date("2026-09-01")
    assert first_term.conflicts_with(asked)
    assert not second_term.conflicts_with(asked)


def test_relative_expressions_are_left_to_temporal_context():
    """"이번 학기"는 기준일이 있어야 풀린다 — 여기서 추측하면 두 곳이 갈린다."""
    assert not extract_requested_period("이번 학기 수강신청 언제야?")
