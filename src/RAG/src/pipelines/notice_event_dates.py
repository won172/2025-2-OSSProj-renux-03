"""공지 제목·본문에서 신청·접수 기간(시작일·마감일)과 근거 문자열을 뽑는다.

``apply_deadline``(채팅 검색의 마감 필터, 알림 후보의 기준일)과 알림·캘린더 후보의
``start_date``/``evidence``/``date_status``가 같은 판단을 공유하도록 한 곳에 둔다.

원칙:
- 날짜는 공지 원문에 적힌 문자열에서만 만든다. 추측하지 않는다.
- ``end_date``는 기존 ``apply_deadline`` 규칙(제목 마감 표기 → 본문 신청·접수 라벨 주변
  날짜)을 그대로 따른다. 검색 필터가 이 값을 쓰므로 규칙을 바꿀 때는 말뭉치 전체의
  변경분을 확인한다.
- ``date_status="confirmed"``는 근거 문자열 하나로 기간이 한 가지로 정해질 때만 준다.
  회차가 여러 개이거나, 연도를 추정해야 하는데 애매하거나, 제목과 본문 마감이 서로
  다르거나, 시작만 있고 끝이 없으면 ``"unverified"``다. unverified 날짜는 참고값이다.
- ``evidence``는 제목 또는 본문의 정확한 부분 문자열이다. 둘 다 근거이면 " || "로 잇는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import List, Literal, Optional, Tuple

import pandas as pd

DateStatus = Literal["confirmed", "unverified"]
EVIDENCE_DELIMITER = " || "


@dataclass(frozen=True)
class NoticeEventDates:
    """공지 하나의 신청·접수 기간.

    ``end_date``는 ``apply_deadline``과 같은 값이다. ``reasons``는 unverified 사유다.
    """

    start_date: Optional[str]
    end_date: str
    evidence: str
    date_status: DateStatus
    source: Literal["title", "body", "title+body"]
    reasons: Tuple[str, ...] = ()


# --------------------------------------------------------------------------
# 패턴
# --------------------------------------------------------------------------

_RANGE_SEP = r"(?:~|-|–|—|∼|〜|\\sim)"
# 전화번호("02-2260-3699")의 앞부분을 월-일로 읽지 않는다.
_NOT_PHONE = r"(?!\d)(?!\s*-\s*\d)"

_TITLE_DEADLINE_PATTERNS = (
    # (~9/20), (마감 6월 21일), (4/1~7/31)
    re.compile(
        r"(?:~|마감|기한|까지)\s*"
        r"(?:(?P<year>\d{4})\s*[.\-/년]\s*)?"
        r"(?P<month>\d{1,2})\s*(?:[.\-/월])\s*"
        r"(?P<day>\d{1,2})\s*일?"
    ),
    # (2026.08.06까지), (10월 12일 마감), (12.22까지)
    re.compile(
        r"(?:(?P<year>\d{4})\s*[.\-/년]\s*)?"
        r"(?P<month>\d{1,2})\s*(?:[.\-/월])\s*"
        r"(?P<day>\d{1,2})\s*일?\s*"
        r"(?:까지|마감|기한)"
    ),
)
_BODY_DEADLINE_KEYWORD_PATTERN = re.compile(
    r"("
    r"지원서\s*접수|지원\s*기간|지원\s*마감|모집\s*기간|모집\s*마감|"
    r"신청\s*기간|신청기간|접수\s*기간|접수기간|"
    r"제출\s*기간|제출기간|서류\s*제출|서류제출|"
    r"등록\s*기간|등록기간|납부\s*기간|납부기간|"
    r"수강\s*신청|수강신청|수강\s*취소|수강취소|"
    r"신청\s*마감|접수\s*마감|제출\s*마감|마감\s*일|마감일|"
    r"신청\s*기한|접수\s*기한|제출\s*기한|기한"
    r")"
)
# "기간"·"기한" 단어 없이 콜론 뒤에 날짜가 바로 오는 라벨("신청: 2025. 6. 2.", "접수: ~ 5.3.").
# 기존 라벨에서 날짜를 못 찾았을 때만 쓴다(기존 마감일을 바꾸지 않기 위해).
_BODY_COLON_LABEL_PATTERN = re.compile(
    r"(?<![가-힣])(?:신청|접수|모집|지원)\s*[:：]"
    r"(?=\s*(?:~\s*)?(?:\d{4}\s*[.\-/년]\s*\d{1,2}|\d{2}\.\s?\d{1,2}\.\s?\d{1,2}|\d{1,2}\s*[.\-/월]\s*\d{1,2}))"
)
_BODY_FULL_DATE_END_PATTERN = re.compile(
    _RANGE_SEP + r"\s*"
    r"(?P<year>\d{4})\s*[.\-/년]\s*"
    r"(?P<month>\d{1,2})\s*[.\-/월]\s*"
    r"(?P<day>\d{1,2})"
)
_BODY_MONTH_DAY_END_PATTERN = re.compile(
    _RANGE_SEP + r"\s*['’]?(?P<month>\d{1,2})\s*[.\-/월]\s*(?P<day>\d{1,2})(?!\d)"
)
# 두 자리 연도 끝 날짜: "~ 26.10.19.", "~'26.02.13." (월·일 패턴이 26월 10일로 읽어 버리던 표기)
_BODY_SHORT_YEAR_END_PATTERN = re.compile(
    _RANGE_SEP + r"\s*['’]?(?P<year>\d{2})\.\s?(?P<month>\d{1,2})\.\s?(?P<day>\d{1,2})(?!\d)"
)
# 연도 없이 점으로 쓴 월.일: "12.30(월)까지", "4. 13. (월)까지". 소수·버전 번호와 구분하려고
# 바로 뒤에 요일 괄호나 까지/마감이 올 때만 날짜로 본다. 다른 패턴이 모두 없을 때만 쓴다.
_BODY_MONTH_DAY_DOT_PATTERN = re.compile(
    r"(?<![\d.])(?P<month>\d{1,2})\s*\.\s*(?P<day>\d{1,2})(?!\d)\s*\.?"
    r"(?=\s*(?:\(\s*[월화수목금토일]|까지|마감))"
)
_BODY_FULL_DATE_SINGLE_PATTERN = re.compile(
    r"(?P<year>\d{4})\s*[.\-/년]\s*"
    r"(?P<month>\d{1,2})\s*[.\-/월]\s*"
    r"(?P<day>\d{1,2})"
)
_BODY_MONTH_DAY_SINGLE_PATTERN = re.compile(
    r"(?P<month>\d{1,2})\s*[\-/월]\s*(?P<day>\d{1,2})" + _NOT_PHONE
)

_WEEKDAY = r"(?:\s*\(\s*[월화수목금토일](?:요일)?\s*\)|\s*[월화수목금토일]요일)?"
_CLOCK = r"(?:\s*(?:오전|오후)?\s*\d{1,2}\s*(?::\s*\d{2}|시(?:\s*\d{1,2}\s*분)?))?"
# 날짜 뒤 장식: ".", "일", "(월)", "(월요일)", "10:00", "오후 5시", "17시 30분"
_DECORATION = re.compile(r"(?:\s*[.일])?" + _WEEKDAY + _CLOCK)
# 범위 끝에서 월이 생략된 일자: "~ 27. (금)", "~ 27일"
_DAY_ONLY_END = re.compile(
    r"\s*(?:~|∼|〜)\s*(?P<day>\d{1,2})\s*(?:일|\.)(?!\s*\d)"
)
# 날짜 뒤에 범위 기호만 있고 끝 날짜가 없음: "2026. 1. 5. (월)10:00 ~"
_UNTIL = re.compile(r"\s*(?:까지|마감)")
_OPEN_RANGE = re.compile(r"\s*(?:~|∼|〜|-|–|부터)\s*(?![\d\s])")
_START_TOKEN_BEFORE = re.compile(
    r"(?:(?P<year>\d{4})\s*[.\-/년]\s*)?"
    r"(?<!\d)(?P<month>\d{1,2})\s*[.\-/월]\s*(?P<day>\d{1,2})"
    r"(?:\s*[.일])?" + _WEEKDAY + _CLOCK + r"\s*$"
)
_SESSION_MARKER = re.compile(r"\d\s*차(?![가-힣])|회차|차수|[①②③④⑤]")

_BODY_WINDOW = 180
# 첫 라벨 뒤에 날짜가 없으면(예: 제목형 문장 "신청 및 서류 제출 안내") 다음 라벨을 본다.
_SCAN_ALL_LABELS = True
_USE_COLON_LABELS = True
# 라벨 뒤 첫 날짜가 있는 줄 안에서만 마감일을 찾는다.
_NEAREST_DATE_LINE = True
# 연도를 게시일로 추정한 날짜가 게시일보다 이만큼 늦으면 연도를 확신하지 않는다.
_INFERRED_MAX_AFTER_DAYS = 240
# 게시일보다 이른 날짜를 이듬해로 넘겼는데 원래 날짜가 게시일과 이만큼 가까우면
# "이미 지난 기간"을 잘못 넘겼을 가능성이 커서 확신하지 않는다.
_ROLLED_MIN_GAP_DAYS = 180
# 연도가 적힌 날짜라도 게시일과 이만큼 떨어지면 확신하지 않는다.
_EXPLICIT_MAX_DISTANCE_DAYS = 550
# 연도가 적힌 마감일이 게시일보다 이만큼 이르면 확신하지 않는다.
_EXPLICIT_MAX_BEFORE_PUBLISHED_DAYS = 31
# 첫 라벨이 아닌 라벨(추가로 훑은 라벨)은 날짜가 라벨 바로 뒤에 있어야 한다.
_EXTRA_LABEL_MAX_GAP = 30
# 라벨과 날짜 사이가 이보다 멀면 그 라벨의 날짜인지 확신하지 않는다.
_CONFIDENT_LABEL_GAP = 60
# 라벨과 날짜 사이에 다른 항목 라벨이 끼어 있으면(표 머리글 등) 그 라벨의 날짜가 아닐 수 있다.
_OTHER_PERIOD_LABEL = re.compile(r"[가-힣]+\s*기\s*간|일\s*시|일\s*정|일\s*자|날\s*짜")
# 앞말 없이 쓰인 "기간:"/"기한:"은 바로 앞 신청·접수 라벨의 이어짐으로 본다("수강 신청 · 기간: 9.18").
_GENERIC_PERIOD_LABEL = re.compile(r"(?<![가-힣])기\s*[간한](?:\s*[:：])?")


def _parse_date_parts(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _published_day(published_date: object) -> Optional[date]:
    if not published_date:
        return None
    ts = pd.to_datetime(published_date, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date()


@dataclass(frozen=True)
class _Found:
    start: Optional[date]
    end: date
    span: Tuple[int, int]  # 원문 기준 근거 구간(라벨 제외)
    reasons: Tuple[str, ...]


def _roll_inferred(value: date, published: date) -> Tuple[Optional[date], Tuple[str, ...]]:
    """연도를 게시일로 추정한 날짜를 기존 규칙대로 보정하고, 애매하면 사유를 단다."""
    if value < published:
        rolled = _parse_date_parts(value.year + 1, value.month, value.day)
        if rolled is None:
            return None, ()
        if (published - value).days < _ROLLED_MIN_GAP_DAYS:
            return rolled, ("year_rolled_ambiguous",)
        return rolled, ()
    if (value - published).days > _INFERRED_MAX_AFTER_DAYS:
        return value, ("year_inferred_far_from_published",)
    return value, ()


def _explicit_reasons(value: date, published: Optional[date]) -> Tuple[str, ...]:
    if published is None:
        return ()
    if abs((value - published).days) > _EXPLICIT_MAX_DISTANCE_DAYS:
        return ("date_far_from_published",)
    if (published - value).days > _EXPLICIT_MAX_BEFORE_PUBLISHED_DAYS:
        # 게시일보다 한참 이른 마감: 연도 오기이거나 지난 회차 안내일 수 있다.
        return ("date_before_published",)
    return ()


def _start_before(text: str, sep_pos: int, floor: int, end: date) -> Tuple[Optional[date], int]:
    """범위 기호(``sep_pos``) 바로 앞의 시작 날짜와 그 시작 위치.

    연도가 없으면 끝 날짜의 연도를 따른다(시작이 끝보다 늦으면 전년도).
    """
    base = max(floor, sep_pos - 40)
    m = _START_TOKEN_BEFORE.search(text[base:sep_pos])
    if m is None:
        return None, sep_pos
    month, day = int(m.group("month")), int(m.group("day"))
    if m.group("year"):
        start = _parse_date_parts(int(m.group("year")), month, day)
    else:
        start = _parse_date_parts(end.year, month, day)
        if start is not None and start > end:
            start = _parse_date_parts(end.year - 1, month, day)
    if start is None or start > end:
        return None, sep_pos
    return start, base + m.start()


# --------------------------------------------------------------------------
# 제목
# --------------------------------------------------------------------------


def _title_deadline(title: str, published: Optional[date]) -> Optional[_Found]:
    if not title or published is None:
        return None
    match = next(
        (
            candidate
            for pattern in _TITLE_DEADLINE_PATTERNS
            if (candidate := pattern.search(title)) is not None
        ),
        None,
    )
    if not match:
        return None
    explicit_year = match.group("year") is not None
    deadline = _parse_date_parts(
        int(match.group("year") or published.year),
        int(match.group("month")),
        int(match.group("day")),
    )
    if deadline is None:
        return None
    reasons: Tuple[str, ...] = ()
    if deadline < published:
        # 기존 규칙: 게시일보다 이르면 이듬해로 본다(연도가 적혀 있어도 그렇게 했다).
        original = deadline
        deadline = _parse_date_parts(deadline.year + 1, deadline.month, deadline.day)
        if deadline is None:
            return None
        if explicit_year:
            reasons = ("explicit_year_before_published",)
        elif (published - original).days < _ROLLED_MIN_GAP_DAYS:
            reasons = ("year_rolled_ambiguous",)
    elif explicit_year:
        reasons = _explicit_reasons(deadline, published)
    elif (deadline - published).days > _INFERRED_MAX_AFTER_DAYS:
        reasons = ("year_inferred_far_from_published",)

    span_start = match.start()
    start: Optional[date] = None
    if title[match.start()] == "~":
        start, start_pos = _start_before(title, match.start(), 0, deadline)
        if start is not None:
            span_start = start_pos
    return _Found(start, deadline, (span_start, match.end()), reasons)


# --------------------------------------------------------------------------
# 본문
# --------------------------------------------------------------------------


_ANY_DATE_TOKEN = re.compile(r"(?<!\d)\d{1,4}\s*[.\-/년월]\s*\d{1,2}(?!\d)")
_LINE_CONTINUES = re.compile(r"(?:~|-|–|—|∼|〜|\\sim|[.년월])\s*$")
_LINE_CONTINUED = re.compile(r"^\s*(?:~|-|–|—|∼|〜|\\sim|\d{1,2}\s*[.월일])")


def _cut_after_first_date_line(window: str) -> str:
    """첫 날짜가 있는 줄까지만 남긴다(날짜가 다음 줄로 이어지면 그 줄도 포함).

    기존 규칙은 창 안 어디든 ``~ YYYY.MM.DD``를 먼저 골라, 라벨 바로 뒤 기간 대신 다음 항목
    ("활동기간: …~ 2024년 2월 8일")의 날짜를 마감일로 쓰는 경우가 있었다.
    """
    first = _ANY_DATE_TOKEN.search(window)
    if first is None:
        return window
    pos = first.end()
    while True:
        newline = window.find("\n", pos)
        if newline < 0:
            return window
        before, after = window[:newline], window[newline + 1 :]
        if _LINE_CONTINUES.search(before) or _LINE_CONTINUED.match(after):
            pos = newline + 1
            continue
        return before


def _parse_window(content: str, window_start: int, published: Optional[date]) -> Optional[_Found]:
    """라벨 바로 뒤 ``_BODY_WINDOW``자에서 기존 우선순위대로 마감일을 찾는다.

    우선순위(기존 규칙): ``~ YYYY.MM.DD`` → ``~ M.D``(두 자리 연도 포함) → ``YYYY.MM.DD`` → ``M/D``.
    """
    window = content[window_start : window_start + _BODY_WINDOW]
    if _NEAREST_DATE_LINE:
        window = _cut_after_first_date_line(window)

    full_match = _BODY_FULL_DATE_END_PATTERN.search(window)
    if full_match:
        end = _parse_date_parts(
            int(full_match.group("year")),
            int(full_match.group("month")),
            int(full_match.group("day")),
        )
        if end is None:
            return None
        sep = window_start + full_match.start()
        start, start_pos = _start_before(content, sep, window_start, end)
        return _Found(start, end, (start_pos, window_start + full_match.end()), _explicit_reasons(end, published))

    month_day_match = _BODY_MONTH_DAY_END_PATTERN.search(window)
    if month_day_match and published is not None:
        sep = window_start + month_day_match.start()
        short = _BODY_SHORT_YEAR_END_PATTERN.match(window, month_day_match.start())
        if short is not None and abs(2000 + int(short.group("year")) - published.year) <= 1:
            end = _parse_date_parts(
                2000 + int(short.group("year")),
                int(short.group("month")),
                int(short.group("day")),
            )
            if end is None:
                return None
            start, start_pos = _start_before(content, sep, window_start, end)
            return _Found(start, end, (start_pos, window_start + short.end()), _explicit_reasons(end, published))
        month, day = int(month_day_match.group("month")), int(month_day_match.group("day"))
        start_token = _START_TOKEN_BEFORE.search(content[max(window_start, sep - 40) : sep])
        explicit_end: Optional[date] = None
        if start_token is not None and start_token.group("year"):
            # "2025.06.02(월)~ 06.05": 끝 날짜의 연도는 시작일에 적힌 연도를 따른다.
            start_year = int(start_token.group("year"))
            start_md = (int(start_token.group("month")), int(start_token.group("day")))
            explicit_end = _parse_date_parts(
                start_year + 1 if (month, day) < start_md else start_year, month, day
            )
            if explicit_end is None:
                return None
        if explicit_end is not None and not _explicit_reasons(explicit_end, published):
            end, reasons = explicit_end, ()
        else:
            # 기존 규칙: 게시일 연도로 추정하고, 게시일보다 이르면 이듬해로 본다. 시작일에 적힌 연도가
            # 게시일과 동떨어지면(연도 오기 가능) 이쪽을 쓰되 확정하지 않는다.
            raw = _parse_date_parts(published.year, month, day)
            if raw is None:
                return None
            end, reasons = _roll_inferred(raw, published)
            if end is None:
                return None
            if explicit_end is not None:
                reasons = reasons + ("year_conflict",)
        start, start_pos = _start_before(content, sep, window_start, end)
        return _Found(start, end, (start_pos, window_start + month_day_match.end()), reasons)

    full_single_match = _BODY_FULL_DATE_SINGLE_PATTERN.search(window)
    if full_single_match:
        single = _parse_date_parts(
            int(full_single_match.group("year")),
            int(full_single_match.group("month")),
            int(full_single_match.group("day")),
        )
        if single is None:
            return None
        return _single_or_day_range(
            content,
            window_start + full_single_match.start(),
            window_start + full_single_match.end(),
            single,
            _explicit_reasons(single, published),
        )

    month_day_single_match = _BODY_MONTH_DAY_SINGLE_PATTERN.search(window)
    if month_day_single_match and published is not None:
        raw = _parse_date_parts(
            published.year,
            int(month_day_single_match.group("month")),
            int(month_day_single_match.group("day")),
        )
        if raw is None:
            return None
        single, reasons = _roll_inferred(raw, published)
        if single is None:
            return None
        return _single_or_day_range(
            content,
            window_start + month_day_single_match.start(),
            window_start + month_day_single_match.end(),
            single,
            reasons,
        )

    month_day_dot_match = _BODY_MONTH_DAY_DOT_PATTERN.search(window)
    if month_day_dot_match and published is not None:
        raw = _parse_date_parts(
            published.year,
            int(month_day_dot_match.group("month")),
            int(month_day_dot_match.group("day")),
        )
        if raw is None:
            return None
        single, reasons = _roll_inferred(raw, published)
        if single is None:
            return None
        # 원문에 요일이 적혀 있으면 그 요일과 맞는 연도를 고른다("05.02.(목)"을 게시일 이듬해로
        # 넘기지 않는다). 게시일보다 이른 날짜가 되면 지난 기간이므로 확정하지 않는다.
        written = _WEEKDAY_AFTER.match(content, window_start + month_day_dot_match.end())
        if written is not None:
            matching = [
                candidate
                for candidate in {raw, single}
                if _WEEKDAYS[candidate.weekday()] == written.group("weekday")
            ]
            if len(matching) == 1 and matching[0] != single:
                single, reasons = matching[0], ("date_before_published",)
            elif len(matching) == 1:
                # 요일이 이듬해로 넘긴 연도를 뒷받침한다.
                reasons = tuple(reason for reason in reasons if reason != "year_rolled_ambiguous")
            elif not matching:
                reasons = reasons + ("weekday_mismatch",)
        return _single_or_day_range(
            content,
            window_start + month_day_dot_match.start(),
            window_start + month_day_dot_match.end(),
            single,
            reasons,
        )
    return None


def _single_or_day_range(
    content: str,
    token_start: int,
    token_end: int,
    single: date,
    reasons: Tuple[str, ...],
) -> _Found:
    """끝 범위 표기를 못 찾은 날짜 하나. 월이 생략된 끝("~ 27.")과 열린 범위("~"만)를 확인한다."""
    deco = _DECORATION.match(content, token_end)
    after = deco.end() if deco else token_end
    day_end = _DAY_ONLY_END.match(content, after)
    if day_end is not None:
        day = int(day_end.group("day"))
        year, month = single.year, single.month
        if day < single.day:
            month += 1
            if month == 13:
                year, month = year + 1, 1
        end = _parse_date_parts(year, month, day)
        if end is not None and end >= single:
            return _Found(single, end, (token_start, day_end.end()), reasons)
    if _UNTIL.match(content, after):
        return _Found(None, single, (token_start, _UNTIL.match(content, after).end()), reasons)
    if _OPEN_RANGE.match(content, after):
        # 시작만 있고 끝이 없다. 기존 규칙은 이 날짜를 마감일로 썼으므로 값은 두되 확정하지 않는다.
        return _Found(single, single, (token_start, after), reasons + ("missing_end_date",))
    return _Found(single, single, (token_start, after), reasons)


@dataclass(frozen=True)
class _BodyHit:
    label: str
    label_start: int
    found: _Found


def _label_hits(
    content: str,
    pattern: re.Pattern,
    published: Optional[date],
    *,
    strip: str,
    require_near: bool = False,
) -> List[_BodyHit]:
    """라벨마다 마감일을 찾는다. 첫 라벨은 기존 규칙(뒤 180자)을, 나머지는 바로 뒤 날짜만 본다."""
    labels = [(m.start(), m.end(), re.sub(strip, "", m.group(0))) for m in pattern.finditer(content)]
    if not _SCAN_ALL_LABELS:
        labels = labels[:1]
    hits: List[_BodyHit] = []
    for index, (label_start, label_end, label) in enumerate(labels):
        found = _parse_window(content, label_end, published)
        if found is None:
            continue
        if index > 0 or require_near:
            gap = content[label_end : found.span[0]]
            if len(gap.strip()) > _EXTRA_LABEL_MAX_GAP:
                continue
        hits.append(_BodyHit(label, label_start, found))
    return hits


def _body_hits(content: str, published: Optional[date]) -> List[_BodyHit]:
    hits = _label_hits(content, _BODY_DEADLINE_KEYWORD_PATTERN, published, strip=r"\s+")
    if hits or not _USE_COLON_LABELS:
        return hits
    return _label_hits(content, _BODY_COLON_LABEL_PATTERN, published, strip=r"[\s:：]+", require_near=True)


def _anchor_label(content: str, hit: _BodyHit) -> Tuple[int, Tuple[str, ...]]:
    """근거의 시작 위치와 라벨 확신도 사유.

    기존 규칙은 첫 라벨 뒤 180자 안의 날짜를 쓴다. 그 사이에 실제 라벨("신청기간:")이 다시
    나오면 근거를 그 라벨부터 잡고, 신청·접수와 무관한 다른 항목 라벨("수강기간", "공고기간",
    "일시")이 날짜 바로 앞 라벨이면 그 항목의 날짜일 수 있으므로 확정하지 않는다.
    """
    date_start = hit.found.span[0]
    label_match = _BODY_DEADLINE_KEYWORD_PATTERN.match(content, hit.label_start) or _BODY_COLON_LABEL_PATTERN.match(
        content, hit.label_start
    )
    gap_start = label_match.end() if label_match is not None else hit.label_start
    anchors = [(hit.label_start, gap_start)]
    for pattern in (_BODY_DEADLINE_KEYWORD_PATTERN, _GENERIC_PERIOD_LABEL):
        anchors.extend((m.start(), m.end()) for m in pattern.finditer(content, gap_start, date_start))
    anchor_start, anchor_end = max(anchors, key=lambda item: item[1])
    others = [
        m
        for m in _OTHER_PERIOD_LABEL.finditer(content, gap_start, date_start)
        if not any(start <= m.start() < end for start, end in anchors)
    ]
    if any(m.start() >= anchor_end for m in others):
        return anchor_start, ("other_label_between",)
    gap_text = content[anchor_end:date_start].strip()
    if date_start - anchor_end > _CONFIDENT_LABEL_GAP or "\n" in gap_text:
        # 멀거나, 라벨과 날짜 사이에 다른 줄의 글이 끼어 있다.
        return anchor_start, ("date_far_from_label",)
    return anchor_start, ()


def _multiple_periods(content: str, hits: List[_BodyHit]) -> bool:
    first = hits[0]
    # 같은 라벨이 근거 뒤에서 다른 마감으로 다시 나오면 회차가 여럿이다.
    for other in hits[1:]:
        if (
            other.label == first.label
            and other.label_start >= first.found.span[1]
            and other.found.end != first.found.end
        ):
            return True
    # 라벨 바로 앞부터 근거 끝까지에 "1차/2차/회차" 표시가 있다.
    if _SESSION_MARKER.search(content[max(0, first.label_start - 12) : first.found.span[1]]):
        return True
    return False


# --------------------------------------------------------------------------
# 진입점
# --------------------------------------------------------------------------


_WEEKDAYS = "월화수목금토일"
_WEEKDAY_AFTER = re.compile(r"\s*\(\s*(?P<weekday>[월화수목금토일])")
_FULL_DATE_WITH_WEEKDAY = re.compile(
    r"(?<!\d)(?P<year>\d{4})\s*[.\-/년]\s*(?P<month>\d{1,2})\s*[.\-/월]\s*(?P<day>\d{1,2})"
    r"(?:\s*[.일])?\s*\(\s*(?P<weekday>[월화수목금토일])"
)


def _weekday_mismatch(evidence: str) -> bool:
    """근거에 적힌 연·월·일과 요일이 맞지 않으면 연도 오기일 가능성이 크다."""
    for m in _FULL_DATE_WITH_WEEKDAY.finditer(evidence):
        value = _parse_date_parts(int(m.group("year")), int(m.group("month")), int(m.group("day")))
        if value is not None and _WEEKDAYS[value.weekday()] != m.group("weekday"):
            return True
    return False


def _iso(value: Optional[date]) -> Optional[str]:
    return None if value is None else value.isoformat()


def extract_notice_event_dates(
    title: object,
    content: object,
    published_date: object,
) -> Optional[NoticeEventDates]:
    """공지의 신청·접수 기간을 근거와 함께 돌려준다. 마감일을 못 찾으면 None.

    ``end_date``는 기존 ``apply_deadline`` 우선순위를 따른다: 제목 마감 표기 → 본문 신청·접수
    라벨 뒤 날짜(라벨이 여럿이면 날짜가 나오는 첫 라벨).
    """
    title_text = title if isinstance(title, str) else ""
    body_text = content if isinstance(content, str) else ""
    published = _published_day(published_date)

    title_found = _title_deadline(title_text, published)
    hits = _body_hits(body_text, published) if body_text.strip() else []

    if title_found is not None:
        reasons = title_found.reasons
        start = title_found.start
        evidence = title_text[title_found.span[0] : title_found.span[1]].strip()
        source: Literal["title", "body", "title+body"] = "title"
        if hits:
            body_first = hits[0]
            if body_first.found.end == title_found.end:
                if start is None:
                    start = body_first.found.start
                body_evidence = body_text[body_first.label_start : body_first.found.span[1]].strip()
                evidence = f"{evidence}{EVIDENCE_DELIMITER}{body_evidence}"
                source = "title+body"
            elif "missing_end_date" not in body_first.found.reasons:
                reasons = reasons + ("title_body_conflict",)
        if _weekday_mismatch(evidence):
            reasons = reasons + ("weekday_mismatch",)
        return NoticeEventDates(
            start_date=_iso(start),
            end_date=title_found.end.isoformat(),
            evidence=evidence,
            date_status="unverified" if reasons else "confirmed",
            source=source,
            reasons=reasons,
        )

    if not hits:
        return None
    first = hits[0]
    reasons = first.found.reasons
    if _multiple_periods(body_text, hits):
        reasons = reasons + ("multiple_periods",)
    anchor_start, anchor_reasons = _anchor_label(body_text, first)
    reasons = reasons + anchor_reasons
    evidence = body_text[anchor_start : first.found.span[1]].strip()
    if _weekday_mismatch(evidence):
        reasons = reasons + ("weekday_mismatch",)
    return NoticeEventDates(
        start_date=_iso(first.found.start),
        end_date=first.found.end.isoformat(),
        evidence=evidence,
        date_status="unverified" if reasons else "confirmed",
        source="body",
        reasons=reasons,
    )


__all__ = [
    "EVIDENCE_DELIMITER",
    "NoticeEventDates",
    "extract_notice_event_dates",
]
