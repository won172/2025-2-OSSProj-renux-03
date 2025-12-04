"""사용자 질문에서 날짜 관련 정보를 추출하는 유틸리티."""

from datetime import date, timedelta, datetime, timezone
import re
from typing import Optional, Tuple

def _parse_relative_date(query: str) -> Optional[Tuple[date, date]]:
    KST = timezone(timedelta(hours=9))
    today = datetime.now(KST).date()
    
    if "오늘" in query:
        return today, today
    elif "어제" in query:
        yesterday = today - timedelta(days=1)
        return yesterday, yesterday
    elif "내일" in query:
        tomorrow = today + timedelta(days=1)
        return tomorrow, tomorrow
    elif "지난주" in query or "지난 주" in query:
        start_of_last_week = today - timedelta(days=today.weekday() + 7)
        end_of_last_week = start_of_last_week + timedelta(days=6)
        return start_of_last_week, end_of_last_week
    elif "이번주" in query or "이번 주" in query:
        start_of_this_week = today - timedelta(days=today.weekday())
        end_of_this_week = start_of_this_week + timedelta(days=6)
        return start_of_this_week, end_of_this_week
    elif "지난달" in query or "지난 달" in query:
        first_day_of_this_month = today.replace(day=1)
        last_day_of_last_month = first_day_of_this_month - timedelta(days=1)
        first_day_of_last_month = last_day_of_last_month.replace(day=1)
        return first_day_of_last_month, last_day_of_last_month
    elif "이번달" in query or "이번 달" in query:
        first_day_of_this_month = today.replace(day=1)
        # 다음 달 1일에서 하루를 빼면 이번 달 마지막 날이 됩니다.
        if today.month == 12:
            last_day_of_this_month = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            last_day_of_this_month = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        return first_day_of_this_month, last_day_of_this_month
    
    return None

def _parse_specific_date(query: str) -> Optional[Tuple[date, date]]:
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
            return first_day, last_day
    
    # YYYY년 MM월 DD일 (ex: 2025년 11월 20일)
    match = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", query)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))
        try:
            specific_date = date(year, month, day)
            return specific_date, specific_date
        except ValueError:
            pass # Invalid date
            
    return None

def extract_date_range_from_query(query: str) -> Optional[Tuple[date, date]]:
    """
    사용자 질문에서 날짜 관련 정보를 추출하고 (시작 날짜, 종료 날짜) 튜플을 반환합니다.
    날짜 정보가 없으면 None을 반환합니다.
    """
    # 상대적 날짜 (오늘, 어제, 지난주, 이번달 등) 우선 파싱
    date_range = _parse_relative_date(query)
    if date_range:
        return date_range
    
    # 특정 날짜 (YYYY년 MM월 DD일, YYYY년 MM월 등) 파싱
    date_range = _parse_specific_date(query)
    if date_range:
        return date_range
        
    return None

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
