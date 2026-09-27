"""Scheduler refreshes must invalidate the same process-local RAG cache."""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services import scheduler  # noqa: E402


def test_scheduler_runtime_refresh_delegates_to_rag_service(monkeypatch):
    calls: list[list[str]] = []

    class FakeRagService:
        @staticmethod
        def refresh_runtime_dataset_state(targets):
            calls.append(targets)
            return {"counts": {"notices": 7}, "dense_counts": {"notices": 7}}

    monkeypatch.setitem(sys.modules, "api.rag_service", FakeRagService)

    scheduler._refresh_runtime_dataset_state("notices")

    assert calls == [["notices"]]


def test_course_refresh_recrawls_before_forced_csv_ingest(monkeypatch):
    calls: list[object] = []
    crawler = ModuleType("src.crawlers.dongguk_department_curriculum_content")
    crawler.main = lambda: calls.append("crawl")
    pipeline = ModuleType("src.pipelines.ingest")

    def fake_ingest_courses(*, refresh_from_csv=False):
        calls.append(("ingest", refresh_from_csv))
        return pd.DataFrame([{"chunk": 1}]), None, None

    pipeline.ingest_courses = fake_ingest_courses
    monkeypatch.setitem(sys.modules, crawler.__name__, crawler)
    monkeypatch.setitem(sys.modules, pipeline.__name__, pipeline)
    monkeypatch.setattr(
        scheduler,
        "_refresh_runtime_dataset_state",
        lambda dataset: calls.append(("refresh", dataset)),
    )
    monkeypatch.setattr(scheduler, "_start_ingestion_run", lambda _dataset: 1)
    monkeypatch.setattr(scheduler, "_finish_ingestion_run", lambda *_args, **_kwargs: None)

    scheduler.refresh_courses_job()

    assert calls == ["crawl", ("ingest", True), ("refresh", "courses")]
