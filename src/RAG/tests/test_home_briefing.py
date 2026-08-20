"""홈 브리핑 원문 파싱 테스트.

실제 CSV 원문(dongguk_meals.csv)의 형식을 그대로 옮겨 검증한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils.briefing import (  # noqa: E402
    format_schedule_period,
    is_closed_row,
    split_meal_corners,
)

# 실제 CSV 한 셀의 형태. 코너 표기 없는 조각("김구이 배추김치")이 섞여 있다.
REAL_MENU_TEXT = (
    "[일반(뚝배기)] (뚝)우삼겹된장찌개 / "
    "[중식 A코너 6,500원] 쌀밥 청양마요치킨커틀렛 무말랭이무침/김구이 배추김치 / "
    "[중식 특별코너] 옛날자장면 / "
    "[석식 6,500원] 쌀밥 고추마요치킨커틀렛"
)


def test_코너_표기를_기준으로_메뉴를_분리한다():
    corners = split_meal_corners(REAL_MENU_TEXT, limit=4)

    assert [corner["corner"] for corner in corners] == [
        "일반(뚝배기)",
        "중식 A코너 6,500원",
        "중식 특별코너",
        "석식 6,500원",
    ]
    assert corners[0]["menu"] == "(뚝)우삼겹된장찌개"


def test_코너_표기가_없는_조각은_앞_코너_메뉴에_이어_붙인다():
    """원문은 한 코너 안에서도 슬래시로 메뉴를 더 나열한다 — 버리면 반찬이 사라진다."""
    corners = split_meal_corners(REAL_MENU_TEXT, limit=4)

    assert corners[1]["menu"] == "쌀밥 청양마요치킨커틀렛 무말랭이무침 · 김구이 배추김치"


def test_limit에_도달하면_더_읽지_않는다():
    assert len(split_meal_corners(REAL_MENU_TEXT, limit=2)) == 2
    assert split_meal_corners(REAL_MENU_TEXT, limit=0) == []


def test_메뉴가_비어_있는_코너는_건너뛴다():
    corners = split_meal_corners("[중식] / [석식] 쌀밥 제육볶음", limit=3)

    assert [corner["corner"] for corner in corners] == ["석식"]


def test_코너_표기가_전혀_없으면_빈_목록을_돌려준다():
    """앞 코너가 없는 상태의 연속 조각은 붙일 곳이 없어 버려진다."""
    assert split_meal_corners("쌀밥 제육볶음 미소국", limit=3) == []


@pytest.mark.parametrize("flag", ["True", "true", " TRUE "])
def test_휴무_플래그가_문자열이어도_휴무로_판정한다(flag):
    """CSV의 is_closed는 문자열이라 bool() 변환에 맡기면 'False'도 참이 된다."""
    assert is_closed_row(flag, "제육덮밥") is True


@pytest.mark.parametrize("flag", ["False", "false", "", None])
def test_운영중인_행은_휴무로_판정하지_않는다(flag):
    assert is_closed_row(flag, "제육덮밥") is False


def test_플래그가_없어도_본문이_휴무면_쉬는_것으로_본다():
    assert is_closed_row("False", "휴무") is True
    assert is_closed_row("", " 휴무 ") is True


def test_하루짜리_일정은_날짜를_한_번만_쓴다():
    assert format_schedule_period("2026-09-01", "2026-09-01") == "2026-09-01"
    assert format_schedule_period("2026-09-01", "") == "2026-09-01"
    assert format_schedule_period("2026-09-01", None) == "2026-09-01"


def test_기간_일정은_시작과_종료를_함께_쓴다():
    assert format_schedule_period("2026-08-01", "2026-08-02") == "2026-08-01 ~ 2026-08-02"


def test_시작일이_없으면_종료일만_표기한다():
    assert format_schedule_period(None, "2026-08-02") == "2026-08-02"
    assert format_schedule_period("", "") == ""


def test_홈_브리핑과_직접답변은_학식_csv가_아닌_정본을_읽는다(monkeypatch):
    import api.rag_service as rag_service

    today = rag_service.kst_now().strftime("%Y-%m-%d")
    canonical = pd.DataFrame([
        {
            "date": today,
            "restaurant": "학생식당",
            "menu_text": "[중식] 김치찌개",
            "is_closed": "0",
        }
    ])
    monkeypatch.setattr(rag_service, "load_meals_from_db", lambda: canonical)

    def fail_if_csv_is_used(*args, **kwargs):
        raise AssertionError("학식 런타임 경로가 CSV를 읽었습니다")

    monkeypatch.setattr(rag_service.pd, "read_csv", fail_if_csv_is_used)

    assert rag_service._briefing_meals() == [
        {"corner": "학생식당 중식", "menu": "김치찌개"}
    ]
    rows = rag_service._load_meal_rows_for_direct_answer()
    assert len(rows) == 1
    assert rows[0].restaurant == "학생식당"
