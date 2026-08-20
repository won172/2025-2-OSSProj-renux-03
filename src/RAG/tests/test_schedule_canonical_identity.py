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


def test_schedule_refresh_builds_chunks_from_canonical_keys(monkeypatch):
    """Repeated schedule content must remain distinct through the canonical key."""
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
    monkeypatch.setattr(ingest, "_save_chunks_to_sqlite", lambda *_args: None)
    monkeypatch.setattr(ingest, "_persist_replacing_collection", lambda _key, _collection, frame: (frame, None, None))
    db.Base.metadata.create_all(bind=engine)

    frame = pd.DataFrame(
        [
            {
                "학년도": "2025",
                "구분": "학사일정",
                "내용": "개강",
                "주관부서": "각 학과별",
                "start": "2025-03-01",
                "end": "2025-03-01",
            },
            {
                "학년도": "2026",
                "구분": "학사일정",
                "내용": "개강",
                "주관부서": "각 학과별",
                "start": "2025-03-01",
                "end": "2025-03-01",
            },
        ]
    )

    chunks, _, _ = ingest.ingest_schedule(frame, refresh_from_csv=True)

    assert len(chunks) == 2
    assert chunks["chunk_id"].is_unique
    assert chunks["doc_id"].nunique() == 2
    assert chunks["doc_id"].str.startswith("schedule:").all()

    session = factory()
    try:
        documents = (
            session.query(db.SourceDocument)
            .filter(db.SourceDocument.dataset == "schedule")
            .order_by(db.SourceDocument.id.asc())
            .all()
        )
        assert len(documents) == 2
        assert {row.document_key for row in documents} == set(chunks["doc_id"])
    finally:
        session.close()
