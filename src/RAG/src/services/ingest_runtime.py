"""Shared context and serialization for ingestion/index writes."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import logging
import threading
import time
from typing import Callable, Iterator, ParamSpec, TypeVar

from src.services.maintenance_lock import maintenance_lock


LOGGER = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")

_RUN_CONTEXT: ContextVar[tuple[str, int | None]] = ContextVar(
    "rag_ingestion_run_context",
    default=("unknown", None),
)
_WRITE_DEPTH: ContextVar[int] = ContextVar("rag_ingest_write_depth", default=0)
_PROCESS_WRITE_LOCK = threading.RLock()


@contextmanager
def ingestion_run_context(dataset: str, run_id: int | None) -> Iterator[None]:
    """Attach durable scheduler identity to downstream stage logs."""
    token = _RUN_CONTEXT.set((str(dataset or "unknown"), run_id))
    try:
        yield
    finally:
        _RUN_CONTEXT.reset(token)


def current_ingestion_context(default_dataset: str = "unknown") -> tuple[str, int | None]:
    dataset, run_id = _RUN_CONTEXT.get()
    if dataset == "unknown" and default_dataset:
        dataset = default_dataset
    return dataset, run_id


@contextmanager
def serialized_ingest_write(*, dataset: str, operation: str) -> Iterator[None]:
    """Serialize index writers across threads and processes, with nesting."""
    depth = _WRITE_DEPTH.get()
    if depth:
        token = _WRITE_DEPTH.set(depth + 1)
        try:
            yield
        finally:
            _WRITE_DEPTH.reset(token)
        return

    context_dataset, run_id = current_ingestion_context(dataset)
    started = time.monotonic()
    with _PROCESS_WRITE_LOCK:
        with maintenance_lock(blocking=True):
            token = _WRITE_DEPTH.set(1)
            LOGGER.info(
                "ingest_write_lock_acquired dataset=%s run_id=%s operation=%s waited_ms=%.2f",
                context_dataset,
                run_id,
                operation,
                (time.monotonic() - started) * 1000,
            )
            try:
                yield
            finally:
                LOGGER.info(
                    "ingest_write_lock_released dataset=%s run_id=%s operation=%s elapsed_ms=%.2f",
                    context_dataset,
                    run_id,
                    operation,
                    (time.monotonic() - started) * 1000,
                )
                _WRITE_DEPTH.reset(token)


def serialized_ingest(dataset: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorate one complete ingestion/reindex operation with the shared lock."""
    def decorate(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            with serialized_ingest_write(dataset=dataset, operation=func.__name__):
                return func(*args, **kwargs)

        return wrapped

    return decorate


__all__ = [
    "current_ingestion_context",
    "ingestion_run_context",
    "serialized_ingest",
    "serialized_ingest_write",
]
