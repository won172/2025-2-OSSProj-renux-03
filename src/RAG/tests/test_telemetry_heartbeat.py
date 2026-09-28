from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base, RagQueryLog, TelemetryHeartbeat
from src.services import scheduler
from src.services.telemetry_heartbeat import read_verdict, write_heartbeat

UTC = timezone.utc


@pytest.fixture
def sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'telemetry.db'}")
    Base.metadata.create_all(engine, tables=[RagQueryLog.__table__, TelemetryHeartbeat.__table__])
    yield sessionmaker(bind=engine)
    engine.dispose()


def verdict(session, now):
    return read_verdict(
        session, now=now, interval_seconds=300, stale_intervals=2,
        no_traffic_seconds=3600, expected_start_hour=9, expected_end_hour=18,
    )


def test_per_replica_heartbeats_count_queries_without_content(sessions):
    now = datetime(2026, 9, 28, 1, 30, tzinfo=UTC)  # 10:30 KST
    with sessions() as session:
        session.add_all([
            RagQueryLog(question="private question", created_at=datetime(2026, 9, 28, 10, 26)),
            RagQueryLog(question="older question", created_at=datetime(2026, 9, 28, 10, 20)),
        ])
        session.commit()
        a = write_heartbeat(session, now=now, scheduler_alive=True,
                            interval_seconds=300, host_id="host-a", process_id="a")
        b = write_heartbeat(session, now=now + timedelta(minutes=1), scheduler_alive=True,
                            interval_seconds=300, host_id="host-b", process_id="b")
        assert a.queries_logged == 1
        assert b.queries_logged == 1
        assert a.latest_query_log_at == datetime(2026, 9, 28, 1, 26)
        assert b.latest_query_log_at == a.latest_query_log_at
        assert verdict(session, now + timedelta(minutes=2)).status == "healthy"
        assert "question" not in TelemetryHeartbeat.__table__.columns.keys()


def test_stale_and_expected_hours_no_traffic_verdicts(sessions):
    start = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)  # 09:00 KST
    with sessions() as session:
        assert verdict(session, start).status == "no_heartbeat"
        write_heartbeat(session, now=start, scheduler_alive=True,
                        interval_seconds=300, host_id="host", process_id="p")
        assert verdict(session, start + timedelta(minutes=10)).status == "healthy"
        assert verdict(session, start + timedelta(minutes=10, seconds=1)).status == "stale_heartbeat"
        later = start + timedelta(hours=1, minutes=1)
        write_heartbeat(session, now=later, scheduler_alive=True,
                        interval_seconds=300, host_id="host", process_id="p")
        assert verdict(session, later).status == "no_traffic_or_logging_broken"
        outside = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)  # 19:00 KST
        write_heartbeat(session, now=outside, scheduler_alive=True,
                        interval_seconds=300, host_id="host", process_id="p")
        assert verdict(session, outside).status == "healthy"


def test_query_advance_clears_no_traffic_warning(sessions):
    now = datetime(2026, 9, 28, 2, 0, tzinfo=UTC)
    with sessions() as session:
        write_heartbeat(session, now=now - timedelta(minutes=61), scheduler_alive=True,
                        interval_seconds=300, host_id="host", process_id="p")
        session.add(RagQueryLog(question="private", created_at=datetime(2026, 9, 28, 10, 59)))
        session.commit()
        write_heartbeat(session, now=now, scheduler_alive=True,
                        interval_seconds=300, host_id="host", process_id="p")
        assert verdict(session, now).status == "healthy"


def test_scheduler_writer_is_unleased_and_webhook_contains_only_aggregates(sessions, monkeypatch):
    now = datetime(2026, 9, 28, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(scheduler, "SessionLocal", sessions)
    monkeypatch.setattr(scheduler, "RAG_TELEMETRY_HEARTBEAT_INTERVAL_SECONDS", 300)
    monkeypatch.setattr(scheduler, "RAG_TELEMETRY_HEARTBEAT_STALE_INTERVALS", 2)
    monkeypatch.setattr(scheduler, "RAG_TELEMETRY_NO_TRAFFIC_SECONDS", 3600)
    monkeypatch.setattr(scheduler, "RAG_TELEMETRY_EXPECTED_TRAFFIC_START_HOUR", 9)
    monkeypatch.setattr(scheduler, "RAG_TELEMETRY_EXPECTED_TRAFFIC_END_HOUR", 18)
    monkeypatch.setattr(scheduler, "RAG_SCHEDULER_ALERT_WEBHOOK_URL", "https://alerts.example.test")
    calls = []

    def fake_post(_url, *, json, timeout):
        calls.append(json)
        return SimpleNamespace(raise_for_status=lambda: None)

    monkeypatch.setattr(scheduler.httpx, "post", fake_post)
    with sessions() as session:
        write_heartbeat(session, now=now - timedelta(minutes=11), scheduler_alive=True,
                        interval_seconds=300, host_id="other", process_id="other")
    scheduler._run_telemetry_heartbeat(SimpleNamespace(running=True), now=now)
    with sessions() as session:
        assert session.query(TelemetryHeartbeat).count() == 2
    assert len(calls) == 1
    assert calls[0]["verdict"] == "stale_heartbeat"
    assert calls[0]["event"] == "telemetry_heartbeat"
    assert "question" not in str(calls[0])


def test_no_traffic_alert_is_warning(sessions, monkeypatch):
    now = datetime(2026, 9, 28, 2, 1, tzinfo=UTC)
    monkeypatch.setattr(scheduler, "SessionLocal", sessions)
    monkeypatch.setattr(scheduler, "RAG_TELEMETRY_HEARTBEAT_INTERVAL_SECONDS", 300)
    monkeypatch.setattr(scheduler, "RAG_TELEMETRY_HEARTBEAT_STALE_INTERVALS", 2)
    monkeypatch.setattr(scheduler, "RAG_TELEMETRY_NO_TRAFFIC_SECONDS", 3600)
    monkeypatch.setattr(scheduler, "RAG_TELEMETRY_EXPECTED_TRAFFIC_START_HOUR", 9)
    monkeypatch.setattr(scheduler, "RAG_TELEMETRY_EXPECTED_TRAFFIC_END_HOUR", 18)
    sent = []
    monkeypatch.setattr(scheduler, "_post_operations_alert", sent.append)
    with sessions() as session:
        write_heartbeat(session, now=now - timedelta(minutes=61), scheduler_alive=True,
                        interval_seconds=300, host_id="host", process_id="p")
        write_heartbeat(session, now=now - timedelta(minutes=5), scheduler_alive=True,
                        interval_seconds=300, host_id="host", process_id="p")
    scheduler._run_telemetry_heartbeat(SimpleNamespace(running=True), now=now)
    assert [item["verdict"] for item in sent] == ["no_traffic_or_logging_broken"]
    assert sent[0]["status"] == "warning"
    scheduler._run_telemetry_heartbeat(
        SimpleNamespace(running=True), now=now + timedelta(minutes=5)
    )
    assert len(sent) == 1


def test_cli_prints_aggregate_only(sessions, monkeypatch, capsys):
    from scripts import report_telemetry_heartbeat

    now = datetime(2026, 9, 28, 1, 30, tzinfo=UTC)
    monkeypatch.setattr(report_telemetry_heartbeat, "SessionLocal", sessions)
    monkeypatch.setattr(report_telemetry_heartbeat, "read_verdict",
                        lambda session, **kwargs: read_verdict(session, now=now, **kwargs))
    monkeypatch.setattr(sys, "argv", ["report_telemetry_heartbeat.py", "--limit", "1"])
    with sessions() as session:
        session.add(RagQueryLog(question="private question", created_at=datetime(2026, 9, 28, 10, 29)))
        session.commit()
        write_heartbeat(session, now=now, scheduler_alive=True,
                        interval_seconds=300, host_id="host", process_id="p")
    assert report_telemetry_heartbeat.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["verdict"] == "healthy"
    assert report["heartbeats"][0]["queries_logged"] == 1
    assert "private question" not in str(report)


def test_heartbeat_registration_bypasses_job_lease(monkeypatch):
    from apscheduler.schedulers import background

    registered = []

    class FakeScheduler:
        def __init__(self, *, timezone):
            self.running = False

        def add_listener(self, *_args):
            pass

        def add_job(self, func, trigger, *, id, **settings):
            registered.append((id, func, trigger, settings))

        def start(self):
            self.running = True

    monkeypatch.setattr(background, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(scheduler, "_scheduler", None)
    monkeypatch.setattr(scheduler, "RAG_SCHEDULER_ENABLED", True)
    monkeypatch.setattr(scheduler, "RAG_SCHEDULER_JOB_LEASE_ENABLED", True)
    monkeypatch.setattr(scheduler, "RAG_TELEMETRY_HEARTBEAT_ENABLED", True)
    monkeypatch.setattr(scheduler, "RAG_TELEMETRY_HEARTBEAT_INTERVAL_SECONDS", 300)
    scheduler.start_scheduler()
    heartbeat = next(job for job in registered if job[0] == "telemetry_heartbeat")
    assert heartbeat[1].func is scheduler._run_telemetry_heartbeat
    assert heartbeat[2] == "interval"
    assert heartbeat[3]["seconds"] == 300
    assert len(registered) == len(scheduler.JOB_LABELS) + 1
