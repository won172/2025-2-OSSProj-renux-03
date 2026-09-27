from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.scheduler import _merge_schedule_snapshots
from src.database import Base, SourceDocument
from src.pipelines.ingest import _store_schedule_source_documents


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


def test_schedule_refresh_drops_legacy_blank_year_snapshot():
    existing = pd.DataFrame(
        [
            {"학년도": "", "내용": "개강", "start": "2026-03-01", "end": "2026-03-01"},
            {"학년도": "", "내용": "종강", "start": "2026-06-21", "end": "2026-06-21"},
        ]
    )
    incoming = pd.DataFrame(
        [
            {"학년도": "2026", "내용": "개강", "start": "2026-03-01", "end": "2026-03-01"},
            {"학년도": "2026", "내용": "종강", "start": "2026-06-21", "end": "2026-06-21"},
        ]
    )

    merged = _merge_schedule_snapshots(existing, incoming)

    assert len(merged) == 2
    assert set(merged["학년도"]) == {"2026"}


def test_schedule_refresh_deduplicates_same_event_within_incoming_year():
    incoming = pd.DataFrame(
        [
            {"학년도": "2026", "내용": "개강", "start": "2026-03-01", "end": "2026-03-01", "주관부서": "교무팀"},
            {"학년도": "2026", "내용": "개강", "start": "2026-03-01", "end": "2026-03-01", "주관부서": "교무팀"},
        ]
    )

    merged = _merge_schedule_snapshots(pd.DataFrame(), incoming)

    assert len(merged) == 1


@pytest.mark.parametrize(
    "incoming",
    [
        pd.DataFrame([{"내용": "개강"}]),
        pd.DataFrame([{"학년도": "", "내용": "개강"}]),
        pd.DataFrame(),
    ],
)
def test_schedule_refresh_rejects_snapshot_without_named_academic_year(incoming):
    with pytest.raises(ValueError):
        _merge_schedule_snapshots(pd.DataFrame(), incoming)


def test_replaced_year_missing_event_becomes_hidden_while_history_stays_active():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        original = pd.DataFrame(
            [
                {"학년도": "2025", "title": "2025 일정", "start_date": "2025-03-01", "end_date": "2025-03-01", "category": "학사"},
                {"학년도": "2026", "title": "삭제될 일정", "start_date": "2026-03-01", "end_date": "2026-03-01", "category": "학사"},
            ]
        )
        _store_schedule_source_documents(session, original)
        replacement = pd.DataFrame(
            [
                {"학년도": "2025", "title": "2025 일정", "start_date": "2025-03-01", "end_date": "2025-03-01", "category": "학사"},
                {"학년도": "2026", "title": "새 일정", "start_date": "2026-03-02", "end_date": "2026-03-02", "category": "학사"},
            ]
        )
        _store_schedule_source_documents(session, replacement)

        rows = session.query(SourceDocument).filter(SourceDocument.dataset == "schedule").all()
        status_by_title = {row.title: row.status for row in rows}
        assert status_by_title["2025 일정"] == "active"
        assert status_by_title["삭제될 일정"] == "hidden"
        assert status_by_title["새 일정"] == "active"
    finally:
        session.close()
