"""Scheduler crawls and the compose readiness probe must have bounded requests."""
from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.crawlers import dongguk_meals, dongguk_notices  # noqa: E402
from src.services import scheduler  # noqa: E402


def test_notice_crawl_plumbs_request_limits_to_list_and_detail(monkeypatch):
    calls: list[tuple[str, float, int]] = []

    def fake_fetch_notice_list(
        board_code: str,
        page: int = 1,
        *,
        timeout: float,
        retries: int,
    ):
        calls.append(("list", timeout, retries))
        if page != 1:
            return []
        return [
            {
                "article_id": 1,
                "title": "공지",
                "category": "일반",
                "posted_at": date(2026, 7, 29),
                "views": 1,
                "is_pinned": False,
            }
        ]

    def fake_fetch_notice_detail(
        board_code: str,
        article_id: int,
        *,
        timeout: float,
        retries: int,
    ):
        calls.append(("detail", timeout, retries))
        return {
            "posted_at": date(2026, 7, 29),
            "views": 1,
            "detail_url": f"https://example.test/{board_code}/{article_id}",
            "content_html": "",
            "content_text": "본문",
            "attachments": [],
        }

    monkeypatch.setattr(dongguk_notices, "fetch_notice_list", fake_fetch_notice_list)
    monkeypatch.setattr(dongguk_notices, "fetch_notice_detail", fake_fetch_notice_detail)

    dongguk_notices.collect_board(
        "일반공지",
        "GENERALNOTICES",
        max_pages=1,
        delay=0,
        request_timeout=7.5,
        request_retries=2,
    )

    assert calls == [("list", 7.5, 2), ("detail", 7.5, 2)]


def test_meals_crawl_plumbs_request_limits_to_daily_and_dflex_fetches(monkeypatch):
    calls: list[tuple[str, float, int]] = []

    def fake_fetch_day_html(target: date, *, timeout: float, retries: int) -> str:
        calls.append(("day", timeout, retries))
        return "<html></html>"

    def fake_crawl_dflex_meals(
        *,
        delay: float,
        request_timeout: float,
        request_retries: int,
    ):
        calls.append(("dflex", request_timeout, request_retries))
        return []

    monkeypatch.setattr(dongguk_meals, "fetch_day_html", fake_fetch_day_html)
    monkeypatch.setattr(dongguk_meals, "parse_day_menus", lambda _html, _target: [])
    monkeypatch.setattr(dongguk_meals, "crawl_dflex_meals", fake_crawl_dflex_meals)

    dongguk_meals.crawl_meals(
        days_back=0,
        days_ahead=0,
        delay=0,
        today=date(2026, 7, 29),
        request_timeout=6.0,
        request_retries=2,
    )

    assert calls == [("day", 6.0, 2), ("dflex", 6.0, 2)]


def test_meal_daily_fetch_retries_with_the_explicit_timeout(monkeypatch):
    attempts: list[float] = []

    class FakeResponse:
        apparent_encoding = "utf-8"
        text = "<html>meal</html>"

        @staticmethod
        def raise_for_status() -> None:
            return None

    def fake_get(_url, *, params, headers, timeout):
        attempts.append(timeout)
        if len(attempts) == 1:
            raise dongguk_meals.requests.exceptions.Timeout("slow response")
        return FakeResponse()

    monkeypatch.setattr(dongguk_meals.requests, "get", fake_get)
    monkeypatch.setattr(dongguk_meals.time, "sleep", lambda _seconds: None)

    html = dongguk_meals.fetch_day_html(
        date(2026, 7, 29),
        timeout=3.0,
        retries=2,
    )

    assert html == "<html>meal</html>"
    assert attempts == [3.0, 3.0]


def test_dflex_fetches_share_the_explicit_request_limits(monkeypatch):
    calls: list[tuple[str, float, int]] = []

    def fake_fetch_notice_list(_board_code, page=1, *, timeout, retries):
        calls.append(("list", timeout, retries))
        return [{"article_id": 1, "posted_at": date(2026, 7, 29)}]

    def fake_fetch_notice_detail(_board_code, _article_id, *, timeout, retries):
        calls.append(("detail", timeout, retries))
        return {
            "posted_at": date(2026, 7, 29),
            "attachments": [{"name": "menu.pdf", "url": "https://example.test/menu.pdf"}],
        }

    class FakePdfResponse:
        content = b"pdf"

    def fake_get_with_retry(_url, *, params=None, timeout, retries):
        calls.append(("pdf", timeout, retries))
        return FakePdfResponse()

    monkeypatch.setattr(dongguk_notices, "fetch_notice_list", fake_fetch_notice_list)
    monkeypatch.setattr(dongguk_notices, "fetch_notice_detail", fake_fetch_notice_detail)
    monkeypatch.setattr(dongguk_meals, "_get_with_retry", fake_get_with_retry)
    monkeypatch.setattr(dongguk_meals, "parse_dflex_pdf", lambda _content, _date: [])

    dongguk_meals.crawl_dflex_meals(
        max_posts=1,
        delay=0,
        request_timeout=5.0,
        request_retries=2,
    )

    assert calls == [
        ("list", 5.0, 2),
        ("detail", 5.0, 2),
        ("pdf", 5.0, 2),
    ]


def test_scheduler_jobs_pass_and_log_configured_request_limits(monkeypatch, caplog):
    calls: dict[str, dict] = {}

    def fake_crawl_notices(**kwargs):
        calls["notices"] = kwargs
        return pd.DataFrame([{"notice": 1}])

    def fake_crawl_meals(**kwargs):
        calls["meals"] = kwargs
        return pd.DataFrame([{"meal": 1}])

    notices_crawler = ModuleType("src.crawlers.dongguk_notices")
    notices_crawler.crawl_notices = fake_crawl_notices
    notices_pipeline = ModuleType("src.pipelines.notices_sync")
    notices_pipeline.load_known_article_ids_by_board = lambda: {"일반공지": {1}}
    notices_pipeline.record_notice_ingestion_failure = lambda *_args, **_kwargs: 1
    notices_pipeline.sync_notices = lambda *_args, **_kwargs: {}

    meals_crawler = ModuleType("src.crawlers.dongguk_meals")
    meals_crawler.crawl_meals = fake_crawl_meals
    meals_pipeline = ModuleType("src.pipelines.ingest")
    meals_pipeline.ingest_meals = lambda _df: (pd.DataFrame([{"chunk": 1}]), None, None)

    monkeypatch.setitem(sys.modules, notices_crawler.__name__, notices_crawler)
    monkeypatch.setitem(sys.modules, notices_pipeline.__name__, notices_pipeline)
    monkeypatch.setitem(sys.modules, meals_crawler.__name__, meals_crawler)
    monkeypatch.setitem(sys.modules, meals_pipeline.__name__, meals_pipeline)
    monkeypatch.setattr(scheduler, "RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS", 8.0)
    monkeypatch.setattr(scheduler, "RAG_SCHEDULER_REQUEST_RETRIES", 2)
    monkeypatch.setattr(scheduler, "_refresh_runtime_dataset_state", lambda _dataset: None)

    with caplog.at_level(logging.INFO, logger=scheduler.__name__):
        scheduler.refresh_notices_job()
        scheduler.refresh_meals_job()

    assert calls["notices"]["request_timeout"] == 8.0
    assert calls["notices"]["request_retries"] == 2
    assert calls["meals"]["request_timeout"] == 8.0
    assert calls["meals"]["request_retries"] == 2
    assert caplog.text.count("request_timeout=8.0s request_retries=2") == 2


def test_rag_service_healthcheck_fallbacks_have_hard_timeouts():
    compose_path = Path(__file__).resolve().parents[2] / "RenuxServer" / "docker-compose.yml"
    compose = compose_path.read_text(encoding="utf-8")
    rag_service = compose.split("    rag-service:", 1)[1].split("    ollama:", 1)[0]

    assert "curl --connect-timeout 2 --max-time 4" in rag_service
    assert 'urlopen(\\"http://localhost:8000/ready\\", timeout=4)' in rag_service
    assert "            timeout: 10s" in rag_service


def test_scheduler_sends_content_free_alerts_only_for_partial_or_failed(monkeypatch):
    calls: list[dict] = []

    class FakeResponse:
        @staticmethod
        def raise_for_status() -> None:
            return None

    def fake_post(url, *, json, timeout):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(scheduler, "RAG_SCHEDULER_ALERT_WEBHOOK_URL", "https://alerts.example.test")
    monkeypatch.setattr(scheduler, "RAG_SCHEDULER_ALERT_TIMEOUT_SECONDS", 3.0)
    monkeypatch.setattr(scheduler.httpx, "post", fake_post)

    scheduler._record_run("refresh_notices", "ok", "신규 0")
    scheduler._record_run("refresh_notices", "partial", "미완료 게시판 1")
    scheduler._record_run("refresh_meals", "failed", "upstream timeout")

    assert len(calls) == 2
    assert [call["json"]["status"] for call in calls] == ["partial", "failed"]
    assert all(call["timeout"] == 3.0 for call in calls)
    assert all(call["json"]["service"] == "dongttok-rag" for call in calls)
    assert all("document" not in call["json"] for call in calls)
    assert all("query" not in call["json"] for call in calls)


def test_scheduler_alert_failure_never_breaks_run_record(monkeypatch, caplog):
    monkeypatch.setattr(scheduler, "RAG_SCHEDULER_ALERT_WEBHOOK_URL", "https://alerts.example.test")
    monkeypatch.setattr(
        scheduler.httpx,
        "post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("webhook unavailable")),
    )

    with caplog.at_level(logging.WARNING, logger=scheduler.__name__):
        scheduler._record_run("refresh_notices", "failed", "crawl failed")

    status = scheduler.get_scheduler_status()
    notice_job = next(job for job in status["jobs"] if job["id"] == "refresh_notices")
    assert notice_job["last_status"] == "failed"
    assert "운영 경보 전송 실패" in caplog.text
