from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, IngestionRun, SourceDocument
from src.services.ingestion_freshness import build_ingestion_freshness_report


KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=KST)


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _document(dataset: str, collected_at: datetime) -> SourceDocument:
    return SourceDocument(
        dataset=dataset,
        source_type="fixture",
        source_id="one",
        document_key=f"{dataset}:one",
        status="active",
        collected_at=collected_at,
    )


def test_fresh_success_is_healthy(monkeypatch):
    monkeypatch.setenv("RAG_MEALS_FRESHNESS_MAX_HOURS", "36")
    session = _session()
    try:
        session.add(_document("meals", NOW - timedelta(hours=3)))
        session.add(IngestionRun(
            dataset="meals",
            status="success",
            outcome_code="success",
            started_at=NOW - timedelta(hours=3),
            finished_at=NOW - timedelta(hours=3),
            documents_seen=10,
        ))
        session.commit()

        report = build_ingestion_freshness_report(
            session,
            datasets=("meals",),
            now=NOW,
        )

        item = report["datasets"][0]
        assert report["gate_passed"] is True
        assert item["state"] == "healthy"
        assert item["fresh"] is True
        assert item["consecutive_unsuccessful_runs"] == 0
    finally:
        session.close()


def test_two_empty_runs_raise_warning_without_discarding_fresh_snapshot(monkeypatch):
    monkeypatch.setenv("RAG_MEALS_FRESHNESS_MAX_HOURS", "36")
    session = _session()
    try:
        session.add(_document("meals", NOW - timedelta(hours=8)))
        session.add_all([
            IngestionRun(
                dataset="meals",
                status="success",
                outcome_code="success",
                started_at=NOW - timedelta(hours=8),
                finished_at=NOW - timedelta(hours=8),
            ),
            IngestionRun(
                dataset="meals",
                status="partial",
                outcome_code="empty_source",
                diagnostics_json=json.dumps({"requested_days": 14}),
                started_at=NOW - timedelta(hours=4),
                finished_at=NOW - timedelta(hours=4),
            ),
            IngestionRun(
                dataset="meals",
                status="partial",
                outcome_code="source_schema_changed",
                started_at=NOW - timedelta(hours=1),
                finished_at=NOW - timedelta(hours=1),
            ),
        ])
        session.commit()

        item = build_ingestion_freshness_report(
            session,
            datasets=("meals",),
            now=NOW,
        )["datasets"][0]

        assert item["fresh"] is True
        assert item["state"] == "warning"
        assert item["consecutive_unsuccessful_runs"] == 2
        assert item["latest_run"]["outcome_code"] == "source_schema_changed"
    finally:
        session.close()


def test_old_snapshot_is_stale_even_when_latest_run_has_a_different_failure(monkeypatch):
    monkeypatch.setenv("RAG_MEALS_FRESHNESS_MAX_HOURS", "36")
    session = _session()
    try:
        session.add(_document("meals", NOW - timedelta(days=4)))
        session.add_all([
            IngestionRun(
                dataset="meals",
                status="success",
                outcome_code="success",
                started_at=NOW - timedelta(days=4),
                finished_at=NOW - timedelta(days=4),
            ),
            IngestionRun(
                dataset="meals",
                status="partial",
                outcome_code="upstream_unreachable",
                started_at=NOW - timedelta(hours=1),
                finished_at=NOW - timedelta(hours=1),
            ),
        ])
        session.commit()

        report = build_ingestion_freshness_report(
            session,
            datasets=("meals",),
            now=NOW,
        )

        assert report["gate_passed"] is False
        assert report["stale_datasets"] == ["meals"]
        assert report["datasets"][0]["state"] == "stale"
    finally:
        session.close()
