from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base, IngestionRun
from src.search import hybrid
from src.search.fts_index import build_fts_index, load_fts_index
from src.services import scheduler
from src.services.corpus_revision import (
    compute_corpus_revision,
    frame_corpus_revision,
    stamp_corpus_revision,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"chunk_id": "b", "doc_id": "docs:2", "chunk_text": "둘째"},
            {"chunk_id": "a", "doc_id": "docs:1", "chunk_text": "첫째"},
        ]
    )


def test_corpus_revision_is_stable_across_row_order():
    frame = _frame()

    assert compute_corpus_revision("docs", frame) == compute_corpus_revision(
        "docs", frame.iloc[::-1].reset_index(drop=True)
    )


def test_corpus_revision_changes_with_retrieval_metadata():
    frame = _frame()
    changed = frame.copy()
    changed.loc[0, "doc_id"] = "docs:changed"

    assert compute_corpus_revision("docs", frame) != compute_corpus_revision("docs", changed)


def test_stamped_frame_has_one_revision_and_self_column_is_excluded():
    stamped, revision = stamp_corpus_revision("docs", _frame())

    assert frame_corpus_revision(stamped) == revision
    assert compute_corpus_revision("docs", stamped) == revision


def test_frame_revision_rejects_mixed_generations():
    frame = _frame()
    frame["corpus_revision"] = ["docs:a", "docs:b"]

    assert frame_corpus_revision(frame) is None


def test_lexical_derivatives_record_same_revision(monkeypatch, tmp_path):
    revision = "docs:abc"
    monkeypatch.setattr(hybrid, "VECTORIZER_DIR", tmp_path)

    hybrid.train_bm25(
        "docs",
        ["첫 문서", "둘째 문서"],
        chunk_ids=["a", "b"],
        corpus_revision=revision,
    )
    fts_path = tmp_path / "lexical.db"
    build_fts_index(
        "docs",
        ["첫 문서", "둘째 문서"],
        ["a", "b"],
        db_path=fts_path,
        corpus_revision=revision,
    )

    assert hybrid.read_lexical_metadata("docs")["corpus_revision"] == revision
    assert load_fts_index("docs", db_path=fts_path).corpus_revision == revision


def test_successful_ingestion_run_records_published_revision(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        run = IngestionRun(dataset="docs", status="running")
        session.add(run)
        session.commit()
        run_id = run.id
    monkeypatch.setattr(scheduler, "SessionLocal", factory)

    scheduler._finish_ingestion_run(
        run_id,
        status="success",
        seen=2,
        corpus_revision="docs:abc",
    )

    with factory() as session:
        stored = session.get(IngestionRun, run_id)
        assert stored.corpus_revision == "docs:abc"
