from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import rag_service  # noqa: E402
from src.database import Base, Notice, SourceDocument  # noqa: E402
from src.pipelines.ingest import build_notice_chunks  # noqa: E402
from src.pipelines.notices_sync import backfill_manual_notice_department_scopes  # noqa: E402
from src.utils.notice_visibility import DEPARTMENT_VISIBILITY, PUBLIC_VISIBILITY  # noqa: E402


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_department_notice_chunks_keep_structured_visibility_metadata():
    chunks = build_notice_chunks(
        pd.DataFrame(
            [
                {
                    "게시판": "학과행사",
                    "제목": "통계인 남산걷기행사",
                    "본문": "일시: 2026-09-04\n주관: 통계학과",
                    "게시일": "2026-09-04",
                    "상세URL": "manual://notice/event/1",
                    "첨부파일": "[]",
                    "department": "통계학과",
                    "visibility": "department",
                }
            ]
        )
    )

    assert chunks["department"].tolist() == ["통계학과"]
    assert chunks["visibility"].tolist() == [DEPARTMENT_VISIBILITY]


def test_home_briefing_keeps_department_notice_out_of_guest_and_other_major_scope():
    session = _session()
    try:
        session.add_all(
            [
                Notice(
                    board="학과행사",
                    title="전체 공개 행사",
                    published_date="2026-08-12",
                    visibility=PUBLIC_VISIBILITY,
                ),
                Notice(
                    board="학과행사",
                    title="통계학과 행사",
                    published_date="2026-08-14",
                    department="통계학과",
                    visibility=DEPARTMENT_VISIBILITY,
                ),
                Notice(
                    board="학과행사",
                    title="컴퓨터·AI학부 행사",
                    published_date="2026-08-15",
                    department="컴퓨터·AI학부",
                    visibility=DEPARTMENT_VISIBILITY,
                ),
            ]
        )
        session.commit()

        assert [row["title"] for row in rag_service._briefing_notices(session)] == ["전체 공개 행사"]
        assert [row["title"] for row in rag_service._briefing_notices(session, user_major="통계학과")] == [
            "통계학과 행사",
            "전체 공개 행사",
        ]
        assert [row["title"] for row in rag_service._briefing_notices(session, user_major="컴퓨터·AI학부")] == [
            "컴퓨터·AI학부 행사",
            "전체 공개 행사",
        ]
    finally:
        session.close()


def test_retrieval_scope_filter_rejects_other_department_notice():
    where_filter = rag_service._notice_visibility_where_filter("통계학과")

    assert rag_service._matches_where_filter(
        pd.Series({"visibility": "public", "department": ""}), where_filter
    )
    assert rag_service._matches_where_filter(
        pd.Series({"visibility": "department", "department": "통계학과"}), where_filter
    )
    assert not rag_service._matches_where_filter(
        pd.Series({"visibility": "department", "department": "컴퓨터·AI학부"}), where_filter
    )


def test_generic_faq_category_is_not_misclassified_as_a_department_scope():
    assert rag_service._pending_notice_visibility(
        "custom_knowledge",
        {"question": "도서관 운영시간", "answer": "평일 9시부터 18시입니다.", "category": "FAQ"},
    ) == PUBLIC_VISIBILITY


def test_legacy_manual_department_notice_is_backfilled_into_source_document():
    session = _session()
    try:
        notice = Notice(
            board="학과행사",
            title="통계인 남산걷기행사",
            published_date="2026-09-04",
            detail_url="manual://notice/event/20",
            content="일시: 2026-09-04\n\n주관: 통계학과",
            attachments="[]",
            is_manual=1,
            visibility=PUBLIC_VISIBILITY,
        )
        session.add(notice)
        session.commit()

        assert backfill_manual_notice_department_scopes(session) == [notice.id]
        assert notice.department == "통계학과"
        assert notice.visibility == DEPARTMENT_VISIBILITY

        document = session.query(SourceDocument).one()
        payload = json.loads(document.normalized_payload_json)
        assert payload["department"] == "통계학과"
        assert payload["visibility"] == DEPARTMENT_VISIBILITY
    finally:
        session.close()
