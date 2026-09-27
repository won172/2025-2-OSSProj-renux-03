from __future__ import annotations

import sys
from types import ModuleType

import pandas as pd

from src.services import scheduler


def _install_modules(monkeypatch, frame: pd.DataFrame, stage_result: dict):
    crawler = ModuleType("src.crawlers.dongguk_staff_contacts")
    crawler.crawl_staff_contacts = lambda **_kwargs: frame
    service = ModuleType("src.services.staff_refresh")
    service.stage_staff_refresh = lambda _frame: stage_result
    monkeypatch.setitem(sys.modules, crawler.__name__, crawler)
    monkeypatch.setitem(sys.modules, service.__name__, service)


def test_complete_staff_change_is_staged_for_review(monkeypatch):
    frame = pd.DataFrame([{"성명": "김**"}])
    frame.attrs["crawl_diagnostics"] = {
        "requested_departments": 1,
        "fetched_departments": 1,
        "failed_departments": 0,
    }
    _install_modules(
        monkeypatch,
        frame,
        {
            "current_rows": 1,
            "incoming_rows": 1,
            "added": 0,
            "removed": 0,
            "contact_changed": 1,
            "pending_item_id": 42,
            "snapshot_sha256": "abc",
        },
    )
    finished: list[dict] = []
    monkeypatch.setattr(scheduler, "_start_ingestion_run", lambda _dataset: 7)
    monkeypatch.setattr(scheduler, "_finish_ingestion_run", lambda _run_id, **kwargs: finished.append(kwargs))
    monkeypatch.setattr(scheduler, "_record_run", lambda *_args, **_kwargs: None)

    scheduler.refresh_staff_job()

    assert finished[0]["status"] == "pending_review"
    assert finished[0]["outcome_code"] == "pending_review"
    assert finished[0]["diagnostics"]["review"]["pending_item_id"] == 42


def test_partial_staff_crawl_never_creates_destructive_review_candidate(monkeypatch):
    frame = pd.DataFrame([{"성명": "김**"}])
    frame.attrs["crawl_diagnostics"] = {
        "requested_departments": 2,
        "fetched_departments": 1,
        "failed_departments": 1,
        "failed_department_ids": ["dept-2"],
    }
    stage_called = False

    def stage(_frame):
        nonlocal stage_called
        stage_called = True
        return {}

    crawler = ModuleType("src.crawlers.dongguk_staff_contacts")
    crawler.crawl_staff_contacts = lambda **_kwargs: frame
    service = ModuleType("src.services.staff_refresh")
    service.stage_staff_refresh = stage
    monkeypatch.setitem(sys.modules, crawler.__name__, crawler)
    monkeypatch.setitem(sys.modules, service.__name__, service)
    finished: list[dict] = []
    monkeypatch.setattr(scheduler, "_start_ingestion_run", lambda _dataset: 8)
    monkeypatch.setattr(scheduler, "_finish_ingestion_run", lambda _run_id, **kwargs: finished.append(kwargs))
    monkeypatch.setattr(scheduler, "_record_run", lambda *_args, **_kwargs: None)

    scheduler.refresh_staff_job()

    assert stage_called is False
    assert finished[0]["status"] == "partial"
    assert finished[0]["outcome_code"] == "partial_source"
