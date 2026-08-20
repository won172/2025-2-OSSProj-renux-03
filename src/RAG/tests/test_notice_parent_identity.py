from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import rag_service  # noqa: E402
from src.database import Base, Chunk, Notice, PendingItem, SourceDocument  # noqa: E402
from src.pipelines.ingest import build_notice_index_frame_from_session  # noqa: E402


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_notice_db_index_frame_preserves_document_identity_and_chunk_order():
    session = _session()
    try:
        url = "https://www.dongguk.edu/article/HAKSANOTICE/detail/100"
        session.add(
            SourceDocument(
                dataset="notices",
                source_type="html_notice",
                source_id="HAKSANOTICE:100",
                source_url=url,
                document_key="notices:HAKSANOTICE:100",
                title="수강정정 안내",
                status="active",
            )
        )
        notice = Notice(
            board="학사공지",
            title="수강정정 안내",
            category="학사공지",
            detail_url=url,
            content="공지 본문",
            attachments="[]",
        )
        session.add(notice)
        session.flush()
        session.add_all(
            [
                Chunk(
                    chunk_id="notice-100-0",
                    chunk_text="[수강정정 안내]\n\n첫 번째 청크",
                    notice_id=notice.id,
                    doc_id="notices:HAKSANOTICE:100",
                    position=0,
                ),
                Chunk(
                    chunk_id="notice-100-1",
                    chunk_text="[수강정정 안내]\n\n수강정정 기간은 5월 21일부터입니다.",
                    notice_id=notice.id,
                    doc_id="notices:HAKSANOTICE:100",
                    position=1,
                ),
            ]
        )
        session.commit()

        frame = build_notice_index_frame_from_session(session)
    finally:
        session.close()

    assert frame["doc_id"].tolist() == ["notices:HAKSANOTICE:100"] * 2
    assert frame["position"].tolist() == [0, 1]


def test_notice_db_index_frame_backfills_legacy_chunk_identity_from_source_document():
    session = _session()
    try:
        url = "https://www.dongguk.edu/article/HAKSANOTICE/detail/101"
        session.add(
            SourceDocument(
                dataset="notices",
                source_type="html_notice",
                source_id="HAKSANOTICE:101",
                source_url=url,
                document_key="notices:HAKSANOTICE:101",
                title="수강정정 안내",
                status="active",
            )
        )
        notice = Notice(
            board="학사공지",
            title="수강정정 안내",
            category="학사공지",
            detail_url=url,
            content="공지 본문",
            attachments="[]",
        )
        session.add(notice)
        session.flush()
        # Pre-migration rows have only the vector chunk ID and body text.
        session.add_all(
            [
                Chunk(chunk_id="notice-101-0", chunk_text="첫 청크", notice_id=notice.id),
                Chunk(chunk_id="notice-101-1", chunk_text="둘째 청크", notice_id=notice.id),
            ]
        )
        session.commit()

        frame = build_notice_index_frame_from_session(session)
    finally:
        session.close()

    assert frame["doc_id"].tolist() == ["notices:HAKSANOTICE:101"] * 2
    assert frame["position"].tolist() == [0, 1]


def test_manual_notice_without_url_uses_canonical_source_document_identity():
    session = _session()
    try:
        source_url = "manual://notice/1"
        session.add(
            SourceDocument(
                dataset="notices",
                source_type="manual_notice",
                source_id="manual_notice:1",
                source_url=source_url,
                document_key="notices:manual_notice:1",
                title="통계학과 종강총회",
                status="active",
            )
        )
        notice = Notice(
            board="통계학과",
            title="통계학과 종강총회",
            category="학과",
            is_manual=1,
            detail_url=None,
            content="수동 공지 본문",
            attachments="[]",
        )
        session.add(notice)
        session.flush()
        session.add(
            Chunk(
                chunk_id="manual-notice-1",
                chunk_text="수동 공지 본문",
                notice_id=notice.id,
                doc_id=None,
                position=0,
            )
        )
        session.commit()

        frame = build_notice_index_frame_from_session(session)
    finally:
        session.close()

    assert frame["doc_id"].tolist() == ["notices:manual_notice:1"]
    assert frame["url"].tolist() == [source_url]


def test_admin_pending_notice_indexes_and_unindexes_through_canonical_document(
    monkeypatch: pytest.MonkeyPatch,
):
    session = _session()
    upsert_call: dict = {}
    deleted_ids: list[str] = []

    def fake_upsert_items(**kwargs):
        upsert_call.update(kwargs)

    monkeypatch.setattr(
        rag_service,
        "encode_texts",
        lambda texts: [[0.1, 0.2] for _ in texts],
    )
    monkeypatch.setattr(rag_service, "upsert_items", fake_upsert_items)
    monkeypatch.setattr(
        rag_service,
        "delete_items",
        lambda _collection, ids: deleted_ids.extend(ids),
    )

    try:
        item = PendingItem(
            source_type="announcement",
            data=json.dumps(
                {
                    "title": "정본 경로 테스트 공지",
                    "content": "모든 검색 인덱스가 같은 문서키를 사용해야 합니다.",
                    "date": "2026-08-10",
                    "category": "일반",
                    "department": "컴퓨터·AI학부",
                },
                ensure_ascii=False,
            ),
        )
        session.add(item)
        session.flush()

        notice, chunk_ids = rag_service._index_pending_item(
            session,
            item,
            "fixture-notices",
        )
        session.flush()

        assert notice is not None
        document = (
            session.query(SourceDocument)
            .filter(SourceDocument.dataset == "notices")
            .one()
        )
        expected_document_key = f"notices:manual_notice:{notice.id}"
        assert document.source_id == f"manual_notice:{notice.id}"
        assert document.document_key == expected_document_key
        assert document.status == "active"
        assert document.last_indexed_at is not None

        chunks = session.query(Chunk).filter(Chunk.notice_id == notice.id).all()
        assert [chunk.chunk_id for chunk in chunks] == chunk_ids
        assert all(chunk.doc_id == expected_document_key for chunk in chunks)
        assert [chunk.position for chunk in chunks] == list(range(len(chunks)))

        assert upsert_call["name"] == "fixture-notices"
        assert upsert_call["ids"] == chunk_ids
        assert all(
            metadata["doc_id"] == expected_document_key
            and metadata["document_key"] == expected_document_key
            for metadata in upsert_call["metadatas"]
        )

        removed_ids = rag_service._unindex_pending_item(
            session,
            item,
            "fixture-notices",
        )
        session.flush()

        assert removed_ids == chunk_ids
        assert deleted_ids == chunk_ids
        assert document.status == "deleted"
        assert session.query(Notice).count() == 0
        assert session.query(Chunk).count() == 0
        assert session.query(SourceDocument).count() == 1
    finally:
        session.close()
