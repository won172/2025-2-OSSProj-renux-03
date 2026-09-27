from __future__ import annotations

from datetime import timedelta

from api import rag_service
from src.services.direct_answer import DirectAnswer


def test_current_meal_direct_answer_fails_closed_when_snapshot_is_stale(monkeypatch):
    monkeypatch.setattr(
        rag_service,
        "build_ingestion_freshness_report",
        lambda *_args, **_kwargs: {"datasets": [{"fresh": False, "state": "stale"}]},
    )
    monkeypatch.setattr(
        rag_service,
        "_load_meal_rows_for_direct_answer",
        lambda: (_ for _ in ()).throw(AssertionError("stale rows must not be loaded")),
    )

    result = rag_service._try_direct_answer(
        "오늘 학식 뭐야?",
        rag_service.kst_now().date(),
    )

    assert result is not None
    assert result.kind == "meal_stale"
    assert result.sources == []
    assert "최신 학식 데이터를 확인하지 못해" in result.answer


def test_historical_as_of_does_not_use_live_freshness_gate(monkeypatch):
    called = False

    def freshness(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"datasets": [{"fresh": False, "state": "stale"}]}

    monkeypatch.setattr(rag_service, "build_ingestion_freshness_report", freshness)
    monkeypatch.setattr(rag_service, "_load_meal_rows_for_direct_answer", lambda: [])

    result = rag_service._try_direct_answer(
        "오늘 학식 뭐야?",
        rag_service.kst_now().date() - timedelta(days=30),
    )

    assert result is None
    assert called is False


def test_fresh_schedule_continues_to_structured_answer(monkeypatch):
    monkeypatch.setattr(
        rag_service,
        "build_ingestion_freshness_report",
        lambda *_args, **_kwargs: {"datasets": [{"fresh": True, "state": "healthy"}]},
    )
    monkeypatch.setattr(rag_service, "_load_schedule_rows_for_direct_answer", lambda: [])
    monkeypatch.setattr(
        rag_service,
        "answer_schedule_when",
        lambda *_args: DirectAnswer(answer="일정", kind="schedule"),
    )

    result = rag_service._try_direct_answer(
        "수강신청 언제야?",
        rag_service.kst_now().date(),
    )

    assert result is not None
    assert result.kind == "schedule"


def test_repeated_refresh_failures_block_current_answer_even_within_age_budget(monkeypatch):
    monkeypatch.setattr(
        rag_service,
        "build_ingestion_freshness_report",
        lambda *_args, **_kwargs: {"datasets": [{"fresh": True, "state": "warning"}]},
    )
    monkeypatch.setattr(
        rag_service,
        "_load_meal_rows_for_direct_answer",
        lambda: (_ for _ in ()).throw(AssertionError("warning rows must not be loaded")),
    )

    result = rag_service._try_direct_answer(
        "오늘 학식 뭐야?",
        rag_service.kst_now().date(),
    )

    assert result is not None
    assert result.kind == "meal_stale"
