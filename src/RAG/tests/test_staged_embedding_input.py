"""Offline dense rebuilds must embed exactly what live ingest embeds."""
from __future__ import annotations

import json
from pathlib import Path

import chromadb
import numpy as np
import pandas as pd
import pytest

from src.pipelines import ingest
from src.services import notices_dense_rebuild, staged_dense_rebuild
from src.services.retrieval_context import enrich_retrieval_fields
from src.vectorstore import chroma_client
from src.vectorstore.collection_pointer import clear_pointer_cache


def _raw_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "chunk_id": "n-1",
                "doc_id": "notices:1",
                "chunk_text": "국가장학금 2차 신청 기간은 9월 1일부터입니다.",
                "title": "2026 국가장학금 2차 신청 안내",
                "category": "장학",
                "department": "장학지원팀",
                "source": "notices",
                "url": "https://www.dongguk.edu/notice/1",
            },
            {
                "chunk_id": "n-2",
                "doc_id": "notices:2",
                "chunk_text": "대학원생 대상 연구 윤리 교육을 실시합니다.",
                "title": "대학원 연구윤리 교육",
                "category": "학사",
                "department": "대학원",
                "source": "notices",
                "url": "https://www.dongguk.edu/notice/2",
            },
            {
                "chunk_id": "n-3",
                "doc_id": "notices:3",
                "chunk_text": "중앙동아리 신규 회원을 모집합니다.",
                "title": "중앙동아리 모집",
                "category": "행사",
                "department": "학생지원팀",
                "source": "notices",
                "url": "https://www.dongguk.edu/notice/3",
            },
        ]
    )


class _Capture:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def __call__(self, texts) -> np.ndarray:
        batch = list(texts)
        self.inputs.extend(batch)
        return np.asarray([[1.0, float(len(text)), 0.5] for text in batch], dtype=np.float32)


def _queries(texts) -> np.ndarray:
    return np.ones((len(list(texts)), 3), dtype=np.float32)


class _FakeNoticeStore:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict]] = {}

    def collection_exists(self, name: str) -> bool:
        return name in self.collections

    def ensure_collection(self, name: str, metadata) -> None:
        self.collections.setdefault(name, {})

    def count(self, name: str) -> int:
        return len(self.collections[name])

    def ids(self, name: str) -> list[str]:
        return list(self.collections[name])

    def upsert(self, name, *, ids, documents, metadatas, embeddings) -> None:
        collection = self.collections.setdefault(name, {})
        for index, chunk_id in enumerate(ids):
            collection[str(chunk_id)] = {
                "document": str(documents[index]),
                "metadata": dict(metadatas[index]),
                "embedding": np.asarray(embeddings[index], dtype=np.float32),
            }

    def embedding_dimensions(self, name: str, *, batch_size: int = 500):
        items = self.collections[name].values()
        return {len(item["embedding"]) for item in items}, len(self.collections[name])

    def query(self, name: str, *, query_embeddings, n_results: int):
        ids = list(self.collections[name])[:n_results]
        return {
            "ids": [list(ids) for _ in query_embeddings],
            "distances": [[0.1 for _ in ids] for _ in query_embeddings],
        }


def _live_ingest_inputs(monkeypatch, tmp_path: Path, frame: pd.DataFrame) -> tuple[list[str], dict]:
    client = chromadb.PersistentClient(path=str(tmp_path / "live-chroma"))
    monkeypatch.setattr(chroma_client, "get_client", lambda: client)
    chroma_client.get_collection.cache_clear()
    monkeypatch.setitem(
        ingest.DATASET_ARTIFACTS,
        "notices",
        ingest.DatasetArtifacts("notices", "live_notices", tmp_path / "live" / "notices.parquet"),
    )
    (tmp_path / "live").mkdir(exist_ok=True)
    monkeypatch.setattr(ingest, "_train_lexical_indices", lambda *_args, **_kwargs: (None, None))
    capture = _Capture()
    monkeypatch.setattr(ingest, "encode_texts", capture)
    client.get_or_create_collection("live_notices", metadata={"hnsw:space": "cosine"})
    try:
        ingest._persist_replacing_collection("notices", "live_notices", frame.copy())
        stored = client.get_collection("live_notices").get(include=["documents", "metadatas"])
    finally:
        chroma_client.get_collection.cache_clear()
    live = {
        chunk_id: (document, metadata)
        for chunk_id, document, metadata in zip(stored["ids"], stored["documents"], stored["metadatas"])
    }
    return capture.inputs, live


def _staged_inputs(tmp_path: Path, artifact: Path) -> tuple[list[str], dict]:
    capture = _Capture()
    chroma_dir = tmp_path / "isolated-chroma"
    result = staged_dense_rebuild.build_staged_dataset(
        chroma_dir=chroma_dir,
        dataset="notices",
        batch_size=2,
        artifact_paths={"notices": artifact},
        encode_documents=capture,
        encode_representative_queries=_queries,
        representative_queries={"notices": ("대표 질문",)},
        enforce_batch_range=False,
    )
    assert result["status"] == "verified"
    collection = chromadb.PersistentClient(path=str(chroma_dir)).get_collection(
        staged_dense_rebuild.DATASET_ARTIFACTS["notices"].collection
    )
    stored = collection.get(include=["documents", "metadatas"])
    return capture.inputs, {
        chunk_id: (document, metadata)
        for chunk_id, document, metadata in zip(stored["ids"], stored["documents"], stored["metadatas"])
    }


def _notice_inputs(monkeypatch, tmp_path: Path, artifact: Path) -> tuple[list[str], dict]:
    monkeypatch.setenv("RAG_COLLECTION_POINTER_FILE", str(tmp_path / "pointers.json"))
    clear_pointer_cache()
    capture = _Capture()
    store = _FakeNoticeStore()
    try:
        checkpoint = notices_dense_rebuild.build_notice_dense_index(
            artifact_path=artifact,
            build_id="embedding-input",
            batch_size=2,
            checkpoint_dir=tmp_path / "notice-checkpoints",
            store=store,
            encode_documents=capture,
            encode_representative_queries=_queries,
            representative_queries=("대표 질문",),
            enforce_batch_range=False,
        )
    finally:
        clear_pointer_cache()
    assert checkpoint["status"] == "verified"
    collection = store.collections[checkpoint["build_collection"]]
    return capture.inputs, {
        chunk_id: (item["document"], item["metadata"]) for chunk_id, item in collection.items()
    }


def _assert_certified(stored: dict) -> None:
    for document, metadata in stored.values():
        assert metadata["embedding_input_field"] == "retrieval_text"
        assert metadata["embedding_input_hash"] == ingest._embedding_input_hash(document)
        assert "retrieval_text" not in metadata
        assert "chunk_text" not in metadata


def test_raw_artifact_rebuilds_embed_same_text_as_live_ingest(monkeypatch, tmp_path: Path):
    frame = _raw_frame()
    live_inputs, live = _live_ingest_inputs(monkeypatch, tmp_path, frame)

    # An artifact without retrieval_text is enriched through the same function.
    artifact = tmp_path / "raw" / "notices.csv"
    artifact.parent.mkdir()
    frame.to_csv(artifact, index=False)

    staged_inputs, staged = _staged_inputs(tmp_path, artifact)
    notice_inputs, notices = _notice_inputs(monkeypatch, tmp_path, artifact)

    expected = enrich_retrieval_fields(frame)["retrieval_text"].tolist()
    assert live_inputs == expected
    assert staged_inputs == expected
    assert notice_inputs == expected
    assert all(text.startswith("[") and text != raw for text, raw in zip(expected, frame["chunk_text"]))

    for stored in (live, staged, notices):
        _assert_certified(stored)
    for chunk_id, (document, metadata) in live.items():
        for stored in (staged, notices):
            assert stored[chunk_id][0] == document
            assert stored[chunk_id][1]["embedding_input_hash"] == metadata["embedding_input_hash"]


def test_stale_stored_retrieval_text_is_recomputed_like_live_ingest(monkeypatch, tmp_path: Path):
    frame = _raw_frame()
    live_inputs, live = _live_ingest_inputs(monkeypatch, tmp_path, frame)

    # Simulate a parquet written by an older enrichment: stored derived fields
    # differ from what enrich_retrieval_fields produces today.
    artifact = tmp_path / "live" / "notices.parquet"
    persisted = pd.read_parquet(artifact)
    assert persisted["retrieval_text"].tolist() == live_inputs
    stale = persisted.copy()
    stale["retrieval_text"] = [f"[옛 문맥] {text}" for text in stale["chunk_text"]]
    stale["retrieval_context"] = "[옛 문맥]"
    stale_artifact = tmp_path / "stale" / "notices.parquet"
    stale_artifact.parent.mkdir()
    stale.to_parquet(stale_artifact, index=False)

    staged_inputs, staged = _staged_inputs(tmp_path, stale_artifact)
    notice_inputs, notices = _notice_inputs(monkeypatch, tmp_path, stale_artifact)

    fresh = enrich_retrieval_fields(stale)["retrieval_text"].tolist()
    assert fresh == live_inputs
    assert staged_inputs == live_inputs
    assert notice_inputs == live_inputs
    assert not any("옛 문맥" in text for text in staged_inputs + notice_inputs)
    for stored in (staged, notices):
        _assert_certified(stored)
        assert {key: value[0] for key, value in stored.items()} == {
            key: value[0] for key, value in live.items()
        }


@pytest.mark.parametrize(
    "payload",
    [staged_dense_rebuild._batch_payload, notices_dense_rebuild._batch_payload],
)
def test_stored_retrieval_text_values_never_replace_fresh_enrichment(payload):
    frame = _raw_frame()
    expected = enrich_retrieval_fields(frame)["retrieval_text"].tolist()
    partial = frame.copy()
    partial["retrieval_text"] = ["", "오래된 검색 텍스트", None]

    ids, documents, metadatas = payload(partial)

    assert ids == ["n-1", "n-2", "n-3"]
    assert documents == expected
    for document, metadata in zip(documents, metadatas):
        assert metadata["embedding_input_field"] == "retrieval_text"
        assert metadata["embedding_input_hash"] == ingest._embedding_input_hash(document)


def _changed_enrichment(monkeypatch) -> None:
    original = ingest.enrich_retrieval_fields

    def changed(frame):
        enriched = original(frame)
        enriched["retrieval_text"] = enriched["retrieval_text"] + "\n[새 문맥 규칙]"
        return enriched

    monkeypatch.setattr(ingest, "enrich_retrieval_fields", changed)


def test_staged_resume_refuses_enrichment_change(monkeypatch, tmp_path: Path):
    artifact = tmp_path / "notices.csv"
    _raw_frame().to_csv(artifact, index=False)
    kwargs = dict(
        chroma_dir=tmp_path / "isolated-chroma",
        dataset="notices",
        batch_size=2,
        artifact_paths={"notices": artifact},
        encode_documents=_Capture(),
        encode_representative_queries=_queries,
        representative_queries={"notices": ("대표 질문",)},
        enforce_batch_range=False,
    )
    calls = iter([False, True])
    paused = staged_dense_rebuild.build_staged_dataset(**kwargs, should_stop=lambda: next(calls))
    assert paused["status"] == "paused"
    assert paused["completed_count"] == 2

    _changed_enrichment(monkeypatch)
    with pytest.raises(staged_dense_rebuild.SourceArtifactChangedError, match="expected_rows_sha256"):
        staged_dense_rebuild.build_staged_dataset(**kwargs)


def test_notice_resume_refuses_enrichment_change(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("RAG_COLLECTION_POINTER_FILE", str(tmp_path / "pointers.json"))
    clear_pointer_cache()
    artifact = tmp_path / "notices.csv"
    _raw_frame().to_csv(artifact, index=False)
    store = _FakeNoticeStore()
    kwargs = dict(
        artifact_path=artifact,
        build_id="enrichment-resume",
        batch_size=2,
        checkpoint_dir=tmp_path / "checkpoints",
        store=store,
        encode_documents=_Capture(),
        encode_representative_queries=_queries,
        representative_queries=("대표 질문",),
        enforce_batch_range=False,
    )
    try:
        calls = iter([False, True])
        paused = notices_dense_rebuild.build_notice_dense_index(**kwargs, should_stop=lambda: next(calls))
        assert paused["status"] == "paused"

        _changed_enrichment(monkeypatch)
        with pytest.raises(notices_dense_rebuild.ArtifactChangedError, match="expected_rows_sha256"):
            notices_dense_rebuild.build_notice_dense_index(**kwargs)
    finally:
        clear_pointer_cache()


def test_chunk_text_era_notice_build_cannot_be_verified_or_activated(monkeypatch, tmp_path: Path):
    pointer_path = tmp_path / "pointers.json"
    monkeypatch.setenv("RAG_COLLECTION_POINTER_FILE", str(pointer_path))
    clear_pointer_cache()
    artifact = tmp_path / "notices.csv"
    _raw_frame().to_csv(artifact, index=False)
    store = _FakeNoticeStore()
    checkpoint_dir = tmp_path / "checkpoints"
    try:
        built = notices_dense_rebuild.build_notice_dense_index(
            artifact_path=artifact,
            build_id="legacy",
            batch_size=2,
            checkpoint_dir=checkpoint_dir,
            store=store,
            encode_documents=_Capture(),
            encode_representative_queries=_queries,
            representative_queries=("대표 질문",),
            enforce_batch_range=False,
        )
        assert built["status"] == "verified"
        # Rewrite the checkpoint as a pre-change (chunk_text-era) build.
        cp_path = notices_dense_rebuild.checkpoint_path("legacy", checkpoint_dir)
        legacy = json.loads(cp_path.read_text(encoding="utf-8"))
        legacy["embedding"].pop("input_field")
        cp_path.write_text(json.dumps(legacy), encoding="utf-8")

        with pytest.raises(notices_dense_rebuild.BuildVerificationError, match="embedding configuration"):
            notices_dense_rebuild.verify_notice_dense_build(
                build_id="legacy",
                checkpoint_dir=checkpoint_dir,
                store=store,
                encode_representative_queries=_queries,
                representative_queries=("대표 질문",),
            )
        with pytest.raises(notices_dense_rebuild.BuildVerificationError, match="embedding configuration"):
            notices_dense_rebuild.activate_notice_dense_build(
                build_id="legacy",
                confirm_build_id="legacy",
                checkpoint_dir=checkpoint_dir,
                pointer_path=pointer_path,
                lock_path=tmp_path / "maintenance.lock",
                store=store,
                encode_representative_queries=_queries,
                representative_queries=("대표 질문",),
            )
        assert not pointer_path.exists()
        clear_pointer_cache()
        assert notices_dense_rebuild.resolve_collection_name(notices_dense_rebuild.LOGICAL_COLLECTION) == built["base_collection"]
    finally:
        clear_pointer_cache()


def test_embedding_configuration_rejects_chunk_text_era_checkpoints():
    # Resume/verification contracts include the input field, so a checkpoint
    # created while chunk_text was embedded cannot be continued as retrieval_text.
    assert staged_dense_rebuild._embedding_configuration()["input_field"] == "retrieval_text"
    assert notices_dense_rebuild._embedding_configuration()["input_field"] == "retrieval_text"
