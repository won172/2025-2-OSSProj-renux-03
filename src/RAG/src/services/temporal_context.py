"""요청 전체가 공유하는 기준 시점과 학사 시기.

RAG 단계마다 ``datetime.now()``를 따로 호출하면 평가 재현성이 깨지고, 질의분석과
검색·생성이 서로 다른 시점을 전제로 동작할 수 있다. 이 모듈은 요청 입구에서 기준일을
한 번 정한 뒤 모든 단계에 전달하기 위한 작은 값 객체를 제공한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Mapping


KST = timezone(timedelta(hours=9))
_ACADEMIC_YEAR_RE = re.compile(r"\b(20\d{2})\s*학년도")
_SEMESTER_RE = re.compile(r"([12])\s*학기")


@dataclass(frozen=True)
class TemporalContext:
    as_of: date
    academic_year: int
    semester: int
    phase: str

    @property
    def prompt_text(self) -> str:
        return (
            f"기준일: {self.as_of.isoformat()} (KST)\n"
            f"현재 학사 시기: {self.academic_year}학년도 {self.semester}학기 {self.phase}"
        )

    @property
    def current_date_text(self) -> str:
        weekday = "월화수목금토일"[self.as_of.weekday()]
        return (
            f"{self.as_of.year}년 {self.as_of.month}월 {self.as_of.day}일"
            f"({weekday}, KST)"
        )


def _calendar_period(anchor: date) -> tuple[int, int, str]:
    if anchor.month <= 2:
        return anchor.year - 1, 2, "겨울방학"
    if anchor.month <= 6:
        return anchor.year, 1, "학기중"
    if anchor.month <= 8:
        return anchor.year, 2, "수강준비기간"
    if anchor.month <= 12:
        return anchor.year, 2, "학기중"
    raise AssertionError("unreachable")


@dataclass(frozen=True)
class ScheduleBoundary:
    title: str
    start: date
    end: date


def _parse_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _coerce_boundary(value: object) -> ScheduleBoundary | None:
    if isinstance(value, ScheduleBoundary):
        return value
    if isinstance(value, Mapping):
        title = str(value.get("title") or "")
        start = _parse_date(value.get("start") or value.get("start_date"))
        end = _parse_date(value.get("end") or value.get("end_date")) or start
    elif isinstance(value, (tuple, list)) and len(value) >= 2:
        title = str(value[0] or "")
        start = _parse_date(value[1])
        end = _parse_date(value[2]) if len(value) >= 3 else start
        end = end or start
    else:
        return None
    if not title or start is None or end is None:
        return None
    return ScheduleBoundary(title=title, start=start, end=end)


def _term_identity(start: date) -> tuple[int, int]:
    return start.year, 1 if start.month <= 7 else 2


def _schedule_period(
    anchor: date,
    schedule_rows: Iterable[object],
) -> tuple[int, int, str] | None:
    rows = [
        boundary
        for value in schedule_rows
        if (boundary := _coerce_boundary(value)) is not None
    ]
    if not rows:
        return None

    # 수강신청이 시작되면 아직 방학 중이어도 다음 학기의 수강준비기간으로 본다.
    registration_periods: list[tuple[date, int, int]] = []
    for row in rows:
        compact = re.sub(r"\s+", "", row.title)
        if "수강신청" not in compact or "계절" in compact or "신입생" in compact:
            continue
        year_match = _ACADEMIC_YEAR_RE.search(row.title)
        semester_match = _SEMESTER_RE.search(row.title)
        if year_match is None or semester_match is None:
            continue
        academic_year = int(year_match.group(1))
        semester = int(semester_match.group(1))
        # 원천 표의 잘 알려진 "2026학년도 1학기 대학원 수강 신청"(2027-02)
        # 같은 연도 오기는 날짜와 학기 조합으로 보수적으로 바로잡는다.
        if semester == 1 and row.start.month <= 2 and academic_year < row.start.year:
            academic_year = row.start.year
        registration_periods.append((row.start, academic_year, semester))

    openings_by_term: dict[tuple[int, int], date] = {}
    for row in rows:
        compact = re.sub(r"\s+", "", row.title)
        if "개강" not in compact and "학기개시일" not in compact:
            continue
        identity = _term_identity(row.start)
        openings_by_term[identity] = min(
            openings_by_term.get(identity, row.start),
            row.start,
        )

    active_registration = [
        (start, year, semester)
        for start, year, semester in registration_periods
        if start <= anchor
        and anchor < openings_by_term.get(
            (year, semester),
            date(year, 3 if semester == 1 else 9, 1),
        )
    ]
    if active_registration:
        _, year, semester = max(active_registration, key=lambda item: item[0])
        return year, semester, "수강준비기간"

    openings = sorted(
        (start, year, semester)
        for (year, semester), start in openings_by_term.items()
    )
    endings = sorted(
        row.start
        for row in rows
        if re.sub(r"\s+", "", row.title) == "종강"
    )
    term_intervals: list[tuple[date, date, int, int]] = []
    for index, (opening, year, semester) in enumerate(openings):
        next_opening = openings[index + 1][0] if index + 1 < len(openings) else None
        term_end = next(
            (
                ending
                for ending in endings
                if ending >= opening
                and (next_opening is None or ending < next_opening)
            ),
            None,
        )
        if term_end is not None:
            term_intervals.append((opening, term_end, year, semester))
            if opening <= anchor <= term_end:
                return year, semester, "학기중"

    for row in rows:
        compact = re.sub(r"\s+", "", row.title)
        if "여름방학" in compact and row.start <= anchor <= row.end:
            return row.start.year, 1, "여름방학"
        if "겨울방학" in compact and row.start <= anchor <= row.end:
            return row.start.year, 2, "겨울방학"

    # 종강과 공식 방학 시작 사이의 보강·성적처리 구간을 학기중으로 오인하지 않는다.
    past_terms = [
        (ending, year, semester)
        for _opening, ending, year, semester in term_intervals
        if ending < anchor
    ]
    future_break_starts = sorted(
        row.start
        for row in rows
        if "방학" in re.sub(r"\s+", "", row.title) and row.start > anchor
    )
    if past_terms and future_break_starts:
        latest_end, year, semester = max(past_terms, key=lambda item: item[0])
        if latest_end < anchor < future_break_starts[0]:
            return year, semester, "학기전환기간"
    return None


def build_temporal_context(
    as_of: date | None = None,
    schedule_titles: Iterable[str] = (),
    schedule_rows: Iterable[object] = (),
) -> TemporalContext:
    """기준일과 가까운 일정 제목을 참고해 요청의 학사 시기를 결정한다.

    일정 데이터가 비어 있거나 제목에서 학년도를 읽을 수 없을 때는 일반적인 학기 달력으로
    결정한다. 일정 제목은 학년도 보정에만 사용하며, 명시된 기준일 자체는 절대 바꾸지 않는다.
    """
    anchor = as_of or datetime.now(KST).date()
    schedule_period = _schedule_period(anchor, schedule_rows)
    calendar_year, semester, phase = (
        schedule_period if schedule_period is not None else _calendar_period(anchor)
    )
    declared_years = [
        int(match.group(1))
        for title in schedule_titles
        if (match := _ACADEMIC_YEAR_RE.search(str(title or ""))) is not None
    ]
    if declared_years:
        nearby = [year for year in declared_years if abs(year - calendar_year) <= 1]
        academic_year = max(nearby, key=lambda year: (year == calendar_year, year)) if nearby else calendar_year
    else:
        academic_year = calendar_year
    return TemporalContext(
        as_of=anchor,
        academic_year=academic_year,
        semester=semester,
        phase=phase,
    )


__all__ = [
    "KST",
    "ScheduleBoundary",
    "TemporalContext",
    "build_temporal_context",
]
