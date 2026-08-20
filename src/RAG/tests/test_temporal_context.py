from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.temporal_context import build_temporal_context
from src.utils.date_parser import extract_date_filter_from_query


SCHEDULE_ROWS = [
    {"title": "학기 개시일", "start_date": "2026-03-01", "end_date": "2026-03-01"},
    {"title": "개강", "start_date": "2026-03-03", "end_date": "2026-03-03"},
    {"title": "종강", "start_date": "2026-06-15", "end_date": "2026-06-15"},
    {"title": "여름 방학", "start_date": "2026-06-23", "end_date": "2026-08-31"},
    {
        "title": "2026학년도 2학기 대학원 수강신청",
        "start_date": "2026-07-31",
        "end_date": "2026-08-05",
    },
    {"title": "개강/학기개시일", "start_date": "2026-09-01", "end_date": "2026-09-01"},
    {"title": "종강", "start_date": "2026-12-14", "end_date": "2026-12-14"},
    {"title": "겨울 방학", "start_date": "2026-12-22", "end_date": "2027-02-28"},
    {
        "title": "2027학년도 1학기 학부 수강 신청",
        "start_date": "2027-02-01",
        "end_date": "2027-02-05",
    },
]


def test_as_of_is_shared_by_temporal_context_and_relative_date_parser():
    temporal = build_temporal_context(date(2026, 7, 30))
    parsed = extract_date_filter_from_query("다음 주 학사일정", today=temporal.as_of)

    assert temporal.academic_year == 2026
    assert temporal.semester == 2
    assert parsed is not None
    assert parsed.start == date(2026, 8, 3)
    assert parsed.end == date(2026, 8, 9)


def test_january_belongs_to_previous_academic_year_second_semester():
    temporal = build_temporal_context(date(2027, 1, 10))

    assert temporal.academic_year == 2026
    assert temporal.semester == 2
    assert temporal.phase == "겨울방학"


def test_schedule_boundaries_override_month_based_semester_guess():
    transition = build_temporal_context(
        date(2026, 6, 16),
        schedule_rows=SCHEDULE_ROWS,
    )
    summer = build_temporal_context(
        date(2026, 6, 23),
        schedule_rows=SCHEDULE_ROWS,
    )
    registration = build_temporal_context(
        date(2026, 7, 31),
        schedule_rows=SCHEDULE_ROWS,
    )

    assert (
        transition.academic_year,
        transition.semester,
        transition.phase,
    ) == (2026, 1, "학기전환기간")
    assert (summer.academic_year, summer.semester, summer.phase) == (
        2026,
        1,
        "여름방학",
    )
    assert (
        registration.academic_year,
        registration.semester,
        registration.phase,
    ) == (2026, 2, "수강준비기간")


def test_schedule_boundary_marks_winter_and_next_registration_separately():
    winter = build_temporal_context(
        date(2027, 1, 15),
        schedule_rows=SCHEDULE_ROWS,
    )
    next_registration = build_temporal_context(
        date(2027, 2, 1),
        schedule_rows=SCHEDULE_ROWS,
    )

    assert (winter.academic_year, winter.semester, winter.phase) == (
        2026,
        2,
        "겨울방학",
    )
    assert (
        next_registration.academic_year,
        next_registration.semester,
        next_registration.phase,
    ) == (2027, 1, "수강준비기간")
