"""Incremental notice writers must embed the same input as live ingest.

Live ingest (``ingest._persist_chunks``) embeds ``retrieval_text`` and certifies
it with ``embedding_input_hash``/``embedding_input_field``.  The incremental
notice sync and the admin manual-notice upsert write into the same live
collection, so their vectors and scope metadata must equal what live ingest
writes when the scheduled refresh rebuilds the same rows from SQLite
(``build_notice_index_frame_from_session``).  Otherwise ``_reusable_vectors``
rejects them, and department-only notices can sit in Chroma as public rows.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import chromadb
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import rag_service  # noqa: E402
from src.database import Base, PendingItem, SourceDocument  # noqa: E402
from src.pipelines import ingest, notices_sync  # noqa: E402
from src.services.retrieval_context import enrich_retrieval_fields  # noqa: E402
from src.vectorstore import chroma_client  # noqa: E402

# Metadata the retrieval filters and the retrieval header depend on.
SCOPE_FIELDS = (
    "audience",
    "department",
    "visibility",
    "published_at",
    "apply_deadline",
    "title_norm",
    "retrieval_context",
    "document_key",
    "doc_id",
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    monkeypatch.setenv("RAG_COLLECTION_POINTER_FILE", str(tmp_path / "pointers.json"))
    monkeypatch.setattr(chroma_client, "get_client", lambda: client)
    chroma_client.get_collection.cache_clear()
    monkeypatch.setitem(
        ingest.DATASET_ARTIFACTS,
        "notices",
        ingest.DatasetArtifacts("notices", notices_sync.NOTICE_COLLECTION, tmp_path / "notices.parquet"),
    )
    monkeypatch.setattr(ingest, "_train_lexical_indices", lambda *_args, **_kwargs: (None, None))
    encoded: dict[str, list[list[str]]] = {"sync": [], "admin": [], "live": []}

    def fake_encoder(label: str):
        def encode(texts):
            batch = [str(text) for text in texts]
            encoded[label].append(batch)
            return np.asarray(
                [[float(byte) for byte in hashlib.sha256(text.encode()).digest()[:4]] for text in batch],
                dtype=np.float32,
            )

        return encode

    monkeypatch.setattr(notices_sync, "encode_texts", fake_encoder("sync"))
    monkeypatch.setattr(rag_service, "encode_texts", fake_encoder("admin"))
    monkeypatch.setattr(ingest, "encode_texts", fake_encoder("live"))
    for name in (notices_sync.NOTICE_COLLECTION, "live_reference"):
        client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})
    yield client, encoded
    chroma_client.get_collection.cache_clear()


def _stored(client, collection: str, ids: list[str]) -> dict[str, tuple[str, dict, np.ndarray]]:
    result = client.get_collection(collection).get(ids=ids, include=["documents", "metadatas", "embeddings"])
    return {
        chunk_id: (result["documents"][index], result["metadatas"][index], np.asarray(result["embeddings"][index]))
        for index, chunk_id in enumerate(result["ids"])
    }


def _assert_matches_db_live_ingest(client, encoded, label: str, session) -> tuple[list[str], dict]:
    """Compare the incremental write with live ingest of the SQLite-rebuilt frame."""
    db_frame = ingest.build_notice_index_frame_from_session(session)
    ids = db_frame["chunk_id"].astype(str).tolist()
    expected = enrich_retrieval_fields(db_frame)
    expected_texts = dict(zip(ids, expected["retrieval_text"].astype(str)))
    # Guard: the fix is meaningful only if retrieval_text differs from chunk_text.
    assert any(expected_texts[i] != text for i, text in zip(ids, db_frame["chunk_text"].astype(str)))

    assert encoded[label] == [[expected_texts[chunk_id] for chunk_id in ids]]
    written = _stored(client, notices_sync.NOTICE_COLLECTION, ids)
    assert set(written) == set(ids)
    for chunk_id, (document, metadata, _vector) in written.items():
        assert document == expected_texts[chunk_id]
        assert metadata["embedding_input_field"] == "retrieval_text"
        assert metadata["embedding_input_hash"] == ingest._embedding_input_hash(document)
        assert "chunk_text" not in metadata
        assert "retrieval_text" not in metadata

    ingest._persist_chunks("notices", "live_reference", db_frame.copy())
    reference = _stored(client, "live_reference", ids)
    assert encoded["live"][-1] == encoded[label][0]
    for chunk_id in ids:
        assert written[chunk_id][0] == reference[chunk_id][0]
        for field in ("embedding_input_field", "embedding_input_hash", *SCOPE_FIELDS):
            assert written[chunk_id][1].get(field) == reference[chunk_id][1].get(field), field
        np.testing.assert_array_equal(written[chunk_id][2], reference[chunk_id][2])

    # The scheduled refresh must reuse these vectors instead of re-embedding.
    texts = [expected_texts[chunk_id] for chunk_id in ids]
    hashes = [ingest._embedding_input_hash(text) for text in texts]
    assert set(ingest._reusable_vectors(notices_sync.NOTICE_COLLECTION, ids, texts, hashes)) == set(ids)
    live_calls = len(encoded["live"])
    ingest._persist_chunks("notices", notices_sync.NOTICE_COLLECTION, db_frame.copy())
    assert len(encoded["live"]) == live_calls, "live refresh re-embedded vectors it should reuse"
    after = _stored(client, notices_sync.NOTICE_COLLECTION, ids)
    for chunk_id in ids:
        # Re-upserting a reused vector may round-trip through Chroma's float storage.
        np.testing.assert_allclose(after[chunk_id][2], written[chunk_id][2], rtol=1e-6)
    return ids, written


def test_incremental_notice_sync_embeds_live_ingest_input(harness):
    client, encoded = harness
    session = _session()
    try:
        normalized, _ = notices_sync._normalize_notice_record(
            pd.Series(
                {
                    "게시판": "학사공지",
                    "게시판코드": "HAKSANOTICE",
                    "원문글ID": 321,
                    "제목": "2026학년도 2학기 수강정정 안내",
                    "카테고리": "학사",
                    "게시일": "2026-08-20",
                    "상세URL": "https://www.dongguk.edu/article/HAKSANOTICE/detail/321",
                    "본문": "대학원생을 제외한 학부생은 9월 1일부터 9월 5일까지 수강정정을 할 수 있습니다.",
                    "첨부파일": "[]",
                }
            )
        )
        source_document = SourceDocument(
            dataset="notices",
            source_type=normalized["source_type"],
            source_id=normalized["source_id"],
            source_url=normalized["detail_url"],
            document_key=normalized["document_key"],
            title=normalized["title"],
            status="active",
        )
        session.add(source_document)
        session.flush()

        notice_rows = notices_sync._upsert_notice_domain_rows(session, [normalized])
        notices_sync._upsert_notice_chunks(session, notice_rows, {normalized["source_id"]: source_document})
        session.flush()
        assert source_document.last_indexed_at is not None

        _assert_matches_db_live_ingest(client, encoded, "sync", session)
    finally:
        session.close()


# >300 chars before the appended "주관:" line and no audience term anywhere in
# title/category/body, so only the department column signals the audience.
_NEUTRAL_LONG_BODY = "행사 장소와 준비물, 참석 방법을 다시 한 번 확인해 주세요. " * 12
assert len(_NEUTRAL_LONG_BODY) > 300


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param(
            {
                "title": "2026학년도 대학원 학위청구논문 제출 안내",
                "content": "대학원 석사과정 학생은 11월 14일까지 학위청구논문을 제출해야 합니다.",
                "date": "2026-09-01",
                "category": "대학원",
                "department": "컴퓨터·AI학부",
            },
            {"visibility": "department", "department": "컴퓨터·AI학부"},
            id="baseline-department-only",
        ),
        pytest.param(
            {
                "title": "종강총회 참석 안내",
                "content": _NEUTRAL_LONG_BODY,
                "date": "2026-09-01",
                "category": "행사",
                "department": "컴퓨터·AI학부",
            },
            {"audience": "undergraduate", "visibility": "department", "department": "컴퓨터·AI학부"},
            id="long-body-department-only-audience",
        ),
        pytest.param(
            {
                "title": "종강총회 장소 변경",
                "content": "장소가 변경되었습니다.",
                "date": "2026.09.01.",
                "category": "행사",
                "department": "컴퓨터·AI학부",
                "visibility": "public",
            },
            {"visibility": "public", "department": "컴퓨터·AI학부", "published_at": "2026.09.01."},
            id="public-non-iso-date",
        ),
    ],
)
def test_admin_manual_notice_upsert_embeds_live_ingest_input(harness, payload, expected):
    client, encoded = harness
    session = _session()
    try:
        item = PendingItem(source_type="announcement", data=json.dumps(payload, ensure_ascii=False))
        session.add(item)
        session.flush()

        notice, chunk_ids = rag_service._index_pending_item(session, item, notices_sync.NOTICE_COLLECTION)
        session.flush()
        assert notice is not None and chunk_ids

        ids, written = _assert_matches_db_live_ingest(client, encoded, "admin", session)
        assert ids == chunk_ids
        source_document = session.query(SourceDocument).filter(SourceDocument.dataset == "notices").one()
        for chunk_id in ids:
            metadata = written[chunk_id][1]
            assert metadata["document_key"] == source_document.document_key
            for field, value in expected.items():
                assert metadata[field] == value, field
        if "published_at" in expected:
            assert f"기준일: {expected['published_at']}" in written[ids[0]][0]
    finally:
        session.close()
