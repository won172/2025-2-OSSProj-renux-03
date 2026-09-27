from __future__ import annotations

from datetime import date

import requests

from src.crawlers import dongguk_meals


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
