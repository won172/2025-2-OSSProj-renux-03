"""Persisted ingestion health and dataset freshness reporting.

The scheduler's in-memory status disappears on restart.  This module derives
the operational state only from ``ingestion_runs`` and canonical
``source_documents`` so readiness, the admin console, and direct answers use
the same definition of "current".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from typing import Any, Iterable

from sqlalchemy.orm import Session

from src.database import IngestionRun, SourceDocument, kst_now


KST = timezone(timedelta(hours=9))
SUCCESS_STATUSES = frozenset({"success", "partial_success"})
PUBLISHED_STATUSES = frozenset({"active", "updated"})
DEFAULT_MAX_AGE_HOURS: dict[str, float] = {
    "notices": 24.0,
    "rules": 240.0,
    "schedule": 72.0,
    "courses": 240.0,
    "staff": 720.0,
    "meals": 36.0,
}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value.astimezone(KST)


def freshness_max_age_hours(dataset: str) -> float:
    """Return the configured age budget without importing the global config."""
    default = DEFAULT_MAX_AGE_HOURS.get(dataset, 168.0)
    raw = os.getenv(f"RAG_{dataset.upper()}_FRESHNESS_MAX_HOURS", str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _latest_canonical_collection_at(session: Session, dataset: str) -> datetime | None:
    row = (
        session.query(SourceDocument.collected_at)
        .filter(
            SourceDocument.dataset == dataset,
            SourceDocument.status.in_(PUBLISHED_STATUSES),
        )
        .order_by(SourceDocument.collected_at.desc())
        .first()
    )
    return _aware(row[0]) if row and row[0] is not None else None


def _dataset_freshness(
    session: Session,
    dataset: str,
    *,
    now: datetime,
) -> dict[str, Any]:
    runs = (
        session.query(IngestionRun)
        .filter(IngestionRun.dataset == dataset)
        .order_by(IngestionRun.started_at.desc(), IngestionRun.id.desc())
        .limit(500)
        .all()
    )
    latest_run = runs[0] if runs else None
    last_success = next((run for run in runs if run.status in SUCCESS_STATUSES), None)
    last_success_at = _aware(
        None if last_success is None else (last_success.finished_at or last_success.started_at)
    )
    last_collection_at = _latest_canonical_collection_at(session, dataset)
    observed_candidates = [value for value in (last_success_at, last_collection_at) if value]
    observed_at = max(observed_candidates) if observed_candidates else None

    completed_before_success = 0
    for run in runs:
        if run.status in SUCCESS_STATUSES:
            break
        if run.status != "running":
            completed_before_success += 1

    max_age_hours = freshness_max_age_hours(dataset)
    age_hours = None
    if observed_at is not None:
        age_hours = max(0.0, (now - observed_at).total_seconds() / 3600.0)
    fresh = age_hours is not None and age_hours <= max_age_hours
    if observed_at is None:
        state = "never_succeeded"
    elif not fresh:
        state = "stale"
    elif completed_before_success >= 2:
        state = "warning"
    else:
        state = "healthy"

    diagnostics = None
    if latest_run is not None and latest_run.diagnostics_json:
        try:
            decoded = json.loads(latest_run.diagnostics_json)
            diagnostics = decoded if isinstance(decoded, dict) else None
        except (TypeError, json.JSONDecodeError):
            diagnostics = None
    return {
        "dataset": dataset,
        "state": state,
        "fresh": fresh,
        "max_age_hours": max_age_hours,
        "age_hours": None if age_hours is None else round(age_hours, 2),
        "last_observed_at": None if observed_at is None else observed_at.isoformat(),
        "last_successful_at": None if last_success_at is None else last_success_at.isoformat(),
        "last_collection_at": None if last_collection_at is None else last_collection_at.isoformat(),
        "latest_run": None if latest_run is None else {
            "id": latest_run.id,
            "status": latest_run.status,
            "outcome_code": latest_run.outcome_code,
            "started_at": None if latest_run.started_at is None else _aware(latest_run.started_at).isoformat(),
            "finished_at": None if latest_run.finished_at is None else _aware(latest_run.finished_at).isoformat(),
            "documents_seen": int(latest_run.documents_seen or 0),
            "documents_failed": int(latest_run.documents_failed or 0),
            "diagnostics": diagnostics,
        },
        "consecutive_unsuccessful_runs": completed_before_success,
    }


def build_ingestion_freshness_report(
    session: Session,
    *,
    datasets: Iterable[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a content-free freshness report for readiness and operations."""
    current = _aware(now or kst_now())
    assert current is not None
    selected = tuple(datasets or DEFAULT_MAX_AGE_HOURS)
    reports = [
        _dataset_freshness(session, dataset, now=current)
        for dataset in selected
    ]
    return {
        "schema_version": 1,
        "generated_at": current.isoformat(),
        "gate_passed": all(item["state"] == "healthy" for item in reports),
        "datasets": reports,
        "stale_datasets": [item["dataset"] for item in reports if not item["fresh"]],
        "warning_datasets": [item["dataset"] for item in reports if item["state"] == "warning"],
    }


def dataset_is_fresh(
    session: Session,
    dataset: str,
    *,
    now: datetime | None = None,
) -> bool:
    report = build_ingestion_freshness_report(
        session,
        datasets=(dataset,),
        now=now,
    )
    return bool(report["datasets"][0]["fresh"])


__all__ = [
    "DEFAULT_MAX_AGE_HOURS",
    "SUCCESS_STATUSES",
    "build_ingestion_freshness_report",
    "dataset_is_fresh",
    "freshness_max_age_hours",
]
