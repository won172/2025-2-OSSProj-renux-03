"""사용자 질문에서 날짜 관련 정보를 추출하는 유틸리티."""

from dataclasses import dataclass
from datetime import date, timedelta, datetime, timezone
import re
from typing import Optional, Tuple


@dataclass(frozen=True)
class QueryDateFilter:
    start: date
    end: date
    label: str
    is_relative: bool = False
    relaxed_start: Optional[date] = None
    relaxed_end: Optional[date] = None

def _parse_relative_date(query: str) -> Optional[QueryDateFilter]:
    KST = timezone(timedelta(hours=9))
    today = datetime.now(KST).date()
    
    if "오늘" in query:
        return QueryDateFilter(
            start=today,
            end=today,
            label="today",
            is_relative=True,
            relaxed_start=today - timedelta(days=2),
            relaxed_end=today,
        )
    elif "어제" in query:
        yesterday = today - timedelta(days=1)
        return QueryDateFilter(start=yesterday, end=yesterday, label="yesterday", is_relative=True)
    elif "내일" in query:
        tomorrow = today + timedelta(days=1)
        return QueryDateFilter(start=tomorrow, end=tomorrow, label="tomorrow", is_relative=True)
    elif any(keyword in query for keyword in ("방금", "최신", "최근", "새로", "막 올라온", "올라온")):
        return QueryDateFilter(
            start=today - timedelta(days=6),
            end=today,
            label="recent",
            is_relative=True,
            relaxed_start=today - timedelta(days=29),
            relaxed_end=today,
        )
    elif "지난주" in query or "지난 주" in query:
        start_of_last_week = today - timedelta(days=today.weekday() + 7)
        end_of_last_week = start_of_last_week + timedelta(days=6)
        return QueryDateFilter(start=start_of_last_week, end=end_of_last_week, label="last_week", is_relative=True)
    elif "이번주" in query or "이번 주" in query:
        start_of_this_week = today - timedelta(days=today.weekday())
        end_of_this_week = start_of_this_week + timedelta(days=6)
        return QueryDateFilter(start=start_of_this_week, end=end_of_this_week, label="this_week", is_relative=True)
    elif "지난달" in query or "지난 달" in query:
        first_day_of_this_month = today.replace(day=1)
        last_day_of_last_month = first_day_of_this_month - timedelta(days=1)
        first_day_of_last_month = last_day_of_last_month.replace(day=1)
        return QueryDateFilter(start=first_day_of_last_month, end=last_day_of_last_month, label="last_month", is_relative=True)
    elif "이번달" in query or "이번 달" in query:
        first_day_of_this_month = today.replace(day=1)
        # 다음 달 1일에서 하루를 빼면 이번 달 마지막 날이 됩니다.
        if today.month == 12:
            last_day_of_this_month = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            last_day_of_this_month = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        return QueryDateFilter(start=first_day_of_this_month, end=last_day_of_this_month, label="this_month", is_relative=True)
    
    return None

def _parse_specific_date(query: str) -> Optional[QueryDateFilter]:
    # YYYY년 MM월 (ex: 2025년 11월)
    match = re.search(r"(\d{4})년\s*(\d{1,2})월", query)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        if 1 <= month <= 12:
            first_day = date(year, month, 1)
            # 다음 달 1일에서 하루를 빼면 해당 월의 마지막 날이 됩니다.
            if month == 12:
                last_day = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                last_day = date(year, month + 1, 1) - timedelta(days=1)
            return QueryDateFilter(start=first_day, end=last_day, label="specific_month")
    
    # YYYY년 MM월 DD일 (ex: 2025년 11월 20일)
    match = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", query)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))
        try:
            specific_date = date(year, month, day)
            return QueryDateFilter(start=specific_date, end=specific_date, label="specific_day")
        except ValueError:
            pass # Invalid date
            
    return None

def extract_date_filter_from_query(query: str) -> Optional[QueryDateFilter]:
    """
    사용자 질문에서 날짜 필터 메타데이터를 추출합니다.
    날짜 정보가 없으면 None을 반환합니다.
    """
    date_filter = _parse_relative_date(query)
    if date_filter:
        return date_filter

    return _parse_specific_date(query)


def extract_date_range_from_query(query: str) -> Optional[Tuple[date, date]]:
    """
    사용자 질문에서 날짜 관련 정보를 추출하고 (시작 날짜, 종료 날짜) 튜플을 반환합니다.
    날짜 정보가 없으면 None을 반환합니다.
    """
    date_filter = extract_date_filter_from_query(query)
    if not date_filter:
        return None
    return date_filter.start, date_filter.end

if __name__ == '__main__':
    print("--- 날짜 파서 테스트 ---")
    test_queries = [
        "오늘 공지사항",
        "어제 학사일정",
        "내일 수업",
        "지난주 행사",
        "이번주 날씨",
        "지난달 소식",
        "이번달 계획",
        "2023년 10월 공지",
        "2024년 5월 15일 이벤트",
        "그냥 일반 질문",
    ]
    
    for q in test_queries:
        result = extract_date_range_from_query(q)
        if result:
            print(f"질문: '{q}' -> 날짜 범위: {result[0].strftime('%Y-%m-%d')} ~ {result[1].strftime('%Y-%m-%d')}")
        else:
            print(f"질문: '{q}' -> 날짜 정보 없음")
