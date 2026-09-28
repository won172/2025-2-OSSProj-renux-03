"""Small, content-free snapshots of RAG query logging and scheduler liveness."""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.database import RagQueryLog, TelemetryHeartbeat

UTC = timezone.utc
KST = timezone(timedelta(hours=9))
HOST_ID = socket.gethostname()
_PROCESS_TOKEN = uuid4().hex[:12]


def _utc_naive(value: datetime) -> datetime:
    """SQLite returns naive datetimes; heartbeat values use UTC by contract."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _query_time_utc(value: datetime | None) -> datetime | None:
    # RagQueryLog.created_at defaults to naive KST. Preserve that legacy contract.
    if value is None:
        return None
    return value.replace(tzinfo=value.tzinfo or KST).astimezone(UTC).replace(tzinfo=None)


def write_heartbeat(
    session: Session,
    *,
    scheduler_alive: bool,
    interval_seconds: int,
    now: datetime | None = None,
    host_id: str = HOST_ID,
    process_id: str | None = None,
) -> TelemetryHeartbeat:
    """Count committed query logs in the previous interval, then append one row."""
    if interval_seconds <= 0:
        raise ValueError("heartbeat interval must be positive")
    recorded_at = _utc_naive(now or datetime.now(UTC))
    interval_started_at = recorded_at - timedelta(seconds=interval_seconds)
    kst_start = interval_started_at.replace(tzinfo=UTC).astimezone(KST).replace(tzinfo=None)
    kst_end = recorded_at.replace(tzinfo=UTC).astimezone(KST).replace(tzinfo=None)
    queries_logged = session.query(func.count(RagQueryLog.id)).filter(
        RagQueryLog.created_at >= kst_start,
        RagQueryLog.created_at < kst_end,
    ).scalar()
    latest_query_time = session.query(func.max(RagQueryLog.created_at)).scalar()
    row = TelemetryHeartbeat(
        host_id=host_id,
        process_id=process_id or f"{os.getpid()}-{_PROCESS_TOKEN}",
        recorded_at=recorded_at,
        interval_started_at=interval_started_at,
        queries_logged=int(queries_logged or 0),
        latest_query_log_at=_query_time_utc(latest_query_time),
        scheduler_alive=scheduler_alive,
    )
    session.add(row)
    session.commit()
    return row


@dataclass(frozen=True)
class TelemetryVerdict:
    status: str
    latest_heartbeat_at: datetime | None
    latest_query_log_at: datetime | None
    age_seconds: float | None


def read_verdict(
    session: Session,
    *,
    now: datetime | None = None,
    interval_seconds: int,
    stale_intervals: int,
    no_traffic_seconds: int,
    expected_start_hour: int,
    expected_end_hour: int,
) -> TelemetryVerdict:
    """Classify the newest persisted snapshot across all processes."""
    if min(interval_seconds, stale_intervals, no_traffic_seconds) <= 0:
        raise ValueError("telemetry thresholds must be positive")
    if not 0 <= expected_start_hour < expected_end_hour <= 24:
        raise ValueError("expected traffic hours must satisfy 0 <= start < end <= 24")
    current = _utc_naive(now or datetime.now(UTC))
    latest = session.query(TelemetryHeartbeat).order_by(
        TelemetryHeartbeat.recorded_at.desc(), TelemetryHeartbeat.id.desc()
    ).first()
    if latest is None:
        return TelemetryVerdict("no_heartbeat", None, None, None)
    age = max(0.0, (current - latest.recorded_at).total_seconds())
    if age > interval_seconds * stale_intervals:
        return TelemetryVerdict("stale_heartbeat", latest.recorded_at, latest.latest_query_log_at, age)

    local_now = current.replace(tzinfo=UTC).astimezone(KST)
    if expected_start_hour <= local_now.hour < expected_end_hour:
        window_start = local_now.replace(hour=expected_start_hour, minute=0, second=0, microsecond=0)
        window_start_utc = window_start.astimezone(UTC).replace(tzinfo=None)
        # An empty DB needs enough observation time before raising a warning.
        first_heartbeat_at = session.query(func.min(TelemetryHeartbeat.recorded_at)).scalar()
        anchor = max(
            candidate for candidate in (
                window_start_utc,
                first_heartbeat_at,
                latest.latest_query_log_at,
            ) if candidate is not None
        )
        if (current - anchor).total_seconds() > no_traffic_seconds:
            return TelemetryVerdict(
                "no_traffic_or_logging_broken", latest.recorded_at,
                latest.latest_query_log_at, age,
            )
    return TelemetryVerdict("healthy", latest.recorded_at, latest.latest_query_log_at, age)


def latest_heartbeats(session: Session, limit: int = 10) -> list[TelemetryHeartbeat]:
    return session.query(TelemetryHeartbeat).order_by(
        TelemetryHeartbeat.recorded_at.desc(), TelemetryHeartbeat.id.desc()
    ).limit(limit).all()
