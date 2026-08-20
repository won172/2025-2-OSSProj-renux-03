from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import database as db  # noqa: E402
from src.pipelines import ingest  # noqa: E402


def test_forced_rules_ingest_builds_chunks_from_canonical_source_documents(
    monkeypatch,
    tmp_path: Path,
):
    """The scheduler path must not fall back to content-derived hash doc IDs."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", factory)
    monkeypatch.setattr(ingest, "engine", engine)
    monkeypatch.setattr(ingest, "SessionLocal", factory)
    db.Base.metadata.create_all(bind=engine)

    rules_path = tmp_path / "rules.csv"
    guides_path = tmp_path / "entry_year_guides.csv"
    pd.DataFrame(
        [
            {
                "relative_dir": "제1편 학교법인",
                "filename": "1-0-1 정관.hwp",
                "title": "학교법인 정관",
                "text": "학교법인 정관 본문 " * 20,
                "section": "학교법인",
            },
            {
                "relative_dir": "official",
                "filename": "2-1-2 학칙.html",
                "title": "학칙",
                "text": "학칙 현행 본문 " * 20,
                "section": "학사",
                "source_version": "70:3638",
            },
            {
                "relative_dir": "제3편 행정",
                "filename": "본문없는내규.hwp",
                "title": "본문 없는 내규",
                "text": "",
                "section": "행정",
            },
        ]
    ).to_csv(rules_path, index=False)
    pd.DataFrame(
        [
            {
                "relative_dir": "entry_year_guides",
                "filename": "2026 학업이수 가이드.pdf",
                "title": "2026 학업이수 가이드",
                "text": "2026학년도 졸업 요건 " * 20,
                "section": "졸업",
                "entry_year": "2026",
            }
        ]
    ).to_csv(guides_path, index=False)

    monkeypatch.setitem(ingest.DATA_SOURCES, "rules", rules_path)
    monkeypatch.setitem(ingest.DATA_SOURCES, "rules_entry_year_guides", guides_path)
    monkeypatch.setattr(ingest, "_entry_year_guide_cache_is_stale", lambda *_args: False)
    monkeypatch.setattr(
        ingest,
        "_persist_replacing_collection",
        lambda _key, _collection, frame: (frame, None, None),
    )

    chunks, _, _ = ingest.ingest_rules(force_source_reload=True)

    session = factory()
    try:
        documents = (
            session.query(db.SourceDocument)
            .filter(db.SourceDocument.dataset == "rules")
            .order_by(db.SourceDocument.id.asc())
            .all()
        )
        canonical_keys = {
            row.document_key for row in documents if row.status in {"active", "updated"}
        }
        parse_failed = [row for row in documents if row.status == "parse_failed"]
        persisted_chunk_doc_ids = {
            value
            for (value,) in session.query(db.Chunk.doc_id)
            .filter(db.Chunk.rule_id.isnot(None))
            .all()
        }
    finally:
        session.close()

    assert len(canonical_keys) == 3
    assert len(parse_failed) == 1
    assert parse_failed[0].parse_error == "empty rule text"
    assert chunks["doc_id"].str.startswith("rules:").all()
    assert set(chunks["doc_id"]) == canonical_keys
    assert persisted_chunk_doc_ids == canonical_keys


def test_legacy_empty_rule_source_is_reconciled_out_of_active_lineage(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(ingest, "engine", engine)
    monkeypatch.setattr(ingest, "SessionLocal", factory)
    db.Base.metadata.create_all(bind=engine)
    session = factory()
    try:
        document = db.SourceDocument(
            dataset="rules",
            source_type="rules_text",
            source_id="empty-rule",
            document_key="rules:empty-rule",
            status="active",
            normalized_payload_json='{"title":"본문 없는 규정","text":""}',
        )
        session.add(document)
        session.commit()

        assert ingest.reconcile_rule_source_statuses(session) == 1
        session.refresh(document)
        assert document.status == "parse_failed"
        assert document.parse_error == "empty rule text"
    finally:
        session.close()
