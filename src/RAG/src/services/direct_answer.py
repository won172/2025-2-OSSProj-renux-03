"""시점형 질문을 정형 데이터에서 먼저 답하는 경로.

폴백 로그 118건을 분석한 결과, 실패의 절반이 "오늘/이번 주 ○○ 알려줘"·"개강일 언제야"
같은 **시점 질문**이었다. 이 질문들은 답이 학사일정·식단 표에 이미 있는데도, 상대 날짜
표현("오늘")이 문서 본문과 벡터로 매칭되지 않아 빈손으로 끝났다.

날짜·기간을 묻는 질문은 텍스트 검색 점수보다 학사일정/식단 행 조회가 더 정확하다.
호출부는 이 모듈의 판정 함수를 이용해 해당 질문만 검색보다 먼저 처리한다.
"""
from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterable

from src.utils.audience import compact_text, derive_audience, query_audience


# ---------------------------------------------------------------- 공통

@dataclass
class DirectAnswer:
    """구제 경로가 만든 답변. sources는 화면의 출처 카드에 그대로 쓰인다."""
    answer: str
    sources: list[dict] = field(default_factory=list)
    kind: str = "direct"


@dataclass(frozen=True)
class NoticePeriodRow:
    """미래 공지에서 라벨이 붙은 신청·접수 기간을 읽기 위한 최소 필드."""

    title: str
    content: str
    notice_id: str | int | None = None
    published_at: str | None = None
    url: str | None = None
    board: str = ""
    category: str = ""


def parse_flexible_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    # 표에는 'YYYY-MM-DD'와 'YYYY-MM-DD 00:00:00'이 섞여 있다.
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text[:len(fmt) + 2].strip(), fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _format_date(value: date) -> str:
    weekday = "월화수목금토일"[value.weekday()]
    return f"{value.year}년 {value.month}월 {value.day}일({weekday})"


def _format_period(start: date | None, end: date | None) -> str:
    if start and end and start != end:
        return f"{_format_date(start)} ~ {_format_date(end)}"
    return _format_date(start or end) if (start or end) else "날짜 미정"


# ---------------------------------------------------------------- 상대 날짜

@dataclass(frozen=True)
class DateWindow:
    """질문이 가리키는 기간과 사람이 읽을 라벨."""
    start: date
    end: date
    label: str


# 상대 날짜 표현. 한자어 축약형(금일·작일·명일·금주·금월)은 다른 단어의 일부로
# 들어가기 쉬워(예: 시'작일', 지'금일'정, 설'명일') 앞에 한글이 붙지 않을 때만 인정한다.
def _standalone(word: str) -> str:
    return rf"(?<![가-힣]){word}"


_RELATIVE_DAY_RULES: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(rf"오늘|{_standalone('금일')}"), 0, "오늘"),
    (re.compile(rf"내일|{_standalone('명일')}"), 1, "내일"),
    (re.compile(rf"어제|{_standalone('작일')}"), -1, "어제"),
    (re.compile(r"모레"), 2, "모레"),
]

_RELATIVE_WEEK_RULES: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(rf"이번주|{_standalone('금주')}"), 0, "이번 주"),
    (re.compile(rf"다음주|{_standalone('차주')}"), 1, "다음 주"),
    (re.compile(r"지난주|저번주"), -1, "지난주"),
]

_RELATIVE_MONTH_RULES: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(rf"이번달|{_standalone('금월')}"), 0, "이번 달"),
    (re.compile(rf"다음달|{_standalone('내달')}"), 1, "다음 달"),
]


def _shift_month(anchor: date, months: int) -> date:
    """해당 월 1일을 기준으로 months만큼 이동한다."""
    total = (anchor.year * 12 + anchor.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def resolve_relative_window(query: str, today: date) -> DateWindow | None:
    """질문 속 상대 날짜 표현을 실제 기간으로 바꾼다.

    '이번 주'는 월요일~일요일로 잡는다 — 사용자가 주 중간에 물어도
    같은 주의 지난 요일 일정까지 보여야 "이번 주 일정"이 된다.
    """
    text = query.replace(" ", "")

    for pattern, offset, label in _RELATIVE_DAY_RULES:
        if pattern.search(text):
            target = today + timedelta(days=offset)
            return DateWindow(target, target, label)

    monday = today - timedelta(days=today.weekday())
    for pattern, week_offset, label in _RELATIVE_WEEK_RULES:
        if pattern.search(text):
            start = monday + timedelta(weeks=week_offset)
            return DateWindow(start, start + timedelta(days=6), label)

    for pattern, month_offset, label in _RELATIVE_MONTH_RULES:
        if pattern.search(text):
            start = _shift_month(today, month_offset)
            return DateWindow(start, _shift_month(start, 1) - timedelta(days=1), label)

    return None


# ---------------------------------------------------------------- 학사일정

# "언제/며칠/기간" 처럼 날짜를 묻는 신호. 이 신호가 없으면 시점 질문이 아니다.
_WHEN_PATTERN = re.compile(r"언제|며칠|몇월|날짜|일정|기간|시작|끝|마감|종료")

# 학사일정 표에서 찾을 대표 사건. 질문 표현 → 표 검색어.
#
# 순서가 중요하다: 구체적인 규칙이 먼저 와야 한다.
# "장바구니"가 "수강신청"보다 뒤에 있으면 "수강신청 장바구니 기간"이 일반 수강신청으로
# 잡혀 엉뚱한 일정(대학원 수강신청)을 답하게 된다.
_SCHEDULE_EVENT_TERMS: list[tuple[re.Pattern[str], tuple[str, ...]]] = [
    (re.compile(r"장바구니|희망강의|수강꾸러미"), ("희망강의", "장바구니")),
    (re.compile(r"중간고사|중간\s*시험"), ("중간고사", "중간시험")),
    (re.compile(r"기말고사|기말\s*시험"), ("기말고사", "기말시험")),
    (re.compile(r"개강|수업\s*시작|학기\s*시작"), ("개강",)),
    (re.compile(r"종강|수업\s*종료|학기\s*종료"), ("종강",)),
    # "수강신청 정정"은 수강신청이 아니라 정정 일정을 묻는 말이다. 아래 수강신청 규칙보다
    # 먼저 둬야 8월 수강신청(8/3~8/7)이 아닌 9월 정정 기간(9/1~9/7)이 잡힌다.
    (re.compile(r"수강\s*신?청?\s*정정"), ("정정",)),
    (re.compile(r"수강\s*취소|철회"), ("취소", "철회")),
    (re.compile(r"수강신청|수강\s*신청"), ("수강신청",)),
    (re.compile(r"성적\s*정정|성적\s*입력|성적\s*공시"), ("성적",)),
    (re.compile(r"시험"), ("시험",)),
    (re.compile(r"방학"), ("방학",)),
    (re.compile(r"계절학기|계절\s*수업"), ("계절",)),
    (re.compile(r"등록금|등록\s*기간|납부"), ("등록",)),
    (re.compile(r"휴학|복학"), ("휴학", "복학")),
    (re.compile(r"졸업식|학위수여"), ("졸업", "학위수여")),
    (re.compile(r"정정"), ("정정",)),
]

_FUTURE_PUBLICATION_DETAIL_RE = re.compile(
    r"언제|기간|일정|마감|시작|종료|모집|접수|신청|지원|선발|공고"
)
_FUTURE_DATE_DETAIL_RE = re.compile(r"언제|기간|일정|마감|시작|종료")
_CONCRETE_DATE_RE = re.compile(
    r"(?:20\d{2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2}|"
    r"\d{1,2}\s*월\s*\d{1,2}\s*일|"
    r"(?<!\d)\d{1,2}\s*[./]\s*\d{1,2}(?!\d))"
)
_FUTURE_PUBLICATION_SUBJECTS = (
    "교환학생",
    "장학",
    "공모전",
    "수강",
    "휴학",
    "복학",
    "등록금",
    "전과",
    "복수전공",
    "다전공",
    "계절학기",
    "기숙사",
    "생활관",
    "인턴",
    "봉사",
    "채용",
    "프로그램",
)

_NOTICE_APPLICATION_LABEL_RE = re.compile(
    r"지원서\s*접수|지원\s*기간|신청\s*기간|접수\s*기간|모집\s*기간"
)
_NOTICE_PERIOD_RE = re.compile(
    r"(?P<start_year>20\d{2})\s*[./년-]\s*"
    r"(?P<start_month>\d{1,2})\s*[./월-]\s*"
    r"(?P<start_day>\d{1,2})\s*일?"
    r"[^~∼～–—\n]{0,24}[~∼～–—]\s*"
    r"(?:(?P<end_year>20\d{2})\s*[./년-]\s*)?"
    r"(?P<end_month>\d{1,2})\s*[./월-]\s*"
    r"(?P<end_day>\d{1,2})\s*일?"
)

# 일반 키워드로 검색했을 때 학생이 실제로 궁금해할 일정을 앞으로 올린다.
# 예: "시험 기간" → 학부 중간·기말시험이지, 대학원 종합시험 접수가 아니다.
_PREFERRED_TITLE_TERMS: dict[str, tuple[str, ...]] = {
    "시험": ("중간시험", "기말시험", "중간고사", "기말고사"),
}


def _preference_rank(row_title: str, terms: tuple[str, ...]) -> int:
    """선호 제목이면 0, 아니면 1. 정렬 시 앞자리 키로 쓴다."""
    for term in terms:
        preferred = _PREFERRED_TITLE_TERMS.get(term)
        if not preferred:
            continue
        if any(p in row_title for p in preferred):
            return 0
    return 1


def extract_schedule_event_terms(query: str) -> tuple[str, ...]:
    """질문에서 찾을 학사일정 사건 키워드를 뽑는다. 없으면 빈 튜플."""
    for pattern, terms in _SCHEDULE_EVENT_TERMS:
        if pattern.search(query):
            return terms
    return ()


def is_schedule_when_question(query: str) -> bool:
    """'개강 언제야'처럼 학사일정의 날짜를 묻는 질문인가."""
    if not _WHEN_PATTERN.search(query):
        return False
    return bool(extract_schedule_event_terms(query))


def is_schedule_direct_question(query: str, today: date) -> bool:
    """학사일정 행 조회만으로 답할 수 있는 사건/기간 질문인가."""
    if is_schedule_when_question(query):
        return True
    return "학사일정" in _compact(query) and resolve_relative_window(query, today) is not None


def future_publication_years(query: str, today: date) -> tuple[int, ...]:
    """공고·일정이 아직 생기지 않았을 수 있는 명시적 미래 학년도를 찾는다."""
    years = sorted(
        {
            int(value)
            for value in re.findall(r"(?<!\d)(20\d{2})(?!\d)", query)
            if int(value) > today.year
        }
    )
    if not years or not _FUTURE_PUBLICATION_DETAIL_RE.search(query):
        return ()
    if not any(subject in _compact(query) for subject in _FUTURE_PUBLICATION_SUBJECTS):
        return ()
    return tuple(years)


def answer_future_unannounced(
    query: str,
    corpus_texts: Iterable[str],
    today: date,
) -> DirectAnswer | None:
    """보유 공식 자료에 해당 미래 사실이 없으면 명시적인 미공고 답을 만든다.

    단순히 미래 연도라는 이유로 막지 않는다. 같은 연도와 질문의 핵심 주제가
    함께 나타나는 일정·공지가 하나라도 있으면 일반 정형 조회/RAG가 답하도록
    그대로 통과시킨다.
    """
    years = future_publication_years(query, today)
    if not years:
        return None
    compact_query = _compact(query)
    subjects = tuple(
        subject for subject in _FUTURE_PUBLICATION_SUBJECTS if subject in compact_query
    )
    corpus_entries = tuple(
        (
            _compact(str(text).splitlines()[0]),
            _compact(str(text)),
        )
        for text in corpus_texts
        if str(text or "").strip()
    )
    related_texts: list[str] = []
    for year in years:
        year_text = str(year)
        related_texts.extend(
            full_text
            for title, full_text in corpus_entries
            if year_text in title and any(subject in title for subject in subjects)
        )

    asks_for_date_detail = bool(_FUTURE_DATE_DETAIL_RE.search(query))
    if related_texts and (
        not asks_for_date_detail
        or any(_CONCRETE_DATE_RE.search(text) for text in related_texts)
    ):
        return None

    year_label = ", ".join(f"{year}학년도" for year in years)
    availability = (
        "관련 안내는 확인되지만 질문하신 정확한 기간은"
        if related_texts
        else "관련 일정·모집 정보는"
    )
    return DirectAnswer(
        answer=(
            f"{year_label} {availability} {today.isoformat()} 기준으로 "
            "보유한 동국대학교 공식 자료에서 아직 공고가 확인되지 않았습니다. "
            "공식 공지가 게시된 뒤 정확한 기간을 안내할 수 있습니다."
        ),
        sources=[],
        kind="future_unannounced",
    )


def _notice_application_period(text: str) -> tuple[date, date, str | None, str] | None:
    """라벨이 붙은 지원/신청 기간 한 줄만 파싱한다.

    이미지 전사 전체에서 임의의 두 날짜를 고르면 설명회나 합격자 발표를 지원
    기간으로 오인할 수 있다. 따라서 라벨이 있는 같은 줄 안의 범위만 인정한다.
    """
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        label_match = _NOTICE_APPLICATION_LABEL_RE.search(line)
        if not label_match:
            continue
        period_match = _NOTICE_PERIOD_RE.search(line, label_match.end())
        if not period_match:
            continue
        try:
            start = date(
                int(period_match.group("start_year")),
                int(period_match.group("start_month")),
                int(period_match.group("start_day")),
            )
            end = date(
                int(period_match.group("end_year") or period_match.group("start_year")),
                int(period_match.group("end_month")),
                int(period_match.group("end_day")),
            )
        except ValueError:
            continue
        if end < start:
            continue
        tail = line[period_match.end(): period_match.end() + 32]
        time_match = re.search(r"(?<!\d)(\d{1,2})\s*시", tail)
        end_time = f"{int(time_match.group(1))}시" if time_match else None
        return start, end, end_time, line
    return None


def answer_future_notice_period(
    query: str,
    rows: Iterable[NoticePeriodRow],
    today: date,
) -> DirectAnswer | None:
    """명시적 미래 질문을 공식 공지의 라벨 있는 신청 기간으로 직접 답한다."""
    years = future_publication_years(query, today)
    if not years or not _FUTURE_DATE_DETAIL_RE.search(query):
        return None
    compact_query = _compact(query)
    subjects = tuple(
        subject for subject in _FUTURE_PUBLICATION_SUBJECTS if subject in compact_query
    )
    candidates: list[tuple[NoticePeriodRow, tuple[date, date, str | None, str]]] = []
    for row in rows:
        compact_title = _compact(row.title)
        if not any(str(year) in compact_title for year in years):
            continue
        if subjects and not any(subject in compact_title for subject in subjects):
            continue
        period = _notice_application_period(row.content)
        if period is not None:
            candidates.append((row, period))
    if not candidates:
        return None

    candidates.sort(key=lambda item: str(item[0].published_at or ""), reverse=True)
    row, (start, end, end_time, evidence_line) = candidates[0]
    subject_label = subjects[0] if subjects else "해당 공지"
    end_label = f"{end.isoformat()} {end_time}" if end_time else end.isoformat()
    caveat = (
        " 공지에 예정 일정으로 안내되어 있어 변경될 수 있습니다."
        if re.search(r"예정|변경될\s*수", row.content)
        else ""
    )
    stable_id = row.notice_id or hashlib.sha256(
        f"{row.title}\n{row.published_at or ''}\n{evidence_line}".encode("utf-8")
    ).hexdigest()[:16]
    return DirectAnswer(
        answer=(
            f"「{row.title}」에 따르면 {subject_label} 지원서 접수 기간은 "
            f"{start.isoformat()}부터 {end_label}까지입니다.{caveat}"
        ),
        sources=[
            {
                "source": "notices",
                "title": row.title,
                "published_at": row.published_at,
                "snippet": evidence_line,
                "url": row.url,
                "chunk_id": f"notice:{stable_id}:application-period",
                "metadata": {
                    "source_type": "notice",
                    "notice_id": str(stable_id),
                    "board": row.board or None,
                    "category": row.category or None,
                    "apply_deadline": end.isoformat(),
                },
            }
        ],
        kind="future_notice_period",
    )


@dataclass
class ScheduleRow:
    """학사일정 한 줄. 데이터프레임/DB 어느 쪽에서 와도 이 형태로 좁혀서 넘긴다."""
    title: str
    start: date | None
    end: date | None
    category: str = ""
    row_id: str | int | None = None
    department: str = ""
    url: str | None = None


def _compact(value: str) -> str:
    return compact_text(value)


def _schedule_audience(value: str) -> str:
    return derive_audience(value)


def _query_audience(query: str, terms: tuple[str, ...]) -> str:
    del terms  # 공통 질의 해석기가 사건 키워드까지 포함해 처리한다.
    return query_audience(query)


def _requested_academic_period(query: str) -> tuple[int | None, int | None]:
    year_match = re.search(r"(?<!\d)(20\d{2})(?!\d)", query)
    short_match = re.search(r"(?<!\d)(\d{2})\s*[-./]\s*([12])(?!\d)", query)
    semester_match = short_match or re.search(r"([12])\s*학기", query)
    year = int(year_match.group(1)) if year_match else None
    if year is None and short_match:
        year = 2000 + int(short_match.group(1))
    semester = int(semester_match.group(2 if short_match else 1)) if semester_match else None
    return year, semester


def _row_matches_academic_period(
    row: ScheduleRow,
    requested_year: int | None,
    requested_semester: int | None,
) -> bool:
    compact = _compact(row.title)
    row_year = re.search(r"(?<!\d)(20\d{2})(?!\d)", compact)
    row_semester = re.search(r"([12])학기", compact)
    if requested_year is not None and row_year is not None and int(row_year.group(1)) != requested_year:
        return False
    if requested_semester is not None and row_semester is not None and int(row_semester.group(1)) != requested_semester:
        return False
    return True


def _audience_rank(row: ScheduleRow, requested: str) -> int:
    audience = _schedule_audience(f"{row.title} {row.category} {row.department}")
    if audience == requested:
        return 0
    if audience == "common":
        return 1
    return 2


def _schedule_source(row: ScheduleRow) -> dict:
    start = row.start.isoformat() if row.start else None
    end = row.end.isoformat() if row.end else None
    stable_id = row.row_id or f"{_compact(row.title)}:{start or ''}:{end or ''}"
    return {
        "source": "schedule",
        "title": row.title,
        "published_at": start,
        "snippet": f"{row.title} · {_format_period(row.start, row.end)}",
        "url": row.url,
        "chunk_id": f"schedule:{stable_id}",
        "metadata": {
            "source_type": "schedule",
            "schedule_id": str(stable_id),
            "schedule_start": start,
            "schedule_end": end,
            "department": row.department or None,
            "audience": _schedule_audience(f"{row.title} {row.category} {row.department}"),
            "campus_scope": "shared",
        },
    }


def answer_schedule_when(
    query: str,
    rows: list[ScheduleRow],
    today: date,
    limit: int = 5,
) -> DirectAnswer | None:
    """학사일정 표에서 질문에 해당하는 일정을 찾아 날짜로 답한다.

    기간이 명시된 질문("이번 주 학사일정")은 그 기간과 겹치는 일정을,
    사건만 물은 질문("개강 언제야")은 오늘 이후 가장 가까운 일정을 우선 제시한다.
    둘 다 비면 가장 가까운 과거/미래 일정을 안내해 빈손으로 끝나지 않게 한다.
    """
    window = resolve_relative_window(query, today)
    terms = extract_schedule_event_terms(query)
    if not window and not terms:
        return None

    candidates = [row for row in rows if row.start or row.end]
    if terms:
        normalized = [(row, _compact(f"{row.title} {row.category}")) for row in candidates]
        candidates = [
            row
            for row, text in normalized
            if any(_compact(term) in text for term in terms)
        ]

    requested_year, requested_semester = _requested_academic_period(query)
    candidates = [
        row
        for row in candidates
        if _row_matches_academic_period(row, requested_year, requested_semester)
    ]

    requested_audience = _query_audience(query, terms)
    if requested_audience != "common":
        candidates = [
            row
            for row in candidates
            if _schedule_audience(f"{row.title} {row.category} {row.department}")
            in {requested_audience, "common"}
        ]

    if not candidates:
        return None

    def overlaps(row: ScheduleRow, start: date, end: date) -> bool:
        row_start = row.start or row.end
        row_end = row.end or row.start
        return row_start is not None and row_end is not None and row_start <= end and row_end >= start

    if window:
        matched = [row for row in candidates if overlaps(row, window.start, window.end)]
        if matched:
            matched.sort(
                key=lambda row: (
                    _audience_rank(row, requested_audience),
                    row.start or row.end or today,
                )
            )
            lines = [f"- {row.title}: {_format_period(row.start, row.end)}" for row in matched[:limit]]
            more = f"\n\n이 밖에 {len(matched) - limit}건이 더 있습니다." if len(matched) > limit else ""
            return DirectAnswer(
                answer=f"{window.label}({_format_period(window.start, window.end)}) 학사일정입니다.\n\n"
                       + "\n".join(lines) + more,
                sources=[_schedule_source(row) for row in matched[:limit]],
                kind="schedule_window",
            )

        # 기간 내에 없으면 '없다'고만 하지 않고 가장 가까운 일정을 함께 알려준다.
        upcoming = sorted(
            (row for row in candidates if (row.end or row.start) and (row.end or row.start) >= today),
            key=lambda row: (
                _audience_rank(row, requested_audience),
                row.start or row.end,
            ),
        )
        if upcoming:
            nearest = upcoming[0]
            return DirectAnswer(
                answer=f"{window.label}({_format_period(window.start, window.end)})에 해당하는 학사일정은 없습니다.\n\n"
                       f"가장 가까운 일정은 {nearest.title}({_format_period(nearest.start, nearest.end)})입니다.",
                sources=[_schedule_source(nearest)],
                kind="schedule_nearest",
            )
        return None

    # 사건만 물은 경우 — 오늘 이후 가장 가까운 것.
    # 단, 일반 키워드('시험')는 학생이 궁금해할 일정(중간·기말)을 먼저 본다.
    upcoming = sorted(
        (row for row in candidates if (row.end or row.start) and (row.end or row.start) >= today),
        key=lambda row: (
            _audience_rank(row, requested_audience),
            _preference_rank(row.title, terms),
            row.start or row.end,
        ),
    )
    if upcoming:
        target = upcoming[0]
        rest = upcoming[1:limit]
        extra = "".join(f"\n- {row.title}: {_format_period(row.start, row.end)}" for row in rest)
        return DirectAnswer(
            answer=f"{target.title}은(는) {_format_period(target.start, target.end)}입니다."
                   + (f"\n\n관련 일정도 함께 안내합니다.{extra}" if extra else ""),
            sources=[_schedule_source(row) for row in upcoming[:limit]],
            kind="schedule_event",
        )

    # 전부 지난 일정이면 마지막 것을 근거와 함께 밝힌다.
    past = sorted(
        (row for row in candidates if (row.end or row.start)),
        key=lambda row: (row.end or row.start),
    )
    if past:
        target = past[-1]
        return DirectAnswer(
            answer=f"앞으로 예정된 일정은 찾지 못했습니다. "
                   f"가장 최근 기록은 {target.title}({_format_period(target.start, target.end)})입니다.",
            sources=[_schedule_source(target)],
            kind="schedule_past",
        )
    return None


# ---------------------------------------------------------------- 학식

_MEAL_PATTERN = re.compile(r"학식|식단|메뉴|학생식당|상록원|솥앤|누들|누리터|가든쿡|팔정도|디플렉스|d-?flex|중식|석식|조식")
_PRICE_PATTERN = re.compile(r"가격|얼마|값|요금|비용")
_MEAL_DIRECT_PATTERN = re.compile(
    r"오늘|내일|어제|모레|이번\s*주|다음\s*주|이번\s*달|"
    r"식단|메뉴|가격|얼마|중식|석식|조식|뭐\s*(?:나와|야|먹)"
)

# 식당 별칭 → CSV의 restaurant 표기에 들어 있는 부분 문자열
_RESTAURANT_ALIASES: dict[str, tuple[str, ...]] = {
    "상록원": ("상록원",),
    "솥앤누들": ("솥앤", "누들"),
    "누리터": ("누리터",),
    "가든쿡": ("가든쿡", "가든"),
    "팔정도": ("팔정도",),
    "경영관": ("경영관", "D-Flex", "디플렉스"),
}


def is_meal_question(query: str) -> bool:
    return bool(_MEAL_PATTERN.search(query))


def is_meal_direct_question(query: str) -> bool:
    """식단 표의 날짜·메뉴·가격 행으로 완결해서 답할 수 있는 질문인가."""
    return is_meal_question(query) and bool(_MEAL_DIRECT_PATTERN.search(query))


def extract_restaurant_hint(query: str) -> tuple[str, ...]:
    """질문에 특정 식당이 언급되면 그 식당의 표기 후보를 돌려준다."""
    compact = query.replace(" ", "").lower()
    for canonical, needles in _RESTAURANT_ALIASES.items():
        if canonical.lower() in compact or any(n.lower().replace("-", "") in compact.replace("-", "") for n in needles):
            return needles
    return ()


@dataclass
class MealRow:
    """학식 한 줄(식당·날짜·원문 메뉴 텍스트)."""
    date: date
    restaurant: str
    menu_text: str
    is_closed: bool = False
    source_url: str | None = None


def _meal_source(row: MealRow) -> dict:
    effective_date = row.date.isoformat()
    return {
        "source": "meals",
        "title": f"{row.restaurant} {_format_date(row.date)} 식단",
        "published_at": effective_date,
        "snippet": row.menu_text[:200],
        "url": row.source_url,
        "chunk_id": f"meals:{effective_date}:{_compact(row.restaurant)}",
        "metadata": {
            "source_type": "meals",
            "restaurant": row.restaurant,
            "meal_date": effective_date,
            "effective_date": effective_date,
            "campus_scope": "shared",
        },
    }


def answer_meal(
    query: str,
    rows: list[MealRow],
    today: date,
    corner_splitter,
) -> DirectAnswer | None:
    """오늘/내일 등 지정한 날짜의 식단을 표에서 직접 찾아 답한다.

    corner_splitter는 원문 메뉴 텍스트를 코너 단위로 쪼개는 함수를 주입받는다
    (브리핑에서 쓰는 것과 같은 로직을 공유해 표기가 어긋나지 않게 한다).
    """
    if not is_meal_question(query):
        return None

    window = resolve_relative_window(query, today)
    target_date = window.start if window else today
    label = window.label if window else "오늘"

    hint = extract_restaurant_hint(query)
    same_day = [row for row in rows if row.date == target_date]
    if hint:
        same_day = [row for row in same_day if any(n.lower() in row.restaurant.lower() for n in hint)]

    open_rows = [row for row in same_day if not row.is_closed and row.menu_text.strip()]
    if not open_rows:
        if same_day:
            names = ", ".join(sorted({row.restaurant for row in same_day}))
            message = f"{label}({_format_date(target_date)})은 {names} 모두 운영하지 않습니다."

            # 막힌 답으로 끝내지 않고, 다음으로 문을 여는 날을 함께 알려준다
            # (방학 중에는 며칠씩 연달아 휴무라 "언제 열지"가 실제 궁금증이다).
            future_open = sorted(
                (row for row in rows
                 if row.date > target_date and not row.is_closed and row.menu_text.strip()),
                key=lambda row: row.date,
            )
            if future_open:
                next_day = future_open[0].date
                next_names = ", ".join(sorted({
                    row.restaurant for row in future_open if row.date == next_day
                }))
                message += f"\n\n다음 운영일은 {_format_date(next_day)}이며, {next_names}이 문을 엽니다."

            return DirectAnswer(
                answer=message,
                sources=[_meal_source(row) for row in same_day],
                kind="meal_closed",
            )
        return None

    wants_price = bool(_PRICE_PATTERN.search(query))
    blocks: list[str] = []
    sources: list[dict] = []
    for row in open_rows:
        corners = corner_splitter(row.menu_text, 6)
        if not corners:
            continue
        lines = []
        for corner in corners:
            # 가격은 코너 표기 안에 함께 들어 있다(예: "중식 A코너 6,500원").
            lines.append(f"  · {corner['corner']}: {corner['menu']}")
        blocks.append(f"**{row.restaurant}**\n" + "\n".join(lines))
        sources.append(_meal_source(row))

    if not blocks:
        return None

    header = f"{label}({_format_date(target_date)}) 학식입니다."
    if wants_price:
        header += " 가격은 코너 이름에 함께 표기되어 있습니다."
    return DirectAnswer(answer=header + "\n\n" + "\n\n".join(blocks), sources=sources, kind="meal")


__all__ = [
    "DateWindow",
    "DirectAnswer",
    "MealRow",
    "NoticePeriodRow",
    "ScheduleRow",
    "parse_flexible_date",
    "answer_meal",
    "answer_schedule_when",
    "answer_future_notice_period",
    "answer_future_unannounced",
    "extract_restaurant_hint",
    "extract_schedule_event_terms",
    "future_publication_years",
    "is_meal_question",
    "is_meal_direct_question",
    "is_schedule_when_question",
    "is_schedule_direct_question",
    "resolve_relative_window",
]
