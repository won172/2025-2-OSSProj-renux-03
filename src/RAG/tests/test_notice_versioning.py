from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.notice_versioning import (
    annotate_notice_versions,
    canonical_notice_key,
)


def test_recurring_academic_year_notices_share_a_canonical_key():
    assert canonical_notice_key(
        "2025학년도 2학기 학부 수강신청 안내",
        "학사공지",
    ) == canonical_notice_key(
        "[수정] 2026학년도 2학기 학부 수강신청 안내",
        "학사공지",
    )


def test_only_newest_document_in_a_notice_series_is_marked_latest():
    frame = pd.DataFrame(
        [
            {
                "doc_id": "old",
                "chunk_id": "old-0",
                "title": "2025학년도 2학기 학부 수강신청 안내",
                "topics": "학사공지",
                "published_at": "2025-07-20",
            },
            {
                "doc_id": "new",
                "chunk_id": "new-0",
                "title": "2026학년도 2학기 학부 수강신청 안내",
                "topics": "학사공지",
                "published_at": "2026-07-22",
            },
            {
                "doc_id": "new",
                "chunk_id": "new-1",
                "title": "2026학년도 2학기 학부 수강신청 안내",
                "topics": "학사공지",
                "published_at": "2026-07-22",
            },
        ]
    )

    annotated = annotate_notice_versions(frame)

    assert annotated.loc[annotated["doc_id"] == "old", "is_latest"].tolist() == [False]
    assert annotated.loc[annotated["doc_id"] == "new", "is_latest"].tolist() == [True, True]
