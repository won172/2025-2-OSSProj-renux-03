from __future__ import annotations

import threading
import time
from pathlib import Path

from src.services import ingest_runtime


def test_serialized_ingest_write_is_reentrant_and_serializes_threads(monkeypatch, tmp_path: Path):
    lock_path = tmp_path / "maintenance.lock"
    original = ingest_runtime.maintenance_lock
    monkeypatch.setattr(
        ingest_runtime,
        "maintenance_lock",
        lambda *, blocking: original(path=lock_path, blocking=blocking),
    )

    events: list[str] = []
    first_entered = threading.Event()
    allow_first_exit = threading.Event()

    def first_writer() -> None:
        with ingest_runtime.serialized_ingest_write(dataset="rules", operation="outer"):
            events.append("first-enter")
            with ingest_runtime.serialized_ingest_write(dataset="rules", operation="nested"):
                events.append("first-nested")
            first_entered.set()
            allow_first_exit.wait(timeout=2)
            events.append("first-exit")

    def second_writer() -> None:
        first_entered.wait(timeout=2)
        with ingest_runtime.serialized_ingest_write(dataset="meals", operation="second"):
            events.append("second-enter")

    first = threading.Thread(target=first_writer)
    second = threading.Thread(target=second_writer)
    first.start()
    second.start()
    assert first_entered.wait(timeout=2)
    time.sleep(0.05)
    assert "second-enter" not in events
    allow_first_exit.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert events == ["first-enter", "first-nested", "first-exit", "second-enter"]
