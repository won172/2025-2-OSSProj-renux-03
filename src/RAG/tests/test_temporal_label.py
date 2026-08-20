"""근거 문서의 시점 판정을 코드가 계산한다.

프롬프트에 날짜 비교를 맡겼더니 2026-08-04에 "2025년 12월 12일에 예정된 종강총회"라는
답이 나왔다. 8개월 지난 행사를 앞으로 열릴 일로 쓴 것이다.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.temporal_label import (  # noqa: E402
    describe_document_time,
    describe_publication_age,
    describe_schedule_window,
)

기준일 = date(2026, 8, 4)


def test_지난_일정을_지났다고_판정한다():
    라벨 = describe_schedule_window("2026-07-31", "2026-08-03", as_of=기준일)
    assert 라벨 is not None and "지난 일정" in 라벨


def test_진행_중인_일정을_구분한다():
    라벨 = describe_schedule_window("2026-08-03", "2026-08-07", as_of=기준일)
    assert 라벨 is not None and "진행 중" in 라벨


def test_다가오는_일정은_남은_일수를_밝힌다():
    라벨 = describe_schedule_window("2026-09-01", "2026-09-07", as_of=기준일)
    assert 라벨 is not None and "28일 뒤" in 라벨


def test_종료일이_없으면_시작일로_판정한다():
    assert "진행 중" in describe_schedule_window("2026-08-04", "", as_of=기준일)
    assert "지난 일정" in describe_schedule_window("2026-08-01", None, as_of=기준일)


def test_게시_경과일을_계산한다():
    라벨 = describe_publication_age("2025-12-12", as_of=기준일)
    assert 라벨 is not None and "235일 경과" in 라벨


def test_당일_게시를_구분한다():
    assert "당일 게시" in describe_publication_age("2026-08-04", as_of=기준일)


@pytest.mark.parametrize("빈값", ["", None, "nan", "알 수 없음"])
def test_날짜를_모르면_아무_말도_하지_않는다(빈값):
    assert describe_schedule_window(빈값, 빈값, as_of=기준일) is None
    assert describe_publication_age(빈값, as_of=기준일) is None


def test_일정_기간이_있으면_게시일보다_우선한다():
    라벨 = describe_document_time(
        as_of=기준일,
        schedule_start="2026-08-03",
        schedule_end="2026-08-07",
        published_at="2025-01-01",
    )
    assert "진행 중" in 라벨


def test_일정이_없으면_게시_경과로_돌아간다():
    라벨 = describe_document_time(as_of=기준일, published_at="2025-12-12")
    assert 라벨 is not None and "게시 후" in 라벨


def test_행사_일시를_지났다고_단정하지_않는다():
    """공지 본문의 행사 날짜는 정형 필드가 아니라 코드가 확인할 수 없다.

    게시 경과만 밝히고, 행사 자체가 끝났다고 말하지 않는다.
    """
    라벨 = describe_document_time(as_of=기준일, published_at="2025-12-12")
    assert "지난 일정" not in 라벨
