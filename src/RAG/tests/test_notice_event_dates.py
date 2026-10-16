from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pipelines.ingest import _extract_notice_apply_deadline  # noqa: E402
from src.pipelines.notice_event_dates import (  # noqa: E402
    EVIDENCE_DELIMITER,
    extract_notice_event_dates,
)


# (이름, 제목, 본문, 게시일, 시작일, 마감일, 확정 여부, unverified 사유)
CASES = [
    ("month_omitted_end", "장학 신청 안내", "신청기간: 2023. 10. 23. (월)~ 27. (금)\n문의: 장학팀", "2023-10-16",
     "2023-10-23", "2023-10-27", "confirmed", ()),
    ("two_digit_year", "교육생 모집", "모집기간: 26.10.12(월)~ 26.10.19\n선발 발표: 추후", "2026-10-05",
     "2026-10-12", "2026-10-19", "confirmed", ()),
    ("apostrophe_two_digit_year", "현장실습 모집", "모집기간: ~'26.02.13. (금)지원 방법은 첨부 참고", "2025-12-18",
     None, "2026-02-13", "confirmed", ()),
    ("colon_label_without_period_word", "프로그램 안내", "신청: 2025. 6. 2.(월) ~ 6. 5.(목)\n장소: 본관", "2025-05-28",
     "2025-06-02", "2025-06-05", "confirmed", ()),
    ("dot_month_day_with_weekday_until", "우수 참여자 시상", "제출기간: 12.30(월)까지 제출방법: 방문", "2024-12-19",
     None, "2024-12-30", "confirmed", ()),
    ("dot_month_day_weekday_picks_past_year", "계절학기 안내", "신청기간 ※ 05.02.(목)까지", "2024-05-03",
     None, "2024-05-02", "unverified", ("date_before_published",)),
    ("multiple_sessions", "교환학생 선발",
     "1차 신청기간: 2026. 3. 2.(월) ~ 3. 6.(금)\n2차 신청기간: 2026. 4. 1.(수) ~ 4. 3.(금)", "2026-02-25",
     "2026-03-02", "2026-03-06", "unverified", ("multiple_periods",)),
    ("open_range_without_end", "복학 신청", "신청기간: 2026. 1. 5. (월)10:00 ~\n유의사항: 기간 엄수", "2025-12-29",
     "2026-01-05", "2026-01-05", "unverified", ("missing_end_date",)),
    ("title_and_body_agree", "장학생 모집(~10.20)", "접수기간: 2026. 10. 13.(화) ~ 10. 20.(화)", "2026-10-08",
     "2026-10-13", "2026-10-20", "confirmed", ()),
    ("title_and_body_conflict", "장학생 모집(~10.20)", "접수기간: 2026. 10. 13.(화) ~ 10. 22.(목)", "2026-10-08",
     None, "2026-10-20", "unverified", ("title_body_conflict",)),
    ("weekday_mismatch", "특강 신청", "신청기간: 2026. 3. 2.(화) ~ 2026. 3. 6.(금)", "2026-02-25",
     "2026-03-02", "2026-03-06", "unverified", ("weekday_mismatch",)),
]


@pytest.mark.parametrize(
    "title, body, published, start, end, status, reasons",
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_extracts_period_evidence_and_status(title, body, published, start, end, status, reasons):
    result = extract_notice_event_dates(title, body, published)

    assert result is not None
    assert (result.start_date, result.end_date, result.date_status) == (start, end, status)
    assert result.reasons == reasons
    # 근거는 제목 또는 본문의 정확한 부분 문자열이다.
    for part in result.evidence.split(EVIDENCE_DELIMITER):
        assert part and (part in title or part in body)
    # apply_deadline(채팅 마감 필터·알림 기준일)은 같은 마감일을 쓴다.
    assert _extract_notice_apply_deadline(title, body, published) == end


@pytest.mark.parametrize(
    "title, body, published",
    [
        # 전화번호 "02-2260"을 2월 22일로 읽지 않는다.
        ("조교 모집", "모집기간: ~채용시 마감\n문의처: 사무실 ☎ 02-2260-3502", "2024-01-11"),
        # 요일·까지가 없는 소수는 날짜가 아니다.
        ("장학 안내", "신청기간 안내: 평점 3.5 이상 학생은 신청 가능", "2026-03-02"),
        ("안내", "자세한 내용은 첨부파일 참고", "2026-01-01"),
        ("안내", "", None),
    ],
    ids=["phone_number", "decimal", "no_date", "empty"],
)
def test_returns_none_without_a_grounded_deadline(title, body, published):
    assert extract_notice_event_dates(title, body, published) is None
    assert _extract_notice_apply_deadline(title, body, published) is None


def test_unverified_reasons_always_mean_unverified():
    for _, title, body, published, *_ in CASES:
        result = extract_notice_event_dates(title, body, published)
        assert (result.date_status == "confirmed") == (not result.reasons)
