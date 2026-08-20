"""근거 문서에 시점 판정을 붙인다.

프롬프트 규칙 15·16이 LLM에게 날짜 비교를 시키고 있었다. 전체 규칙 중 가장 긴 두 개인데,
날짜 산술은 모델이 조용히 틀리는 대표적인 영역이다. 실제로 2026-08-04에 이런 답이 나왔다.

    "2025년 12월 12일에 예정된 종강총회가 있습니다"   ← 8개월 지난 행사

이 팀의 원칙은 *"시점은 코드가 정한다"*이고, 코드는 이미 답을 알고 있다. 그러니 계산해서
근거에 실어 보내고, 모델은 읽기만 하게 한다.

    [문서2] … · 2025-12-12 게시 · 기준일(2026-08-04) 기준 236일 지남

라벨은 **아는 것만** 말한다. 공지 본문에 적힌 행사 일시는 정형 필드가 아니라 추출할 수
없으므로, 게시 시점의 경과만 밝히고 행사 자체가 지났다고 단정하지 않는다.
"""
from __future__ import annotations

from datetime import date, datetime


def _as_date(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return None
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def describe_schedule_window(
    start: object,
    end: object,
    *,
    as_of: date,
) -> str | None:
    """학사일정 행의 기간을 기준일과 대조한 한 줄 판정(모르면 None)."""
    start_date = _as_date(start)
    end_date = _as_date(end) or start_date
    if start_date is None:
        return None
    if end_date is not None and end_date < as_of:
        return f"기준일({as_of.isoformat()}) 기준 이미 지난 일정"
    if start_date > as_of:
        remaining = (start_date - as_of).days
        return f"기준일({as_of.isoformat()}) 기준 {remaining}일 뒤 시작 예정"
    return f"기준일({as_of.isoformat()}) 기준 진행 중"


def describe_publication_age(published_at: object, *, as_of: date) -> str | None:
    """게시일과 기준일의 간격(모르면 None).

    행사 일시가 아니라 **게시 시점**만 말한다. 공지 본문에 적힌 날짜는 정형 필드가
    아니어서 코드가 확인할 수 없기 때문이다.
    """
    published = _as_date(published_at)
    if published is None:
        return None
    days = (as_of - published).days
    if days < 0:
        return f"기준일({as_of.isoformat()})보다 나중에 게시된 자료"
    if days == 0:
        return f"기준일({as_of.isoformat()}) 당일 게시"
    return f"기준일({as_of.isoformat()}) 기준 게시 후 {days}일 경과"


def describe_document_time(
    *,
    as_of: date,
    schedule_start: object = None,
    schedule_end: object = None,
    published_at: object = None,
) -> str | None:
    """문서에 붙일 시점 판정. 일정 기간이 있으면 그것을, 없으면 게시 경과를 쓴다."""
    window = describe_schedule_window(schedule_start, schedule_end, as_of=as_of)
    if window is not None:
        return window
    return describe_publication_age(published_at, as_of=as_of)


__all__ = [
    "describe_document_time",
    "describe_publication_age",
    "describe_schedule_window",
]
