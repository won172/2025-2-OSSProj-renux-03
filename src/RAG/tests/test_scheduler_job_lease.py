"""Scheduler replicas coordinate through one temporary SQLite lease table."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event
from types import SimpleNamespace
import time

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.services import job_lease
from src.database import Base, SchedulerJobLease
from src.services import scheduler
from src.services.job_lease import JobLease, LeaseGuard, LeaseLostError
from src.services.job_lease import check_current_lease


@pytest.fixture
def sessions(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'leases.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[SchedulerJobLease.__table__])
    yield sessionmaker(bind=engine)
    engine.dispose()


def test_two_owners_racing_start_one_body(sessions):
    first = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="replica-a")
    second = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="replica-b")
    starting = Barrier(3)
    body_started = Event()
    finish = Event()
    started = []
    skipped = []

    def execute(lease):
        starting.wait()

        def body():
            started.append(lease.owner_id)
            body_started.set()
            assert finish.wait(5)

        lease.run("refresh_notices", body, on_skip=lambda _generation: skipped.append(lease.owner_id))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute, lease) for lease in (first, second)]
        starting.wait()
        assert body_started.wait(5)
        done, _ = wait(futures, timeout=5, return_when=FIRST_COMPLETED)
        assert len(done) == 1
        assert len(started) == len(skipped) == 1
        finish.set()
        for future in futures:
            future.result(timeout=5)
    with sessions() as session:
        rows = session.scalars(select(SchedulerJobLease)).all()
        assert len(rows) == 1
        assert rows[0].owner_id == ""


def test_locked_acquisition_retries_then_starts_job(sessions, monkeypatch):
    lease = JobLease(
        sessions, ttl_seconds=5, renew_seconds=1, owner_id="a",
        acquire_retry_seconds=0.5,
    )
    acquire_once = lease._acquire_once
    attempts = []

    def locked_then_available(job_name, *, scheduled_at=None):
        attempts.append(job_name)
        if len(attempts) <= 2:
            raise OperationalError("INSERT scheduler_job_leases", {}, Exception("database is locked"))
        return acquire_once(job_name, scheduled_at=scheduled_at)

    monkeypatch.setattr(lease, "_acquire_once", locked_then_available)
    started = []
    assert lease.run(
        "refresh_notices", lambda: started.append(True),
        on_skip=lambda _generation: pytest.fail("unexpected skip"),
    ) is None
    assert len(attempts) == 3
    assert started == [True]


def test_locked_acquisition_budget_records_skip_and_refreshes_after_release(
    sessions, monkeypatch,
):
    leader = JobLease(sessions, ttl_seconds=1, renew_seconds=0.1, owner_id="a")
    follower = JobLease(
        sessions, ttl_seconds=1, renew_seconds=0.1, owner_id="b",
        acquire_retry_seconds=0.06,
    )
    acquired = leader.try_acquire("refresh_schedule")
    assert acquired is not None
    attempts = []

    def always_locked(*_args, **_kwargs):
        attempts.append(True)
        raise OperationalError("INSERT scheduler_job_leases", {}, Exception("database is locked"))

    monkeypatch.setattr(follower, "_acquire_once", always_locked)
    monkeypatch.setattr(scheduler, "_LAST_RUNS", {})
    monkeypatch.setattr(scheduler, "_send_scheduler_alert", lambda *_args: None)
    refreshed = Event()
    monkeypatch.setattr(scheduler, "_refresh_runtime_dataset_state", lambda _dataset: refreshed.set())
    workers = []
    start_refresh = scheduler._start_follower_refresh

    def capture_refresh(*args):
        workers.append(start_refresh(*args))

    monkeypatch.setattr(scheduler, "_start_follower_refresh", capture_refresh)
    scheduler._run_scheduled_job(
        "refresh_schedule", lambda: pytest.fail("duplicate body"), follower, None
    )
    assert len(attempts) >= 2
    assert scheduler._LAST_RUNS["refresh_schedule"]["last_status"] == "skipped_lease_db_locked"
    assert not refreshed.is_set()
    leader.release("refresh_schedule", acquired)
    workers[0].join(timeout=2)
    assert refreshed.is_set()


def test_locked_acquisition_does_not_follow_previous_fire_release(sessions, monkeypatch, caplog):
    current = [datetime(2026, 1, 1, 12)]
    clock = lambda: current[0]
    leader = JobLease(sessions, ttl_seconds=0.2, renew_seconds=0.05, owner_id="a", clock=clock)
    follower = JobLease(
        sessions, ttl_seconds=0.2, renew_seconds=0.05, owner_id="b",
        clock=clock, acquire_retry_seconds=0.01,
    )
    previous = leader.try_acquire("refresh_schedule", scheduled_at=current[0])
    assert previous is not None
    leader.release("refresh_schedule", previous)
    current[0] += timedelta(minutes=1)

    def locked(*_args, **_kwargs):
        raise OperationalError("INSERT scheduler_job_leases", {}, Exception("database is locked"))

    monkeypatch.setattr(follower, "_acquire_once", locked)
    monkeypatch.setattr(scheduler, "_LAST_RUNS", {})
    monkeypatch.setattr(scheduler, "_send_scheduler_alert", lambda *_args: None)
    refreshed = []
    monkeypatch.setattr(scheduler, "_refresh_runtime_dataset_state", refreshed.append)
    workers = []
    start_refresh = scheduler._start_follower_refresh
    monkeypatch.setattr(
        scheduler, "_start_follower_refresh",
        lambda *args: workers.append(start_refresh(*args)),
    )
    fire_times = scheduler._SubmittedFireTimes(clock=clock)
    fire_times.on_submitted(SimpleNamespace(
        job_id="refresh_schedule",
        scheduled_run_times=[current[0].replace(tzinfo=timezone.utc)],
    ))
    scheduler._run_scheduled_job(
        "refresh_schedule", lambda: pytest.fail("duplicate body"), follower, fire_times
    )
    workers[0].join(timeout=2)
    assert not workers[0].is_alive()
    assert refreshed == []
    assert scheduler._LAST_RUNS["refresh_schedule"]["last_status"] == "skipped_lease_db_locked"
    assert "matching fire not observed" in caplog.text


def test_expired_lease_takeover_and_stale_owner_cannot_release(sessions):
    current = [datetime(2026, 1, 1)]
    clock = lambda: current[0]
    first = JobLease(sessions, ttl_seconds=10, renew_seconds=2, owner_id="a", clock=clock)
    second = JobLease(sessions, ttl_seconds=10, renew_seconds=2, owner_id="b", clock=clock)
    initial = first.try_acquire("refresh_rules")
    assert initial is not None
    assert second.try_acquire("refresh_rules") is None

    current[0] += timedelta(seconds=11)
    replacement = second.try_acquire("refresh_rules")
    assert replacement is not None
    assert not first.renew("refresh_rules", initial)
    first.release("refresh_rules", initial)
    with sessions() as session:
        assert session.get(SchedulerJobLease, "refresh_rules").owner_id == "b"
    second.release("refresh_rules", replacement)


def test_long_job_renews_before_initial_expiry(sessions):
    first = JobLease(sessions, ttl_seconds=0.5, renew_seconds=0.05, owner_id="a")
    second = JobLease(sessions, ttl_seconds=0.5, renew_seconds=0.05, owner_id="b")
    started = Event()
    finish = Event()

    def body():
        started.set()
        assert finish.wait(3)

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(first.run, "refresh_meals", body, on_skip=lambda _generation: pytest.fail("skip"))
        try:
            assert started.wait(3)
            time.sleep(0.7)  # Past the initial TTL; only heartbeats can keep ownership.
            assert second.try_acquire("refresh_meals") is None
            with sessions() as session:
                record = session.get(SchedulerJobLease, "refresh_meals")
                assert record.heartbeat_at > record.acquired_at
        finally:
            finish.set()
        result.result(timeout=3)
    replacement = second.try_acquire("refresh_meals")
    assert replacement is not None
    second.release("refresh_meals", replacement)


def test_locked_renewal_recovers_before_expiry(sessions, monkeypatch):
    current = [datetime(2026, 1, 1)]
    lease = JobLease(sessions, ttl_seconds=12, renew_seconds=2, owner_id="a", clock=lambda: current[0])
    acquired = lease.try_acquire("refresh_meals")
    assert acquired is not None
    guard = LeaseGuard(lease, "refresh_meals", acquired)
    real_renew = lease.renew
    attempts = []

    def temporarily_locked(job_name, acquired_at):
        attempts.append(True)
        if len(attempts) <= 2:
            current[0] += timedelta(seconds=3)  # Simulate more than five seconds of DB contention.
            raise OperationalError("UPDATE scheduler_job_leases", {}, Exception("database is locked"))
        return real_renew(job_name, acquired_at)

    monkeypatch.setattr(lease, "renew", temporarily_locked)
    guard.check()
    assert len(attempts) == 3
    assert current[0] - acquired == timedelta(seconds=6)
    assert not guard.lost.is_set()
    with sessions() as session:
        row = session.get(SchedulerJobLease, "refresh_meals")
        assert row.owner_id == "a" and row.heartbeat_at > row.acquired_at
    lease.release("refresh_meals", acquired)


def test_heartbeat_retries_lock_and_job_completes(sessions, monkeypatch):
    lease = JobLease(sessions, ttl_seconds=2, renew_seconds=0.05, owner_id="a")
    real_renew = lease.renew
    recovered = Event()
    attempts = []

    def temporarily_locked(job_name, acquired_at):
        attempts.append(True)
        if len(attempts) <= 2:
            raise OperationalError("UPDATE scheduler_job_leases", {}, Exception("database is locked"))
        result = real_renew(job_name, acquired_at)
        recovered.set()
        return result

    monkeypatch.setattr(lease, "renew", temporarily_locked)

    def body():
        assert recovered.wait(2)
        check_current_lease()
        return "completed"

    assert lease.run("refresh_meals", body, on_skip=lambda _generation: pytest.fail("skip")) == "completed"
    assert len(attempts) >= 3
    with sessions() as session:
        assert session.get(SchedulerJobLease, "refresh_meals").owner_id == ""


def test_renewal_errors_past_expiry_mark_lease_lost(sessions, monkeypatch):
    current = [datetime(2026, 1, 1)]
    lease = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="a", clock=lambda: current[0])
    acquired = lease.try_acquire("refresh_meals")
    assert acquired is not None
    guard = LeaseGuard(lease, "refresh_meals", acquired)

    def locked_until_expired(*_args):
        current[0] += timedelta(seconds=6)
        raise OperationalError("UPDATE scheduler_job_leases", {}, Exception("database is locked"))

    monkeypatch.setattr(lease, "renew", locked_until_expired)
    with pytest.raises(LeaseLostError, match="job lease lost"):
        guard.check()
    assert guard.lost.is_set()


def test_confirmed_owner_change_marks_lease_lost(sessions):
    lease = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="a")
    acquired = lease.try_acquire("refresh_meals")
    assert acquired is not None
    guard = LeaseGuard(lease, "refresh_meals", acquired)
    with sessions() as session:
        row = session.get(SchedulerJobLease, "refresh_meals")
        row.owner_id = "b"
        row.generation += 1
        session.commit()
    with pytest.raises(LeaseLostError, match="job lease lost"):
        guard.check()
    assert guard.lost.is_set()


@pytest.mark.parametrize("fails", [False, True])
def test_release_after_success_or_exception(sessions, fails):
    lease = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="a")

    def body():
        if fails:
            raise RuntimeError("job failed")
        return "ok"

    if fails:
        with pytest.raises(RuntimeError, match="job failed"):
            lease.run("refresh_staff", body, on_skip=lambda _generation: pytest.fail("skip"))
    else:
        assert lease.run("refresh_staff", body, on_skip=lambda _generation: pytest.fail("skip")) == "ok"
    with sessions() as session:
        assert session.get(SchedulerJobLease, "refresh_staff").owner_id == ""


def test_locked_release_retries_then_releases(sessions, monkeypatch):
    lease = JobLease(
        sessions, ttl_seconds=5, renew_seconds=1, owner_id="a", release_retry_seconds=0.5,
    )
    acquired = lease.try_acquire("refresh_staff")
    assert acquired is not None
    release_once = lease._release_once
    attempts = []

    def locked_then_released(job_name, acquired_at):
        attempts.append(True)
        if len(attempts) <= 2:
            raise OperationalError("UPDATE scheduler_job_leases", {}, Exception("database is locked"))
        return release_once(job_name, acquired_at)

    monkeypatch.setattr(lease, "_release_once", locked_then_released)
    lease.release("refresh_staff", acquired)
    assert len(attempts) == 3
    with sessions() as session:
        assert session.get(SchedulerJobLease, "refresh_staff").owner_id == ""


def test_persistent_release_lock_retries_in_background_until_expiry(
    sessions, monkeypatch, caplog,
):
    current = [datetime(2026, 1, 1)]
    lease = JobLease(
        sessions, ttl_seconds=2, renew_seconds=0.5, owner_id="a",
        clock=lambda: current[0], release_retry_seconds=0.01,
    )
    acquired = lease.try_acquire("refresh_staff")
    assert acquired is not None
    attempts = []
    returned = Event()
    retried_in_background = Event()
    workers = []
    real_thread = job_lease.Thread

    def capture_thread(*args, **kwargs):
        worker = real_thread(*args, **kwargs)
        workers.append(worker)
        return worker

    def always_locked(*_args):
        attempts.append(True)
        if returned.is_set():
            retried_in_background.set()
        raise OperationalError("UPDATE scheduler_job_leases", {}, Exception("database is locked"))

    monkeypatch.setattr(job_lease, "Thread", capture_thread)
    monkeypatch.setattr(lease, "_release_once", always_locked)
    lease.release("refresh_staff", acquired)
    assert len(workers) == 1
    returned.set()
    assert retried_in_background.wait(2)
    current[0] += timedelta(seconds=3)
    workers[0].join(timeout=2)
    assert not workers[0].is_alive()
    count_at_expiry = len(attempts)
    time.sleep(0.1)
    assert len(attempts) == count_at_expiry
    assert "retrying in background" in caplog.text
    assert "release expired before completion" in caplog.text


def test_different_jobs_have_independent_leases(sessions):
    first = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="a")
    second = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="b")
    notice = first.try_acquire("refresh_notices")
    meals = second.try_acquire("refresh_meals")
    assert notice is not None and meals is not None
    first.release("refresh_notices", notice)
    second.release("refresh_meals", meals)


def test_fast_completed_job_cannot_restart_for_same_cron_fire(sessions):
    current = [datetime(2026, 1, 1, 12)]
    clock = lambda: current[0]
    first = JobLease(sessions, ttl_seconds=10, renew_seconds=2, owner_id="a", clock=clock)
    second = JobLease(sessions, ttl_seconds=10, renew_seconds=2, owner_id="b", clock=clock)
    started = []
    skipped = []
    fire_at = current[0]
    first.run("refresh_courses", lambda: started.append("a"),
              on_skip=lambda _generation: skipped.append("a"), scheduled_at=fire_at)
    current[0] += timedelta(seconds=1)
    second.run("refresh_courses", lambda: started.append("b"),
               on_skip=lambda _generation: skipped.append("b"), scheduled_at=fire_at)
    assert started == ["a"]
    assert skipped == ["b"]

    current[0] += timedelta(hours=1)
    second.run("refresh_courses", lambda: started.append("b"),
               on_skip=lambda _generation: skipped.append("b"), scheduled_at=current[0])
    assert started == ["a", "b"]


def test_delayed_submission_and_next_slot_both_start(sessions, monkeypatch):
    kst = timezone(timedelta(hours=9))
    now = [datetime(2026, 1, 1, 3, 1, 5)]  # 12:00 job starts at 12:01:05 KST.
    clock = lambda: now[0]
    first = JobLease(sessions, ttl_seconds=10, renew_seconds=2, owner_id="a", clock=clock)
    second = JobLease(sessions, ttl_seconds=10, renew_seconds=2, owner_id="b", clock=clock)
    fire_times = scheduler._SubmittedFireTimes(clock=clock)
    monkeypatch.setattr(scheduler, "_start_follower_refresh", lambda *_args: None)
    started = []

    def submit(minute):
        fire_times.on_submitted(SimpleNamespace(
            job_id="refresh_courses",
            scheduled_run_times=[datetime(2026, 1, 1, 12, minute, tzinfo=kst)],
        ))

    submit(0)
    scheduler._run_scheduled_job("refresh_courses", lambda: started.append("12:00"), first, fire_times)
    with sessions() as session:
        row = session.get(SchedulerJobLease, "refresh_courses")
        assert row.acquired_at == now[0]
        assert row.scheduled_at == datetime(2026, 1, 1, 3)

    submit(0)
    scheduler._run_scheduled_job("refresh_courses", lambda: pytest.fail("same slot"), second, fire_times)
    now[0] += timedelta(seconds=1)
    submit(1)
    scheduler._run_scheduled_job("refresh_courses", lambda: started.append("12:01"), second, fire_times)
    assert started == ["12:00", "12:01"]


def test_missed_submission_does_not_shift_next_slot():
    current = datetime(2026, 1, 1, 3, 2, 5)
    fire_times = scheduler._SubmittedFireTimes(clock=lambda: current)
    for minute in (0, 2):
        fire_times.on_submitted(SimpleNamespace(
            job_id="refresh_courses",
            scheduled_run_times=[datetime(2026, 1, 1, 3, minute, tzinfo=timezone.utc)],
        ))
    assert fire_times.take("refresh_courses") == datetime(2026, 1, 1, 3, 2)


def test_missing_lease_table_does_not_start_job(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'no-table.db'}")
    lease = JobLease(sessionmaker(bind=engine), ttl_seconds=5, renew_seconds=1)
    started = []
    with pytest.raises(OperationalError, match="scheduler_job_leases"):
        lease.run("refresh_rules", lambda: started.append(True), on_skip=lambda _generation: None)
    assert started == []
    engine.dispose()


def test_forked_worker_uses_new_owner_identity(sessions):
    lease = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="pre-fork")
    lease._pid = -1
    acquired = lease.try_acquire("refresh_rules")
    assert acquired is not None
    assert lease.owner_id != "pre-fork"
    lease.release("refresh_rules", acquired)


def test_scheduler_records_skip_without_entering_body(sessions, monkeypatch):
    first = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="a")
    second = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="b")
    acquired = first.try_acquire("refresh_courses")
    assert acquired is not None
    monkeypatch.setattr(scheduler, "_LAST_RUNS", {})
    monkeypatch.setattr(scheduler, "_send_scheduler_alert", lambda *_args: None)
    monkeypatch.setattr(scheduler, "_start_follower_refresh", lambda *_args: None)
    scheduler._run_scheduled_job(
        "refresh_courses", lambda: pytest.fail("duplicate body"), second, None
    )
    assert scheduler._LAST_RUNS["refresh_courses"]["last_status"] == "skipped_not_leader"
    first.release("refresh_courses", acquired)


def test_follower_refreshes_after_leader_release(sessions, monkeypatch):
    first = JobLease(sessions, ttl_seconds=1, renew_seconds=0.1, owner_id="a")
    second = JobLease(sessions, ttl_seconds=1, renew_seconds=0.1, owner_id="b")
    acquired = first.try_acquire("refresh_schedule")
    assert acquired is not None
    refreshed = Event()
    monkeypatch.setattr(scheduler, "_refresh_runtime_dataset_state", lambda dataset: refreshed.set())
    monkeypatch.setattr(scheduler, "_LAST_RUNS", {})
    monkeypatch.setattr(scheduler, "_send_scheduler_alert", lambda *_args: None)

    scheduler._run_scheduled_job("refresh_schedule", lambda: pytest.fail("duplicate"), second, None)
    assert not refreshed.wait(0.1)
    first.release("refresh_schedule", acquired)
    assert refreshed.wait(2)
    assert scheduler._LAST_RUNS["refresh_schedule"]["last_status"] == "skipped_not_leader"


def test_follower_refreshes_if_release_is_followed_by_reacquire_before_poll(sessions, monkeypatch):
    first = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="a")
    follower = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="b")
    next_owner = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="c")
    initial = first.try_acquire("refresh_schedule")
    assert initial is not None
    polling_started = Event()
    allow_poll = Event()
    refreshed = Event()
    original_wait = follower.wait_for_release

    def delayed_wait(job_name, observed_generation, *, scheduled_at=None):
        polling_started.set()
        assert allow_poll.wait(2)
        return original_wait(job_name, observed_generation, scheduled_at=scheduled_at)

    monkeypatch.setattr(follower, "wait_for_release", delayed_wait)
    monkeypatch.setattr(scheduler, "_refresh_runtime_dataset_state", lambda dataset: refreshed.set())
    monkeypatch.setattr(scheduler, "_LAST_RUNS", {})
    monkeypatch.setattr(scheduler, "_send_scheduler_alert", lambda *_args: None)
    scheduler._run_scheduled_job("refresh_schedule", lambda: pytest.fail("duplicate"), follower, None)
    assert polling_started.wait(2)
    try:
        first.release("refresh_schedule", initial)
        replacement = next_owner.try_acquire("refresh_schedule")
        assert replacement is not None
        with sessions() as session:
            row = session.get(SchedulerJobLease, "refresh_schedule")
            assert (row.generation, row.released_generation, row.owner_id) == (2, 1, "c")
    finally:
        allow_poll.set()
    assert refreshed.wait(2)
    next_owner.release("refresh_schedule", replacement)


def test_release_then_reacquire_and_release_still_refreshes_follower(sessions, monkeypatch):
    current = [datetime(2026, 1, 1)]
    clock = lambda: current[0]
    first = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="a", clock=clock)
    follower = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="b", clock=clock)
    next_owner = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="c", clock=clock)
    initial = first.try_acquire("refresh_schedule")
    assert initial is not None
    _, observed_generation = follower._attempt_acquire("refresh_schedule")
    assert observed_generation == 1

    first.release("refresh_schedule", initial)
    current[0] += timedelta(seconds=1)
    replacement = next_owner.try_acquire("refresh_schedule")
    assert replacement is not None
    next_owner.release("refresh_schedule", replacement)
    with sessions() as session:
        row = session.get(SchedulerJobLease, "refresh_schedule")
        assert (row.generation, row.generation_outcomes) == (2, "RR")

    refreshed = []
    monkeypatch.setattr(scheduler, "_refresh_runtime_dataset_state", refreshed.append)
    worker = scheduler._start_follower_refresh("refresh_schedule", follower, observed_generation)
    assert worker is not None
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert refreshed == ["schedule"]


def test_expired_takeover_does_not_count_as_release_for_follower(sessions):
    current = [datetime(2026, 1, 1)]
    clock = lambda: current[0]
    first = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="a", clock=clock)
    follower = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="b", clock=clock)
    next_owner = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="c", clock=clock)
    assert first.try_acquire("refresh_schedule") is not None
    observed_at, observed_generation = follower._attempt_acquire("refresh_schedule")
    assert observed_at is None and observed_generation == 1
    current[0] += timedelta(seconds=6)
    replacement = next_owner.try_acquire("refresh_schedule")
    assert replacement is not None
    assert not follower.wait_for_release("refresh_schedule", observed_generation)
    next_owner.release("refresh_schedule", replacement)


def test_late_release_after_expiry_is_not_clean_release(sessions):
    current = [datetime(2026, 1, 1)]
    clock = lambda: current[0]
    owner = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="a", clock=clock)
    follower = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="b", clock=clock)
    acquired = owner.try_acquire("refresh_schedule")
    _, observed_generation = follower._attempt_acquire("refresh_schedule")
    current[0] += timedelta(seconds=6)
    owner.release("refresh_schedule", acquired)
    assert not follower.wait_for_release("refresh_schedule", observed_generation)


def test_takeover_then_release_still_warns_follower_with_in_memory_sqlite(monkeypatch, caplog):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[SchedulerJobLease.__table__])
    sessions = sessionmaker(bind=engine)
    current = [datetime(2026, 1, 1)]
    clock = lambda: current[0]
    first = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="a", clock=clock)
    follower = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="b", clock=clock)
    next_owner = JobLease(sessions, ttl_seconds=5, renew_seconds=1, owner_id="c", clock=clock)
    refreshed = []
    monkeypatch.setattr(scheduler, "_refresh_runtime_dataset_state", refreshed.append)
    try:
        assert first.try_acquire("refresh_schedule") is not None
        _, observed_generation = follower._attempt_acquire("refresh_schedule")
        current[0] += timedelta(seconds=6)
        replacement = next_owner.try_acquire("refresh_schedule")
        assert replacement is not None
        next_owner.release("refresh_schedule", replacement)
        with sessions() as session:
            row = session.get(SchedulerJobLease, "refresh_schedule")
            assert (row.generation, row.released_generation) == (2, 2)
            assert row.generation_outcomes == "ER"
        worker = scheduler._start_follower_refresh("refresh_schedule", follower, observed_generation)
        assert worker is not None
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert refreshed == []
        assert "lease expired without release" in caplog.text
    finally:
        engine.dispose()


def test_follower_does_not_refresh_on_expiry_without_release(sessions, monkeypatch, caplog):
    first = JobLease(sessions, ttl_seconds=0.2, renew_seconds=0.05, owner_id="a")
    second = JobLease(sessions, ttl_seconds=0.2, renew_seconds=0.05, owner_id="b")
    acquired = first.try_acquire("refresh_meals")
    assert acquired is not None
    refreshed = []
    monkeypatch.setattr(scheduler, "_refresh_runtime_dataset_state", refreshed.append)
    with sessions() as session:
        observed_generation = session.get(SchedulerJobLease, "refresh_meals").generation
    worker = scheduler._start_follower_refresh("refresh_meals", second, observed_generation)
    assert worker is not None
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert refreshed == []
    assert "lease expired without release" in caplog.text


def test_failed_renewal_stops_publication_refresh_and_success(sessions, monkeypatch):
    lease = JobLease(sessions, ttl_seconds=1, renew_seconds=0.05, owner_id="a")
    published = []
    refreshed = []
    heartbeat_failed = Event()
    monkeypatch.setattr(scheduler, "_refresh_runtime_dataset_state", refreshed.append)
    monkeypatch.setattr(scheduler, "_LAST_RUNS", {})
    monkeypatch.setattr(scheduler, "_send_scheduler_alert", lambda *_args: None)
    def fail_renewal(*_args):
        heartbeat_failed.set()
        return False

    monkeypatch.setattr(lease, "renew", fail_renewal)

    def body():
        assert heartbeat_failed.wait(2)
        check_current_lease()
        published.append(True)
        scheduler._refresh_runtime_dataset_state("notices")
        scheduler._record_run("refresh_notices", "ok")

    scheduler._run_scheduled_job("refresh_notices", body, lease, None)
    assert published == refreshed == []
    assert scheduler._LAST_RUNS["refresh_notices"]["last_status"] == "lease_lost"


def test_expired_takeover_stops_old_body_at_checkpoint(sessions, monkeypatch):
    current = [datetime(2026, 1, 1)]
    clock = lambda: current[0]
    first = JobLease(sessions, ttl_seconds=3, renew_seconds=1, owner_id="a", clock=clock)
    second = JobLease(sessions, ttl_seconds=3, renew_seconds=1, owner_id="b", clock=clock)
    monkeypatch.setattr(scheduler, "_LAST_RUNS", {})
    monkeypatch.setattr(scheduler, "_send_scheduler_alert", lambda *_args: None)
    published = []

    def old_body():
        current[0] += timedelta(seconds=4)
        acquired = second.try_acquire("refresh_courses")
        assert acquired is not None
        check_current_lease()
        published.append(True)

    scheduler._run_scheduled_job("refresh_courses", old_body, first, None)
    assert published == []
    assert scheduler._LAST_RUNS["refresh_courses"]["last_status"] == "lease_lost"
    with sessions() as session:
        assert session.get(SchedulerJobLease, "refresh_courses").owner_id == "b"


def test_staff_stage_is_blocked_after_lease_loss(sessions, monkeypatch):
    from src.crawlers import dongguk_staff_contacts
    from src.services import source_schema, staff_refresh

    lease = JobLease(sessions, ttl_seconds=1, renew_seconds=0.1, owner_id="a")
    staged = []
    recorded = []
    monkeypatch.setattr(lease, "renew", lambda *_args: False)
    monkeypatch.setattr(scheduler, "_start_ingestion_run", lambda *_args: 1)
    monkeypatch.setattr(scheduler, "_finish_ingestion_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scheduler, "_record_run", lambda _job, status, *_args: recorded.append(status))
    monkeypatch.setattr(dongguk_staff_contacts, "crawl_staff_contacts", lambda **_kwargs: pd.DataFrame({"name": ["fixture"]}))
    monkeypatch.setattr(source_schema, "fingerprint_dataframe", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(staff_refresh, "stage_staff_refresh", lambda *_args: staged.append(True))

    scheduler._run_scheduled_job("refresh_staff", scheduler.refresh_staff_job, lease, None)
    assert staged == []
    assert recorded == ["lease_lost"]


@pytest.mark.parametrize("enabled", [False, True])
def test_scheduler_registration_preserves_jobs_and_disabled_behavior(monkeypatch, enabled):
    from apscheduler.schedulers import background

    registered = []

    class FakeScheduler:
        def __init__(self, *, timezone):
            assert timezone == "Asia/Seoul"

        def add_listener(self, callback, mask):
            from apscheduler.events import EVENT_JOB_SUBMITTED
            assert mask == EVENT_JOB_SUBMITTED
            assert callback.__self__.__class__ is scheduler._SubmittedFireTimes

        def add_job(self, func, trigger, *, id, **settings):
            registered.append((id, func, settings))

        def start(self):
            pass

    monkeypatch.setattr(background, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(scheduler, "_scheduler", None)
    monkeypatch.setattr(scheduler, "RAG_SCHEDULER_ENABLED", True)
    monkeypatch.setattr(scheduler, "RAG_SCHEDULER_JOB_LEASE_ENABLED", enabled)
    assert isinstance(scheduler.start_scheduler(), FakeScheduler)
    assert [name for name, _, _ in registered] == list(scheduler.JOB_LABELS)
    assert all(settings == {"max_instances": 1, "coalesce": True, "misfire_grace_time": 120}
               for _, _, settings in registered)
    if enabled:
        assert all(func.func is scheduler._run_scheduled_job for _, func, _ in registered)
    else:
        assert [func for _, func, _ in registered] == [
            scheduler.refresh_notices_job,
            scheduler.refresh_rules_job,
            scheduler.refresh_schedule_job,
            scheduler.refresh_meals_job,
            scheduler.refresh_courses_job,
            scheduler.refresh_staff_job,
        ]
