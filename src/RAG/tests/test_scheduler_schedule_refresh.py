from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.scheduler import _merge_schedule_snapshots


def test_schedule_refresh_replaces_current_year_and_preserves_history():
    existing = pd.DataFrame(
        [
            {"학년도": "2025", "내용": "2025 일정", "start": "2025-03-01", "end": "2025-03-01"},
            {"학년도": "2026", "내용": "수정 전 일정", "start": "2026-03-01", "end": "2026-03-01"},
        ]
    )
    incoming = pd.DataFrame(
        [
            {"학년도": "2026", "내용": "수정 후 일정", "start": "2026-03-02", "end": "2026-03-02"},
        ]
    )

    merged = _merge_schedule_snapshots(existing, incoming)

    assert set(merged["내용"]) == {"2025 일정", "수정 후 일정"}
    assert "수정 전 일정" not in set(merged["내용"])
