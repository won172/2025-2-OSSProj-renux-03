"""승인된 공지가 어휘 검색에도 즉시 걸리는지.

승인 경로는 Chroma에만 증분 upsert한다. parquet/BM25를 다시 만들지 않으면 새 공지는
정기 갱신(기본 6시간)까지 dense 전용으로 남고, RRF가 두 순위를 합치므로 절반의 점수만
받는다. 실제로 FAQ 11건이 Chroma에는 11건, parquet에는 0건인 상태가 관측됐다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import chromadb
import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api.rag_service as rag_service  # noqa: E402
from src.database import Base, SourceDocument  # noqa: E402
from src.pipelines import ingest, notices_sync  # noqa: E402
from src.search import hybrid, fts_index  # noqa: E402
from src.services.canonical_lineage import build_canonical_lineage_report  # noqa: E402
from src.vectorstore import chroma_client  # noqa: E402


@pytest.fixture(autouse=True)
def _초기화():
    rag_service._lexical_rebuild_pending.clear()
    yield
    rag_service._lexical_rebuild_pending.clear()


def _준비상태_갱신_막기(monkeypatch):
    monkeypatch.setattr(rag_service, "_refresh_data_quality_readiness", lambda: {})
    monkeypatch.setattr(rag_service, "_refresh_canonical_lineage_readiness", lambda: {})


def test_캐시_재적재가_어휘_아티팩트도_다시_만든다(monkeypatch):
    _준비상태_갱신_막기(monkeypatch)
    호출 = []
    monkeypatch.setattr(
        rag_service,
        "_rebuild_notices_lexical_artifacts",
        lambda context: 호출.append(context),
    )
    monkeypatch.setattr(rag_service, "_ensure_dataset_locked", lambda name: None)
    rag_service._datasets.pop("notices", None)

    rag_service._reload_notices_cache("approve")

    assert 호출 == ["approve"]


def test_어휘_재생성이_실패해도_캐시_재적재는_진행된다(monkeypatch):
    _준비상태_갱신_막기(monkeypatch)
    def 폭발(context):
        raise RuntimeError("디스크 오류")

    재적재 = []
    monkeypatch.setattr(rag_service, "_rebuild_notices_lexical_artifacts", 폭발)
    monkeypatch.setattr(
        rag_service, "_ensure_dataset_locked", lambda name: 재적재.append(name)
    )
    rag_service._datasets.pop("notices", None)

    # DB·Chroma에는 이미 반영됐으므로 승인 자체를 실패시키지 않는다.
    rag_service._reload_notices_cache("approve")

    assert 재적재 == ["notices"]


def test_연속_승인은_재생성을_한_번으로_합친다(monkeypatch):
    실행 = []

    def 가짜_갱신():
        실행.append("build")

    monkeypatch.setattr(
        rag_service, "refresh_notice_artifacts", 가짜_갱신,
    )

    rag_service._rebuild_notices_lexical_artifacts("approve-1")
    assert 실행 == ["build"]

    # 락을 잡지 못해 대기하다 들어온 두 번째 요청은 플래그가 이미 내려가 있으면
    # 재생성을 건너뛴다. 여기서는 순차 호출이므로 매번 새 요청으로 취급된다.
    rag_service._rebuild_notices_lexical_artifacts("approve-2")
    assert 실행 == ["build", "build"]


def test_admin_refresh_updates_dense_revision_and_preserves_unchanged_vectors(monkeypatch, tmp_path):
    """A metadata-only approval refresh must keep the strict gate passing."""
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    monkeypatch.setenv("RAG_COLLECTION_POINTER_FILE", str(tmp_path / "pointers.json"))
    monkeypatch.setattr(chroma_client, "get_client", lambda: client)
    chroma_client.get_collection.cache_clear()
    collection_name = notices_sync.NOTICE_COLLECTION
    collection = client.create_collection(collection_name, metadata={"hnsw:space": "cosine"})
    artifact = ingest.DatasetArtifacts("notices", collection_name, tmp_path / "notices.parquet")
    monkeypatch.setitem(ingest.DATASET_ARTIFACTS, "notices", artifact)
    monkeypatch.setattr(hybrid, "VECTORIZER_DIR", tmp_path / "vectorizers")
    monkeypatch.setattr(ingest, "encode_texts", lambda texts: np.asarray([[1.0, 2.0, 3.0] for _ in texts]))
    monkeypatch.setattr(notices_sync, "RAG_NOTICES_INCREMENTAL_EMBED", True)
    frame = pd.DataFrame([
        {"chunk_id": "one", "doc_id": "notices:one", "title": "장학 안내",
         "chunk_text": "장학 신청 방법", "source": "notices", "url": "https://www.dongguk.edu/old"},
    ])
    initial, _, _ = ingest._persist_chunks("notices", collection_name, frame)
    before = collection.get(include=["documents", "metadatas", "embeddings"])
    changed = frame.copy()
    changed.loc[0, "url"] = "https://www.dongguk.edu/new"
    # The old API helper imported these functions directly from ingest; patch
    # both import sites so this regression fails against that old path too.
    monkeypatch.setattr(ingest, "build_notice_index_frame_from_db", lambda: changed.copy())
    monkeypatch.setattr(notices_sync, "build_notice_index_frame_from_db", lambda: changed.copy())
    monkeypatch.setattr(ingest, "encode_texts", lambda _texts: pytest.fail("metadata-only refresh re-embedded unchanged text"))

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add(SourceDocument(dataset="notices", source_type="fixture", source_id="one",
                                   document_key="notices:one", status="active"))
        session.commit()
        assert build_canonical_lineage_report(session, artifacts={"notices": artifact})["gate_passed"]

        rag_service._rebuild_notices_lexical_artifacts("approval-regression")

        persisted = pd.read_parquet(artifact.chunk_path)
        revision = persisted["corpus_revision"].item()
        assert revision != initial["corpus_revision"].item()
        after = collection.get(include=["documents", "metadatas", "embeddings"])
        assert after["metadatas"][0]["corpus_revision"] == revision
        assert after["metadatas"][0]["url"] == changed.iloc[0]["url"]
        assert after["documents"] == before["documents"]
        np.testing.assert_array_equal(after["embeddings"], before["embeddings"])
        assert hybrid.read_lexical_metadata("notices")["corpus_revision"] == revision
        assert fts_index.load_fts_index("notices").corpus_revision == revision
        assert build_canonical_lineage_report(session, artifacts={"notices": artifact})["gate_passed"]
    engine.dispose()
