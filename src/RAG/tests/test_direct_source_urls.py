"""Official source URLs survive canonical storage into deterministic answers."""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api import rag_service
from scripts.catch_up_meals import merge_active_meals
from src import database as db
from src.crawlers import dongguk_meals, dongguk_notices
from src.pipelines import ingest
from src.services.scheduler import _merge_schedule_snapshots
from src.services.direct_answer import answer_meal, answer_schedule_when
from src.utils.briefing import split_meal_corners


SCHEDULE_URL = "https://www.dongguk.edu/schedule/detail"
COOP_URL = dongguk_meals._meal_page_url(date(2026, 9, 1))
COOP_URL_NEXT = dongguk_meals._meal_page_url(date(2026, 9, 2))
DFLEX_URL = "https://www.dongguk.edu/cmmn/fileDown.do?filename=menu.pdf"


@pytest.fixture
def source_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    factory = sessionmaker(bind=engine)
    db.Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(ingest, "SessionLocal", factory)
    monkeypatch.setattr(rag_service, "SessionLocal", factory)
    return factory


def test_schedule_official_url_reaches_direct_answer_without_changing_payload(source_db):
    with source_db() as session:
        ingest._store_schedule_source_documents(session, pd.DataFrame([
            {"title": "개강", "start_date": "2026-09-01", "end_date": "2026-09-01", "source_url": SCHEDULE_URL, "source_type": "official_academic_schedule"},
        ]))
        document = session.query(db.SourceDocument).filter_by(dataset="schedule").one()
        assert document.source_url == SCHEDULE_URL
        assert document.source_type == "academic_schedule"
        assert "source_url" not in json.loads(document.normalized_payload_json)
        frame = ingest.load_canonical_source_frame(session, "schedule", include_source_url=True)
        assert frame.iloc[0]["source_url"] == SCHEDULE_URL
        assert ingest.build_schedule_chunks(frame).iloc[0]["url"] == SCHEDULE_URL

    rows = rag_service._load_schedule_rows_for_direct_answer()
    result = answer_schedule_when("개강 언제야?", rows, date(2026, 8, 1))
    assert result is not None
    assert result.sources[0]["url"] == SCHEDULE_URL
    assert rag_service._direct_answer_transport(result)[2][0].url == SCHEDULE_URL


def test_schedule_unknown_or_nonofficial_provenance_has_no_url(source_db):
    with source_db() as session:
        ingest._store_schedule_source_documents(session, pd.DataFrame([
            {"title": "개강", "start_date": "2026-09-01", "end_date": "2026-09-01"},
            {"title": "종강", "start_date": "2026-12-20", "end_date": "2026-12-20", "source_url": "https://example.com/schedule", "source_type": "official_academic_schedule"},
        ]))
    assert all(row.url is None for row in rag_service._load_schedule_rows_for_direct_answer())


def test_canonical_schedule_loader_keeps_wise_identity_for_direct_answer(source_db):
    with source_db() as session:
        ingest._store_schedule_source_documents(session, pd.DataFrame([
            {
                "title": "WISE캠퍼스 정규학기 학점교류 신청",
                "department": "WISE캠퍼스/학사지원팀",
                "start_date": "2026-10-22", "end_date": "2026-10-23",
            },
            {
                "title": "서울캠퍼스 중간시험",
                "start_date": "2026-10-20", "end_date": "2026-10-21",
                "source_url": SCHEDULE_URL, "source_type": "official_academic_schedule",
            },
        ]))

    result = answer_schedule_when(
        "이번 달 학사일정 알려줘",
        rag_service._load_schedule_rows_for_direct_answer(),
        date(2026, 10, 2),
    )

    assert result is not None and result.kind == "schedule_window"
    assert "WISE" not in result.answer
    assert [source["metadata"]["campus_scope"] for source in result.sources] == ["seoul"]
    assert result.sources[0]["url"] == SCHEDULE_URL


def test_schedule_refresh_keeps_historical_url_metadata():
    historical = pd.DataFrame([{
        "학년도": "2025", "내용": "개강", "start": "2025-03-01", "end": "2025-03-01",
        "source_url": SCHEDULE_URL, "source_type": "official_academic_schedule",
    }])
    incoming = pd.DataFrame([{
        "학년도": "2026", "내용": "개강", "start": "2026-03-01", "end": "2026-03-01",
        "source_url": SCHEDULE_URL, "source_type": "official_academic_schedule",
    }])
    merged = _merge_schedule_snapshots(historical, incoming)
    assert merged["source_url"].tolist() == [SCHEDULE_URL, SCHEDULE_URL]
    assert merged["source_type"].tolist() == ["official_academic_schedule"] * 2


def test_meal_urls_are_row_specific_and_unverified_rows_remain_unlinked(source_db):
    rows = pd.DataFrame([
        {"date": "2026-09-01", "weekday": "화", "restaurant": "상록원3층식당", "menu_text": "[중식] 비빔밥", "is_closed": False, "source_url": COOP_URL, "source_type": "official_coop_meal"},
        {"date": "2026-09-01", "weekday": "화", "restaurant": "경영관 D-Flex식당", "menu_text": "[중식] 파스타", "is_closed": False, "source_url": DFLEX_URL, "source_type": "official_dflex_pdf_meal"},
        {"date": "2026-09-02", "weekday": "수", "restaurant": "경영관 D-Flex식당", "menu_text": "[중식] 국밥", "is_closed": False, "source_url": COOP_URL, "source_type": "official_dflex_pdf_meal"},
        {"date": "2026-09-02", "weekday": "수", "restaurant": "상록원3층식당", "menu_text": "[중식] 국수", "is_closed": False, "source_url": "https://example.com/menu", "source_type": "official_coop_meal"},
        {"date": "2026-09-02", "weekday": "수", "restaurant": "상록원2층식당", "menu_text": "[중식] 국수", "is_closed": False, "source_url": COOP_URL, "source_type": "official_coop_meal"},
    ])
    ingest.store_meals_in_db(rows)
    with source_db() as session:
        documents = session.query(db.SourceDocument).filter_by(dataset="meals").all()
        assert all("source_url" not in json.loads(doc.normalized_payload_json) for doc in documents)

    frame = ingest.load_meals_from_db(include_source_url=True)
    by_key = {(row.date, row.restaurant): row.source_url for row in frame.itertuples()}
    assert by_key[("2026-09-01", "상록원3층식당")] == COOP_URL
    assert by_key[("2026-09-01", "경영관 D-Flex식당")] == DFLEX_URL
    assert by_key[("2026-09-02", "경영관 D-Flex식당")] == ""
    assert by_key[("2026-09-02", "상록원3층식당")] == ""
    assert by_key[("2026-09-02", "상록원2층식당")] == ""
    chunk_urls = dict(zip(ingest.build_meal_chunks(frame)["doc_id"], ingest.build_meal_chunks(frame)["url"]))
    assert chunk_urls["meals:2026-09-01:경영관 D-Flex식당"] == DFLEX_URL
    assert chunk_urls["meals:2026-09-02:경영관 D-Flex식당"] == ""

    direct_rows = rag_service._load_meal_rows_for_direct_answer()
    answer = answer_meal("오늘 D-Flex 학식 뭐야?", direct_rows, date(2026, 9, 1), split_meal_corners)
    assert answer is not None
    assert answer.sources[0]["url"] == DFLEX_URL
    assert rag_service._direct_answer_transport(answer)[2][0].url == DFLEX_URL


def test_old_meal_url_written_without_row_provenance_is_not_exposed(source_db):
    payload = {"date": "2026-09-01", "weekday": "화", "restaurant": "경영관 D-Flex식당", "menu_text": "[중식] 파스타", "is_closed": False}
    with source_db() as session:
        session.add(db.SourceDocument(
            dataset="meals", source_type="html_meal", source_id="2026-09-01:경영관 D-Flex식당",
            document_key="meals:2026-09-01:경영관 D-Flex식당", source_url=COOP_URL,
            normalized_payload_json=json.dumps(payload), status="active",
        ))
        session.commit()
    assert ingest.load_meals_from_db().iloc[0]["source_url"] == ""
    assert rag_service._load_meal_rows_for_direct_answer()[0].source_url is None


def test_meal_crawlers_attach_the_fetched_page_or_pdf_url(monkeypatch):
    day = date(2026, 9, 1)
    monkeypatch.setattr(dongguk_meals, "fetch_day_html", lambda *_args, **_kwargs: "<html />")
    monkeypatch.setattr(dongguk_meals, "parse_day_menus", lambda *_args: [{
        "date": day.isoformat(), "weekday": "화", "restaurant": "상록원3층식당",
        "menu_text": "[중식] 비빔밥", "is_closed": False,
    }])
    coop = dongguk_meals.crawl_meals(days_back=0, days_ahead=0, today=day, delay=0, include_dflex=False)
    assert coop.iloc[0]["source_url"] == dongguk_meals._meal_page_url(day)
    assert coop.iloc[0]["source_type"] == "official_coop_meal"

    monkeypatch.setattr(dongguk_notices, "fetch_notice_list", lambda *_args, **_kwargs: [{"article_id": 42, "posted_at": day}])
    monkeypatch.setattr(dongguk_notices, "fetch_notice_detail", lambda *_args, **_kwargs: {
        "posted_at": day, "attachments": [{"name": "menu.pdf", "url": DFLEX_URL}],
    })
    monkeypatch.setattr(dongguk_meals, "_get_with_retry", lambda *_args, **_kwargs: type("R", (), {"content": b"pdf"})())
    monkeypatch.setattr(dongguk_meals, "parse_dflex_pdf", lambda *_args: [{
        "date": day.isoformat(), "weekday": "화", "restaurant": "경영관 D-Flex식당",
        "menu_text": "[중식] 파스타", "is_closed": False,
    }])
    dflex = dongguk_meals.crawl_dflex_meals(max_posts=1, delay=0)
    assert dflex[0]["source_url"] == DFLEX_URL
    assert dflex[0]["source_type"] == "official_dflex_pdf_meal"


def test_catch_up_merge_preserves_each_existing_source_url():
    active = pd.DataFrame([{
        "date": "2026-09-01", "weekday": "화", "restaurant": "경영관 D-Flex식당",
        "menu_text": "[중식] 파스타", "is_closed": False, "source_url": DFLEX_URL, "source_type": "official_dflex_pdf_meal",
    }])
    incoming = pd.DataFrame([{
        "date": "2026-09-02", "weekday": "수", "restaurant": "상록원3층식당",
        "menu_text": "[중식] 비빔밥", "is_closed": False, "source_url": COOP_URL_NEXT, "source_type": "official_coop_meal",
    }])
    merged = merge_active_meals(active, incoming)
    assert dict(zip(merged["restaurant"], merged["source_url"])) == {
        "경영관 D-Flex식당": DFLEX_URL,
        "상록원3층식당": COOP_URL_NEXT,
    }
