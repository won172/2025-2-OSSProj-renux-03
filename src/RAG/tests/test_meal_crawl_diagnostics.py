from __future__ import annotations

from datetime import date

import requests
import pytest

from src.crawlers import dongguk_meals, dongguk_notices


def test_meal_crawl_reports_upstream_failures_without_exposing_messages(monkeypatch):
    monkeypatch.setattr(
        dongguk_meals,
        "fetch_day_html",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.Timeout("secret detail")),
    )

    frame = dongguk_meals.crawl_meals(
        days_back=0,
        days_ahead=0,
        delay=0,
        today=date(2026, 9, 21),
        include_dflex=False,
    )

    diagnostics = frame.attrs["crawl_diagnostics"]
    assert frame.empty
    assert diagnostics["requested_days"] == 1
    assert diagnostics["fetched_days"] == 0
    assert diagnostics["fetch_failed_days"] == 1
    assert diagnostics["failure_types"] == {"Timeout": 1}
    assert "secret detail" not in str(diagnostics)


def test_meal_crawl_distinguishes_reachable_but_unparseable_pages(monkeypatch):
    monkeypatch.setattr(dongguk_meals, "fetch_day_html", lambda *_args, **_kwargs: "<html></html>")
    monkeypatch.setattr(dongguk_meals, "parse_day_menus", lambda *_args: [])

    frame = dongguk_meals.crawl_meals(
        days_back=0,
        days_ahead=0,
        delay=0,
        today=date(2026, 9, 21),
        include_dflex=False,
    )

    diagnostics = frame.attrs["crawl_diagnostics"]
    assert diagnostics["fetched_days"] == 1
    assert diagnostics["fetch_failed_days"] == 0
    assert diagnostics["parsed_days_with_rows"] == 0
    assert diagnostics["parse_empty_days"] == 1


def test_dflex_range_pages_back_until_all_weekdays_are_covered(monkeypatch):
    seen_pages: list[int] = []

    def list_page(_board, page, *, timeout, retries):
        seen_pages.append(page)
        day = {1: date(2026, 9, 2), 2: date(2026, 9, 1)}[page]
        posts = [{"article_id": page, "posted_at": day}]
        if page == 1:
            posts.insert(0, {"article_id": 99, "posted_at": date(2026, 3, 13), "is_pinned": True})
        return posts

    def detail(_board, article_id, *, timeout, retries):
        assert article_id != 99, "unrelated old PDF must not be inspected"
        return {
            "posted_at": date(2026, 9, 3 - article_id),
            "attachments": [{"name": "menu.pdf", "url": "https://example.test/menu.pdf"}],
        }

    class PdfResponse:
        content = b"pdf"

    monkeypatch.setattr(dongguk_notices, "fetch_notice_list", list_page)
    monkeypatch.setattr(dongguk_notices, "fetch_notice_detail", detail)
    monkeypatch.setattr(dongguk_meals, "_get_with_retry", lambda *_args, **_kwargs: PdfResponse())
    monkeypatch.setattr(dongguk_meals, "inspect_dflex_pdf_dates", lambda _content, ref: {ref.isoformat(): True})
    monkeypatch.setattr(
        dongguk_meals,
        "parse_dflex_pdf",
        lambda _content, ref: [{
            "date": ref.isoformat(), "weekday": "화", "restaurant": dongguk_meals.DFLEX_RESTAURANT,
            "menu_text": "메뉴", "is_closed": False,
        }],
    )

    rows = dongguk_meals.crawl_dflex_meals_range(
        date(2026, 9, 1), date(2026, 9, 2), delay=0,
    )
    assert seen_pages == [1, 2]
    assert {row["date"] for row in rows.records} == {"2026-09-01", "2026-09-02"}


def test_dflex_range_stops_after_old_pages_without_parsing_their_pdfs(monkeypatch):
    pages: list[int] = []

    def list_page(_board, page, *, timeout, retries):
        pages.append(page)
        return [{"article_id": page, "posted_at": date(2026, 3, 13)}]

    monkeypatch.setattr(dongguk_notices, "fetch_notice_list", list_page)
    monkeypatch.setattr(
        dongguk_notices,
        "fetch_notice_detail",
        lambda *_args, **_kwargs: pytest.fail("old post detail fetched"),
    )
    with pytest.raises(ValueError, match="historical coverage incomplete"):
        dongguk_meals.crawl_dflex_meals_range(
            date(2026, 9, 21), date(2026, 9, 22), delay=0,
        )
    assert pages == [1, 2]


def test_dflex_range_fails_closed_on_older_page_error(monkeypatch):
    def list_page(_board, page, *, timeout, retries):
        if page == 2:
            raise requests.Timeout("private detail")
        return [{"article_id": 1, "posted_at": date(2026, 9, 2)}]

    monkeypatch.setattr(dongguk_notices, "fetch_notice_list", list_page)
    monkeypatch.setattr(dongguk_notices, "fetch_notice_detail", lambda *_args, **_kwargs: {
        "posted_at": date(2026, 9, 2),
        "attachments": [{"name": "menu.pdf", "url": "https://example.test/menu.pdf"}],
    })
    monkeypatch.setattr(dongguk_meals, "_get_with_retry", lambda *_args, **_kwargs: type("R", (), {"content": b"pdf"})())
    monkeypatch.setattr(dongguk_meals, "inspect_dflex_pdf_dates", lambda _content, ref: {ref.isoformat(): True})
    monkeypatch.setattr(dongguk_meals, "parse_dflex_pdf", lambda _content, ref: [{
        "date": ref.isoformat(), "restaurant": dongguk_meals.DFLEX_RESTAURANT,
    }])

    with pytest.raises(requests.Timeout):
        dongguk_meals.crawl_dflex_meals_range(
            date(2026, 9, 1), date(2026, 9, 2), delay=0,
        )


@pytest.mark.parametrize("stage", ["detail", "pdf", "parse"])
def test_dflex_range_fails_closed_on_detail_or_pdf_error(monkeypatch, stage):
    monkeypatch.setattr(dongguk_notices, "fetch_notice_list", lambda *_args, **_kwargs: [{
        "article_id": 1, "posted_at": date(2026, 9, 1),
    }])

    def detail(*_args, **_kwargs):
        if stage == "detail":
            raise requests.Timeout("detail unavailable")
        return {
            "posted_at": date(2026, 9, 1),
            "attachments": [{"name": "menu.pdf", "url": "https://example.test/menu.pdf"}],
        }

    def pdf(*_args, **_kwargs):
        if stage == "pdf":
            raise requests.Timeout("PDF unavailable")
        return type("R", (), {"content": b"pdf"})()

    monkeypatch.setattr(dongguk_notices, "fetch_notice_detail", detail)
    monkeypatch.setattr(dongguk_meals, "_get_with_retry", pdf)
    monkeypatch.setattr(dongguk_meals, "inspect_dflex_pdf_dates", lambda _content, ref: {ref.isoformat(): True})
    monkeypatch.setattr(dongguk_meals, "parse_dflex_pdf", lambda _content, _ref: [])

    with pytest.raises((requests.Timeout, ValueError)):
        dongguk_meals.crawl_dflex_meals_range(
            date(2026, 9, 1), date(2026, 9, 1), delay=0,
        )


def test_dflex_pdf_header_with_blank_menu_column_is_published_but_not_a_meal(monkeypatch):
    import pdfplumber

    class FakePage:
        def extract_tables(self):
            return [[
                ["구분", "09월 24일", "09월 25일"],
                ["중식", "확인된 메뉴", None],
                ["석식", "", ""],
            ]]

    class FakePdf:
        pages = [FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(pdfplumber, "open", lambda _stream: FakePdf())
    assert dongguk_meals.inspect_dflex_pdf_dates(b"pdf", date(2026, 9, 18)) == {
        "2026-09-24": True,
        "2026-09-25": False,
    }


def test_dflex_range_accepts_blank_published_date_without_creating_row(monkeypatch):
    monkeypatch.setattr(dongguk_notices, "fetch_notice_list", lambda *_args, **_kwargs: [{
        "article_id": 1, "posted_at": date(2026, 9, 18),
    }])
    monkeypatch.setattr(dongguk_notices, "fetch_notice_detail", lambda *_args, **_kwargs: {
        "posted_at": date(2026, 9, 18),
        "attachments": [{"name": "menu.pdf", "url": "https://example.test/menu.pdf"}],
    })
    monkeypatch.setattr(dongguk_meals, "_get_with_retry", lambda *_args, **_kwargs: type("R", (), {"content": b"pdf"})())
    monkeypatch.setattr(dongguk_meals, "inspect_dflex_pdf_dates", lambda *_args: {
        "2026-09-24": True, "2026-09-25": False,
    })
    monkeypatch.setattr(dongguk_meals, "parse_dflex_pdf", lambda *_args: [{
        "date": "2026-09-24", "weekday": "목", "restaurant": dongguk_meals.DFLEX_RESTAURANT,
        "menu_text": "확인된 메뉴", "is_closed": False,
    }])
    result = dongguk_meals.crawl_dflex_meals_range(date(2026, 9, 24), date(2026, 9, 25), delay=0)
    assert [row["date"] for row in result.records] == ["2026-09-24"]
    assert result.published_dates == frozenset({"2026-09-24", "2026-09-25"})
    assert result.blank_dates == frozenset({"2026-09-25"})
