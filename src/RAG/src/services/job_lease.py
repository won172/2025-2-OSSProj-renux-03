"""Atomic SQLite leases for jobs scheduled in more than one API process.

Cancellation is cooperative. An ingest function that commits internally can still
publish after its holder loses the lease, until the next scheduler checkpoint.
The default 24-hour TTL and renewal at no more than TTL/3 reduce that window.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextvars import ContextVar
import logging
import os
import socket
from threading import Event, Lock, Thread
import time
from typing import Callable, TypeVar
from uuid import uuid4

from sqlalchemy import and_, case, or_, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import OperationalError

from src.database import SchedulerJobLease


logger = logging.getLogger(__name__)
T = TypeVar("T")


class LeaseLostError(RuntimeError):
    """The job must stop before its next publication or success checkpoint."""


class LeaseGuard:
    def __init__(self, lease: "JobLease", job_name: str, acquired_at: datetime):
        self.lease = lease
        self.job_name = job_name
        self.acquired_at = acquired_at
        self.lost = Event()
        self._expires_at = acquired_at + timedelta(seconds=lease.ttl_seconds)
        self._renew_lock = Lock()

    def check(self) -> None:
        if not self.renew_until_confirmed():
            raise LeaseLostError(f"job lease lost: {self.job_name}")

    def renew_until_confirmed(self, stop: Event | None = None) -> bool:
        """Retry transient DB failures only while our last confirmed lease is live."""
        with self._renew_lock:
            backoff = 0.05
            while not self.lost.is_set():
                if stop is not None and stop.is_set():
                    return True
                started_at = self.lease._clock()
                if started_at >= self._expires_at:
                    self.lost.set()
                    return False
                try:
                    if self.lease.renew(self.job_name, self.acquired_at):
                        # The DB update uses its own clock reading at the start;
                        # this slightly earlier bound cannot overstate its TTL.
                        self._expires_at = started_at + timedelta(seconds=self.lease.ttl_seconds)
                        return True
                    if not self.lease.is_current_owner(self.job_name, self.acquired_at):
                        self.lost.set()
                        return False
                except Exception as exc:
                    logger.warning(
                        "[scheduler] transient job lease renewal error job=%s: %s",
                        self.job_name, exc,
                    )
                remaining = (self._expires_at - self.lease._clock()).total_seconds()
                if remaining <= 0:
                    self.lost.set()
                    return False
                delay = min(backoff, remaining)
                if stop is None:
                    time.sleep(delay)
                else:
                    stop.wait(delay)
                backoff = min(backoff * 2, 1.0)
            return False


_current_guard: ContextVar[LeaseGuard | None] = ContextVar("scheduler_job_lease_guard", default=None)


def check_current_lease() -> None:
    """Cooperative checkpoint; manual jobs and disabled leases retain old behavior."""
    guard = _current_guard.get()
    if guard is not None:
        guard.check()


def _utc_now() -> datetime:
    # SQLite stores DateTime without an offset; all lease timestamps use UTC.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class JobLease:
    def __init__(
        self,
        session_factory,
        *,
        ttl_seconds: float,
        renew_seconds: float,
        owner_id: str | None = None,
        clock: Callable[[], datetime] = _utc_now,
        acquire_retry_seconds: float = 5.0,
        release_retry_seconds: float = 5.0,
    ) -> None:
        if ttl_seconds <= 0 or renew_seconds <= 0 or renew_seconds > ttl_seconds / 3:
            raise ValueError("job lease requires 0 < renew_seconds <= ttl_seconds / 3")
        if acquire_retry_seconds < 0 or release_retry_seconds < 0:
            raise ValueError("job lease retry budgets cannot be negative")
        self._session_factory = session_factory
        self.ttl_seconds = ttl_seconds
        self.renew_seconds = renew_seconds
        self._pid = os.getpid()
        self.owner_id = owner_id or f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex}"
        self._clock = clock
        self.acquire_retry_seconds = acquire_retry_seconds
        self.release_retry_seconds = release_retry_seconds

    def _acquire_once(
        self, job_name: str, *, scheduled_at: datetime | None = None
    ) -> tuple[datetime | None, int]:
        """Claim a job or observe its generation under the same write transaction."""
        if os.getpid() != self._pid:
            # A preloaded scheduler can be inherited by forked workers.
            self._pid = os.getpid()
            self.owner_id = f"{socket.gethostname()}:{self._pid}:{uuid4().hex}"
        now = self._clock()
        fire_at = scheduled_at or now
        expires_at = now + timedelta(seconds=self.ttl_seconds)
        statement = insert(SchedulerJobLease).values(
            job_name=job_name,
            generation=1,
            released_generation=0,
            generation_outcomes="",
            owner_id=self.owner_id,
            acquired_at=now,
            scheduled_at=fire_at,
            expires_at=expires_at,
            heartbeat_at=now,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[SchedulerJobLease.job_name],
            set_={
                "generation": SchedulerJobLease.generation + 1,
                # A live predecessor can only be replaced after expiry. Record
                # its outcome in the same atomic claim as the new generation.
                "generation_outcomes": case(
                    (SchedulerJobLease.owner_id != "", SchedulerJobLease.generation_outcomes + "E"),
                    else_=SchedulerJobLease.generation_outcomes,
                ),
                "owner_id": self.owner_id,
                "acquired_at": now,
                "scheduled_at": fire_at,
                "expires_at": expires_at,
                "heartbeat_at": now,
            },
            where=and_(
                SchedulerJobLease.expires_at <= now,
                or_(
                    SchedulerJobLease.owner_id != "",
                    SchedulerJobLease.scheduled_at < fire_at,
                ),
            ),
        )
        with self._session_factory() as session:
            result = session.execute(statement)
            # The upsert has the SQLite writer lock until commit. Reading here
            # makes the losing generation immune to a release/reacquire race.
            row = session.get(SchedulerJobLease, job_name)
            generation = row.generation
            session.commit()
            return (now if result.rowcount == 1 else None), generation

    def _attempt_acquire(
        self, job_name: str, *, scheduled_at: datetime | None = None
    ) -> tuple[datetime | None, int | None]:
        """Retry SQLite writer contention; an exhausted budget is a follower skip."""
        deadline = time.monotonic() + self.acquire_retry_seconds
        backoff = 0.05
        while True:
            try:
                return self._acquire_once(job_name, scheduled_at=scheduled_at)
            except OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        "[scheduler] job lease acquisition locked until retry budget elapsed job=%s",
                        job_name,
                    )
                    return None, None
                time.sleep(min(backoff, remaining))
                backoff = min(backoff * 2, 1.0)

    def try_acquire(
        self, job_name: str, *, scheduled_at: datetime | None = None
    ) -> datetime | None:
        """Atomically claim a job, deduplicating completed runs of one cron fire."""
        acquired_at, _ = self._attempt_acquire(job_name, scheduled_at=scheduled_at)
        return acquired_at

    def renew(self, job_name: str, acquired_at: datetime) -> bool:
        now = self._clock()
        statement = (
            update(SchedulerJobLease)
            .where(
                SchedulerJobLease.job_name == job_name,
                SchedulerJobLease.owner_id == self.owner_id,
                SchedulerJobLease.acquired_at == acquired_at,
                SchedulerJobLease.expires_at > now,
            )
            .values(
                expires_at=now + timedelta(seconds=self.ttl_seconds),
                heartbeat_at=now,
            )
        )
        with self._session_factory() as session:
            result = session.execute(statement)
            session.commit()
            return result.rowcount == 1

    def is_current_owner(self, job_name: str, acquired_at: datetime) -> bool:
        """Confirm a failed renewal was caused by a changed lease holder."""
        with self._session_factory() as session:
            row = session.get(SchedulerJobLease, job_name)
            return bool(
                row is not None
                and row.owner_id == self.owner_id
                and row.acquired_at == acquired_at
            )

    def _release_once(self, job_name: str, acquired_at: datetime) -> bool:
        # Keep scheduled_at as the completed fire marker while releasing ownership.
        now = self._clock()
        statement = (
            update(SchedulerJobLease)
            .where(
                SchedulerJobLease.job_name == job_name,
                SchedulerJobLease.owner_id == self.owner_id,
                SchedulerJobLease.acquired_at == acquired_at,
                SchedulerJobLease.expires_at > now,
            )
            .values(
                owner_id="",
                expires_at=now,
                released_generation=SchedulerJobLease.generation,
                generation_outcomes=SchedulerJobLease.generation_outcomes + "R",
            )
        )
        with self._session_factory() as session:
            result = session.execute(statement)
            session.commit()
            return result.rowcount == 1

    def release(
        self, job_name: str, acquired_at: datetime, *, expires_at: datetime | None = None
    ) -> None:
        """Retry a locked release, then keep retrying in the background until expiry."""
        expiry = expires_at or acquired_at + timedelta(seconds=self.ttl_seconds)
        deadline = time.monotonic() + self.release_retry_seconds

        def retry(*, background: bool) -> bool:
            backoff = 0.05
            while self._clock() < expiry:
                try:
                    # A zero-row update means this owner already expired or was
                    # replaced; retrying cannot release another generation.
                    self._release_once(job_name, acquired_at)
                    return True
                except OperationalError as exc:
                    if "locked" not in str(exc).lower():
                        if background:
                            logger.exception("[scheduler] job lease release failed permanently job=%s", job_name)
                            return False
                        raise
                    remaining = (expiry - self._clock()).total_seconds()
                    if not background:
                        remaining = min(remaining, deadline - time.monotonic())
                    if remaining <= 0:
                        break
                    time.sleep(min(backoff, remaining))
                    backoff = min(backoff * 2, 1.0)
            if background:
                logger.error("[scheduler] job lease release expired before completion job=%s", job_name)
            return False

        if retry(background=False):
            return
        if self._clock() >= expiry:
            logger.error("[scheduler] job lease release expired before completion job=%s", job_name)
            return
        logger.error(
            "[scheduler] job lease release still locked; retrying in background job=%s owner=%s",
            job_name, self.owner_id,
        )
        Thread(
            target=lambda: retry(background=True),
            name=f"job-lease-release-{job_name}", daemon=True,
        ).start()

    def wait_for_release(
        self, job_name: str, observed_generation: int | None, *,
        scheduled_at: datetime | None = None, poll_seconds: float = 0.25,
    ) -> bool:
        """Wait at most one TTL for this generation's release, even if reacquired."""
        deadline = time.monotonic() + self.ttl_seconds
        while True:
            try:
                with self._session_factory() as session:
                    row = session.get(SchedulerJobLease, job_name)
                    if row is not None:
                        if observed_generation is None:
                            # Acquisition was blocked before it could observe a
                            # generation. Never mistake the previous fire's
                            # completed row for this scheduled fire's release.
                            if scheduled_at is None or row.scheduled_at >= scheduled_at:
                                observed_generation = row.generation
                        if observed_generation is not None:
                            # Outcomes are append-only, so later reacquisitions
                            # cannot obscure this follower's generation.
                            if len(row.generation_outcomes) >= observed_generation:
                                return row.generation_outcomes[observed_generation - 1] == "R"
                            if row.generation > observed_generation or row.expires_at <= self._clock():
                                return False
            except OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(poll_seconds, remaining))

    def run(
        self,
        job_name: str,
        body: Callable[[], T],
        *,
        on_skip: Callable[[int | None], None],
        on_lost: Callable[[], None] | None = None,
        scheduled_at: datetime | None = None,
    ) -> T | None:
        acquired_at, observed_generation = self._attempt_acquire(
            job_name, scheduled_at=scheduled_at
        )
        if acquired_at is None:
            on_skip(observed_generation)
            return None

        stop = Event()
        guard = LeaseGuard(self, job_name, acquired_at)

        def heartbeat() -> None:
            while not stop.wait(self.renew_seconds):
                if not guard.renew_until_confirmed(stop):
                    logger.error("[scheduler] job lease lost job=%s owner=%s", job_name, self.owner_id)
                    return

        worker = Thread(target=heartbeat, name=f"job-lease-{job_name}", daemon=True)
        token = _current_guard.set(guard)
        try:
            worker.start()
            result = body()
            guard.check()
            return result
        except LeaseLostError:
            if on_lost is not None:
                on_lost()
            else:
                raise
            return None
        finally:
            _current_guard.reset(token)
            stop.set()
            if worker.ident is not None:
                worker.join()
            try:
                self.release(job_name, acquired_at, expires_at=guard._expires_at)
            except Exception:
                logger.exception("[scheduler] job lease release failed job=%s", job_name)
