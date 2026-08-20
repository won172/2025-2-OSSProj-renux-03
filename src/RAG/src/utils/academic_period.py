"""문서와 질의에서 학년도·학기를 뽑는다.

**왜 한 곳에 모으나.** 같은 일을 하는 정규식이 세 군데에 흩어져 있었고 서로 달랐다.

  `hybrid._academic_period_title_adjustment`  제목만 보고 점수를 ±0.30 가감.
                                              **공지에만** 걸려서 학사일정은 무방비였다.
  `rag_service._candidate_academic_period`    제목 우선, 본문 앞부분 폴백, schedule은 날짜에서 유도.
  `retrieval_context._academic_period`        검색용 문맥 헤더 문자열 생성.

세 구현이 인식하는 표기가 달라서, 어떤 경로를 타느냐에 따라 같은 문서의 학기가
달라졌다. 실측 사례: "2026학년도 **2학기** 개강일이 언제야?"에서 1학기 개강일
(2026-03-03)이 정답(2026-09-01)보다 위에 올라왔다 — 학사일정에는 학기 보정이
아예 걸리지 않았기 때문이다.

**왜 배제형으로만 쓰나.** 공지 제목의 37.2%에는 학기 표기가 없다. 학기를 가진
문서만 남기는 필터를 걸면 그 37%가 통째로 사라진다. 그래서 이 모듈의 결과는
"이 문서가 **다른** 학기를 선언했는가"를 판정하는 데만 쓴다. 선언이 없는 문서는
어느 학기 질문에도 후보로 남는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

# 계절학기는 1/2학기와 배타적인 별도 값으로 다룬다. "여름계절학기 재수강"을
# 2학기 질문의 근거로 쓰면 안 되고, 그 반대도 마찬가지다.
SEASONAL = "seasonal"

_YEAR = r"(?P<year>20\d{2})"
_SHORT_YEAR = r"(?P<short>\d{2})"

# 표기 변형. 코퍼스 실측 분포(공지 제목 11,481건 기준):
#   2026학년도 2학기  18.2%   ·  2026-2 / 2026-2학기  17.5%
#   2학기(연도 없음)   20.8%   ·  2026년 2학기          1.0%
#   계절학기            5.9%   ·  26-2                 0.2%
_FULL_PERIOD = re.compile(
    rf"{_YEAR}\s*(?:학년도|년도|년)\s*(?P<semester>[12])\s*학기"
)
_COMPACT_PERIOD = re.compile(
    rf"(?<![\d.])({_YEAR})\s*[-–~]\s*(?P<semester>[12])(?!\d)"
)
_SHORT_PERIOD = re.compile(
    rf"(?<![\d.]){_SHORT_YEAR}\s*[-–]\s*(?P<semester>[12])(?!\d)"
)
_YEAR_ONLY = re.compile(rf"{_YEAR}\s*(?:학년도|년도|년)")
# 앞에 숫자나 하이픈이 붙으면 "2026-2학기"의 꼬리이므로 단독 학기로 세지 않는다.
_SEMESTER_ONLY = re.compile(r"(?<![\d\-–])(?P<semester>[12])\s*학기")
_SEASONAL = re.compile(r"(?:여름|겨울|하계|동계)?\s*계절\s*학기")
# "2024학번", "22학번"은 학기가 아니라 입학 코호트다. 학년도로 오인하면 안 된다.
_COHORT = re.compile(r"\d{2,4}\s*학번")


@dataclass(frozen=True)
class AcademicPeriod:
    """문서나 질의가 **선언한** 적용 시기. 모르면 각 항목이 None."""

    year: Optional[int] = None
    semester: Optional[str] = None  # "1" | "2" | SEASONAL

    def __bool__(self) -> bool:
        return self.year is not None or self.semester is not None

    def conflicts_with(self, other: "AcademicPeriod") -> bool:
        """두 시기가 **명시적으로 어긋나는가**.

        한쪽이 모르는 항목은 충돌로 치지 않는다. 그래야 학기 표기가 없는 37%가
        살아남는다. 둘 다 선언했는데 값이 다를 때만 True다.
        """
        if self.year is not None and other.year is not None and self.year != other.year:
            return True
        if (
            self.semester is not None
            and other.semester is not None
            and self.semester != other.semester
        ):
            return True
        return False


def _strip_cohorts(text: str) -> str:
    """학번 표기를 지운다 — "2024학번 졸업요건"의 2024는 적용 학년도가 아니다."""
    return _COHORT.sub(" ", text)


def extract_period(text: object) -> AcademicPeriod:
    """한 덩어리의 글에서 학년도·학기를 뽑는다. 없으면 빈 결과."""
    if text is None:
        return AcademicPeriod()
    cleaned = _strip_cohorts(str(text))
    if not cleaned.strip():
        return AcademicPeriod()

    for pattern in (_FULL_PERIOD, _COMPACT_PERIOD):
        match = pattern.search(cleaned)
        if match:
            return AcademicPeriod(int(match.group("year")), match.group("semester"))

    match = _SHORT_PERIOD.search(cleaned)
    if match:
        return AcademicPeriod(2000 + int(match.group("short")), match.group("semester"))

    year_match = _YEAR_ONLY.search(cleaned)
    year = int(year_match.group("year")) if year_match else None

    # 계절학기는 1/2학기 표기보다 우선한다. "2026학년도 여름계절학기"에는 보통
    # 둘 다 없지만, "2학기 및 겨울계절학기" 같은 제목에서는 계절이 실제 범위다.
    if _SEASONAL.search(cleaned):
        return AcademicPeriod(year, SEASONAL)

    semester_match = _SEMESTER_ONLY.search(cleaned)
    if semester_match:
        return AcademicPeriod(year, semester_match.group("semester"))
    return AcademicPeriod(year, None)


def extract_declared_period(
    title: object,
    body: object = "",
    *,
    body_chars: int = 320,
) -> AcademicPeriod:
    """문서가 **선언한** 적용 범위. 제목이 우선이다.

    제목은 그 문서가 스스로 밝힌 적용 범위다. 본문에는 비교표나 예시로 다른
    연도가 흔히 등장하므로, 제목에 선언이 있으면 본문으로 넓히지 않는다.
    그렇게 하지 않으면 "2024학번 ..." 제목이 본문 예시의 2022를 주워 와
    2022 코호트 질문의 근거로 잘못 쓰인다.
    """
    from_title = extract_period(title)
    if from_title:
        return from_title
    return extract_period(str(body or "")[:body_chars])


def period_from_date(value: object) -> AcademicPeriod:
    """날짜에서 학년도·학기를 유도한다(학사일정처럼 시작일이 정본인 경우).

    3~8월을 1학기, 9~2월을 2학기로 본다. 1·2월은 직전 학년도의 2학기다 —
    2027-01의 기말은 2026학년도 2학기 일정이다.
    """
    import pandas as pd

    parsed = pd.to_datetime(value, errors="coerce")
    if parsed is None or pd.isna(parsed):
        return AcademicPeriod()
    month = int(parsed.month)
    year = int(parsed.year)
    if 3 <= month <= 8:
        return AcademicPeriod(year, "1")
    if month >= 9:
        return AcademicPeriod(year, "2")
    return AcademicPeriod(year - 1, "2")


def extract_requested_period(question: object) -> AcademicPeriod:
    """질의가 **명시적으로 지목한** 시기.

    상대 표현("이번 학기")은 여기서 다루지 않는다. 그건 기준일이 있어야 풀리고
    `services/temporal_context.py`가 이미 담당한다. 여기서 추측하면 두 곳이
    서로 다른 답을 내게 된다.
    """
    return extract_period(question)


def strongest_period(values: Iterable[object]) -> AcademicPeriod:
    """여러 후보 글에서 처음으로 잡히는 시기를 돌려준다(앞선 것이 우선)."""
    for value in values:
        period = extract_period(value)
        if period:
            return period
    return AcademicPeriod()


__all__ = [
    "SEASONAL",
    "AcademicPeriod",
    "extract_period",
    "extract_declared_period",
    "extract_requested_period",
    "period_from_date",
    "strongest_period",
]
