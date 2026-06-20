"""공지 수집/색인 누락 방지 회귀 테스트."""
from __future__ import annotations

from datetime import date
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.crawlers import dongguk_notices  # noqa: E402
from src.pipelines.ingest import build_notice_chunks  # noqa: E402


def test_collect_board_continues_past_known_articles(monkeypatch):
    """기존 글이 연속으로 있어도 같은 페이지 뒤쪽 신규 글을 확인해야 한다."""
    list_rows = [
        {"article_id": article_id, "title": f"기존 공지 {article_id}", "category": "", "posted_at": date(2026, 6, 1), "views": 1, "is_pinned": False}
        for article_id in range(1, 6)
    ]
    list_rows.append(
        {"article_id": 6, "title": "새 공지", "category": "", "posted_at": date(2026, 6, 1), "views": 1, "is_pinned": False}
    )

    def fake_fetch_notice_list(board_code: str, page: int = 1):
        return list_rows if page == 1 else []

    def fake_fetch_notice_detail(board_code: str, article_id: int):
        return {
            "posted_at": date(2026, 6, 1),
            "views": 2,
            "detail_url": f"https://example.test/detail/{article_id}",
            "content_html": "",
            "content_text": f"본문 {article_id}",
            "attachments": [],
        }

    monkeypatch.setattr(dongguk_notices, "fetch_notice_list", fake_fetch_notice_list)
    monkeypatch.setattr(dongguk_notices, "fetch_notice_detail", fake_fetch_notice_detail)

    df = dongguk_notices.collect_board(
        "일반공지",
        "GENERALNOTICES",
        max_pages=1,
        delay=0,
        earliest_year=2023,
        known_ids={1, 2, 3, 4, 5},
    )

    assert len(df) == 1
    assert df.iloc[0]["원문글ID"] == 6


def test_collect_board_continues_after_page_with_only_known_articles(monkeypatch):
    """한 페이지가 모두 기존 글이어도 max_pages 범위 안의 다음 페이지를 확인해야 한다."""
    pages = {
        1: [
            {"article_id": 1, "title": "기존 공지 1", "category": "", "posted_at": date(2026, 6, 2), "views": 1, "is_pinned": False},
            {"article_id": 2, "title": "기존 공지 2", "category": "", "posted_at": date(2026, 6, 2), "views": 1, "is_pinned": False},
        ],
        2: [
            {"article_id": 3, "title": "두 번째 페이지 신규 공지", "category": "", "posted_at": date(2026, 6, 1), "views": 1, "is_pinned": False},
        ],
    }

    def fake_fetch_notice_list(board_code: str, page: int = 1):
        return pages.get(page, [])

    def fake_fetch_notice_detail(board_code: str, article_id: int):
        return {
            "posted_at": date(2026, 6, 1),
            "views": 2,
            "detail_url": f"https://example.test/detail/{article_id}",
            "content_html": "",
            "content_text": f"본문 {article_id}",
            "attachments": [],
        }

    monkeypatch.setattr(dongguk_notices, "fetch_notice_list", fake_fetch_notice_list)
    monkeypatch.setattr(dongguk_notices, "fetch_notice_detail", fake_fetch_notice_detail)

    df = dongguk_notices.collect_board(
        "일반공지",
        "GENERALNOTICES",
        max_pages=2,
        delay=0,
        earliest_year=2023,
        known_ids={1, 2},
    )

    assert len(df) == 1
    assert df.iloc[0]["원문글ID"] == 3


def test_collect_board_keeps_notice_when_detail_fetch_fails(monkeypatch):
    def fake_fetch_notice_list(board_code: str, page: int = 1):
        if page != 1:
            return []
        return [
            {
                "article_id": 77,
                "title": "상세 본문이 없는 공지",
                "category": "일반",
                "posted_at": date(2026, 6, 19),
                "views": 1,
                "is_pinned": False,
            }
        ]

    def fake_fetch_notice_detail(board_code: str, article_id: int):
        raise RuntimeError("detail parse failed")

    monkeypatch.setattr(dongguk_notices, "fetch_notice_list", fake_fetch_notice_list)
    monkeypatch.setattr(dongguk_notices, "fetch_notice_detail", fake_fetch_notice_detail)

    df = dongguk_notices.collect_board(
        "일반공지",
        "GENERALNOTICES",
        max_pages=1,
        delay=0,
        earliest_year=2023,
    )

    assert len(df) == 1
    assert df.iloc[0]["제목"] == "상세 본문이 없는 공지"
    assert df.iloc[0]["본문"] == ""
    assert df.iloc[0]["상세URL"].endswith("/article/GENERALNOTICES/detail/77")


def test_build_notice_chunks_indexes_title_when_body_is_empty():
    df = pd.DataFrame(
        [
            {
                "게시판": "장학공지",
                "게시판코드": "JANGHAKNOTICE",
                "원문글ID": 123,
                "제목": "2026학년도 장학 신청 안내",
                "카테고리": "장학",
                "게시일": "2026-06-19",
                "상단고정": False,
                "상세URL": "https://www.dongguk.edu/article/JANGHAKNOTICE/detail/123",
                "본문": "",
                "첨부파일": [],
                "db_id": 1,
            }
        ]
    )

    chunks = build_notice_chunks(df)

    assert not chunks.empty
    assert "2026학년도 장학 신청 안내" in chunks.iloc[0]["chunk_text"]
    assert "공지 링크를 확인" in chunks.iloc[0]["chunk_text"]
    assert chunks.iloc[0]["url"].endswith("/123")


def test_build_notice_chunks_extracts_deadline_from_dot_range_body():
    df = pd.DataFrame(
        [
            {
                "게시판": "학사공지",
                "게시판코드": "HAKSANOTICE",
                "원문글ID": 124,
                "제목": "2026학년도 여름계절학기 수강신청 안내",
                "카테고리": "학사",
                "게시일": "2026-05-01",
                "상단고정": False,
                "상세URL": "https://www.dongguk.edu/article/HAKSANOTICE/detail/124",
                "본문": "수강신청 기간: 2026. 05. 14.(화) 10:00 ~ 05. 16.(목) 23:59 [3일간]",
                "첨부파일": [],
                "db_id": 2,
            }
        ]
    )

    chunks = build_notice_chunks(df)

    assert chunks.iloc[0]["apply_deadline"] == "2026-05-16"


def test_build_notice_chunks_extracts_deadline_from_submission_until_body():
    df = pd.DataFrame(
        [
            {
                "게시판": "장학공지",
                "게시판코드": "JANGHAKNOTICE",
                "원문글ID": 125,
                "제목": "교외장학 서류 제출 안내",
                "카테고리": "장학",
                "게시일": "2026-06-19",
                "상단고정": False,
                "상세URL": "https://www.dongguk.edu/article/JANGHAKNOTICE/detail/125",
                "본문": "서류 제출 기한: 2026년 7월 5일(금)까지 학생서비스팀으로 제출",
                "첨부파일": [],
                "db_id": 3,
            }
        ]
    )

    chunks = build_notice_chunks(df)

    assert chunks.iloc[0]["apply_deadline"] == "2026-07-05"


def test_build_notice_chunks_extracts_deadline_from_title_with_korean_month_day():
    df = pd.DataFrame(
        [
            {
                "게시판": "일반공지",
                "게시판코드": "GENERALNOTICES",
                "원문글ID": 126,
                "제목": "비교과 프로그램 신청 마감 6월 21일 안내",
                "카테고리": "일반",
                "게시일": "2026-06-10",
                "상단고정": False,
                "상세URL": "https://www.dongguk.edu/article/GENERALNOTICES/detail/126",
                "본문": "신청 방법은 첨부파일을 확인하세요.",
                "첨부파일": [],
                "db_id": 4,
            }
        ]
    )

    chunks = build_notice_chunks(df)

    assert chunks.iloc[0]["apply_deadline"] == "2026-06-21"
