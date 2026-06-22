"""날짜 필터에서 게시일 미상 문서를 보조 후보로 보존하는 회귀 테스트."""
from __future__ import annotations

from datetime import date
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.rag_service import _apply_date_filter  # noqa: E402
from src.utils.date_parser import QueryDateFilter  # noqa: E402


def test_date_filter_keeps_unknown_dates_as_auxiliary_candidates():
    hits = pd.DataFrame(
        [
            {
                "chunk_id": "in-range",
                "title": "범위 안 공지",
                "published_at": "2026-06-19",
                "hybrid_score": 0.9,
            },
            {
                "chunk_id": "unknown-date",
                "title": "게시일 미상 공지",
                "published_at": "",
                "hybrid_score": 0.8,
            },
            {
                "chunk_id": "out-of-range",
                "title": "범위 밖 공지",
                "published_at": "2026-05-01",
                "hybrid_score": 0.7,
            },
        ]
    )
    date_filter = QueryDateFilter(
        start=date(2026, 6, 18),
        end=date(2026, 6, 20),
        label="specific_range",
    )

    filtered, eliminated = _apply_date_filter(hits, "notices", date_filter)

    assert eliminated is False
    assert filtered["chunk_id"].tolist() == ["in-range", "unknown-date"]
    unknown_row = filtered[filtered["chunk_id"] == "unknown-date"].iloc[0]
    assert int(unknown_row["date_unknown_auxiliary"]) == 1


def test_date_filter_uses_unknown_dates_when_all_dated_hits_are_eliminated():
    hits = pd.DataFrame(
        [
            {
                "chunk_id": "unknown-date",
                "title": "게시일 미상 공지",
                "published_at": "",
                "hybrid_score": 0.8,
            },
            {
                "chunk_id": "out-of-range",
                "title": "범위 밖 공지",
                "published_at": "2026-05-01",
                "hybrid_score": 0.7,
            },
        ]
    )
    date_filter = QueryDateFilter(
        start=date(2026, 6, 18),
        end=date(2026, 6, 20),
        label="specific_range",
    )

    filtered, eliminated = _apply_date_filter(hits, "notices", date_filter)

    assert eliminated is True
    assert filtered["chunk_id"].tolist() == ["unknown-date"]
    assert int(filtered.iloc[0]["date_unknown_auxiliary"]) == 1
