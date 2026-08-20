"""공지 수집/색인 누락 방지 회귀 테스트."""
from __future__ import annotations

from datetime import date
import sys
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.crawlers import dongguk_notices  # noqa: E402
from src.database import Base, IngestionRun, SourceDocument  # noqa: E402
from src.pipelines import notices_sync  # noqa: E402
from scripts import update_notices  # noqa: E402
from src.pipelines.ingest import (  # noqa: E402
    _extract_notice_apply_deadline,
    build_notice_chunks,
)


def test_collect_board_continues_past_known_articles(monkeypatch):
    """기존 글이 연속으로 있어도 같은 페이지 뒤쪽 신규 글을 확인해야 한다."""
    list_rows = [
        {"article_id": article_id, "title": f"기존 공지 {article_id}", "category": "", "posted_at": date(2026, 6, 1), "views": 1, "is_pinned": False}
        for article_id in range(1, 6)
    ]
    list_rows.append(
        {"article_id": 6, "title": "새 공지", "category": "", "posted_at": date(2026, 6, 1), "views": 1, "is_pinned": False}
    )

    def fake_fetch_notice_list(board_code: str, page: int = 1, **_request_limits):
        return list_rows if page == 1 else []

    def fake_fetch_notice_detail(board_code: str, article_id: int, **_request_limits):
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

    def fake_fetch_notice_list(board_code: str, page: int = 1, **_request_limits):
        return pages.get(page, [])

    def fake_fetch_notice_detail(board_code: str, article_id: int, **_request_limits):
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
    def fake_fetch_notice_list(board_code: str, page: int = 1, **_request_limits):
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

    def fake_fetch_notice_detail(board_code: str, article_id: int, **_request_limits):
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


def test_crawl_notices_fails_when_every_board_list_is_unreachable(monkeypatch):
    def fail_list(*_args, **_kwargs):
        raise RuntimeError("dns unavailable")

    monkeypatch.setattr(dongguk_notices, "fetch_notice_list", fail_list)

    with pytest.raises(dongguk_notices.NoticeCrawlError, match="모두 목록 수집에 실패"):
        dongguk_notices.crawl_notices(
            boards=["일반공지", "학사공지"],
            max_pages=1,
            delay=0,
            request_retries=1,
        )


def test_crawl_notices_allows_zero_new_rows_when_board_was_reachable(monkeypatch):
    def fake_fetch_notice_list(board_code: str, page: int = 1, **_request_limits):
        return [
            {
                "article_id": 77,
                "title": "이미 수집된 공지",
                "category": "일반",
                "posted_at": date(2026, 8, 10),
                "views": 1,
                "is_pinned": False,
            }
        ]

    monkeypatch.setattr(dongguk_notices, "fetch_notice_list", fake_fetch_notice_list)

    frame = dongguk_notices.crawl_notices(
        boards=["일반공지"],
        max_pages=1,
        delay=0,
        known_ids_by_board={"일반공지": {77}},
    )

    assert frame.empty
    assert frame.attrs["crawl_status"] == "success"
    assert frame.attrs["crawl_incomplete_boards"] == []
    assert frame.attrs["crawl_diagnostics"][0]["list_rows_seen"] == 1


def test_incomplete_crawl_never_hides_unseen_source_documents(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    seed = session_factory()
    try:
        seed.add(
            SourceDocument(
                dataset="notices",
                source_type="html_notice",
                source_id="GENERALNOTICES:1",
                source_url="https://www.dongguk.edu/article/GENERALNOTICES/detail/1",
                document_key="notices:GENERALNOTICES:1",
                title="기존 공지",
                category="일반",
                status="active",
                miss_count=0,
            )
        )
        seed.commit()
    finally:
        seed.close()

    monkeypatch.setattr(notices_sync, "SessionLocal", session_factory)
    frame = pd.DataFrame(columns=["게시판", "게시판코드", "원문글ID", "상세URL"])
    frame.attrs["crawl_incomplete_boards"] = ["일반공지"]

    result = notices_sync.collect_notice_documents(
        frame,
        allow_missing_detection=True,
    )

    verification = session_factory()
    try:
        document = verification.query(SourceDocument).one()
        run = verification.query(IngestionRun).one()
        assert document.status == "active"
        assert document.miss_count == 0
        assert run.status == "partial_success"
        assert "missing detection disabled" in str(run.error_summary)
        assert result.crawl_incomplete_boards == ["일반공지"]
    finally:
        verification.close()


def test_pre_collection_failure_is_persisted_in_ingestion_history(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(notices_sync, "SessionLocal", session_factory)

    run_id = notices_sync.record_notice_ingestion_failure(
        "all boards unreachable",
        stage="crawl",
    )

    verification = session_factory()
    try:
        run = verification.query(IngestionRun).one()
        assert run.id == run_id
        assert run.dataset == "notices"
        assert run.status == "failed"
        assert run.finished_at is not None
        assert run.error_summary == "crawl: all boards unreachable"
    finally:
        verification.close()


def test_notice_cli_records_crawl_failure_and_returns_failure(monkeypatch):
    recorded: list[tuple[str, str]] = []

    monkeypatch.setattr(update_notices, "load_known_article_ids_by_board", lambda: {})
    monkeypatch.setattr(
        update_notices,
        "crawl_notices",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("dns unavailable")),
    )
    monkeypatch.setattr(
        update_notices,
        "record_notice_ingestion_failure",
        lambda error, stage: recorded.append((stage, str(error))) or 99,
    )

    succeeded = update_notices._run_once(
        ["일반공지"],
        max_pages=1,
        delay=0,
        earliest_year=2026,
        mode="collect-only",
    )

    assert succeeded is False
    assert recorded == [("crawl", "dns unavailable")]


def test_notice_cli_returns_failure_for_partial_crawl(monkeypatch):
    frame = pd.DataFrame()
    monkeypatch.setattr(update_notices, "load_known_article_ids_by_board", lambda: {})
    monkeypatch.setattr(update_notices, "crawl_notices", lambda **_kwargs: frame)
    monkeypatch.setattr(
        update_notices,
        "sync_notices",
        lambda *_args, **_kwargs: {
            "seen": 2,
            "new": 1,
            "updated": 0,
            "deleted": 0,
            "failed": 0,
            "incomplete_boards": 1,
        },
    )

    succeeded = update_notices._run_once(
        ["일반공지"],
        max_pages=1,
        delay=0,
        earliest_year=2026,
        mode="collect-only",
    )

    assert succeeded is False


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


def test_notice_deadline_title_suffix_patterns_are_supported():
    cases = {
        "L-HUSS 여행코스 공모전 밸류업(신안) (2026.08.06까지)": "2026-08-06",
        "아이디어 공모전 (10월 12일 마감)": "2026-10-12",
        "교내 프로그램 신청 (12.22까지)": "2026-12-22",
    }

    for title, expected in cases.items():
        assert _extract_notice_apply_deadline(
            title,
            "",
            "2026-07-01",
        ) == expected


def test_notice_deadline_without_year_rolls_into_next_year_after_publication():
    assert _extract_notice_apply_deadline(
        "겨울 프로그램 신청 (1.15까지)",
        "",
        "2026-12-20",
    ) == "2027-01-15"


def test_application_form_window_wins_over_later_result_announcement():
    content = """
    지원서 접수: 2026. 8. 5.(수) ~ 8. 13.(목) 14시, nDRIMS 접수
    비대면 AI 면접: 2026. 8. 19.(수) 10시 ~ 8. 20.(목) 15시
    합격자 발표: 2026. 8. 27.(목) 17시 이후
    """
    assert _extract_notice_apply_deadline(
        "2027-1학기 파견 영어권 교환학생 선발 일정",
        content,
        "2026-07-02",
    ) == "2026-08-13"
