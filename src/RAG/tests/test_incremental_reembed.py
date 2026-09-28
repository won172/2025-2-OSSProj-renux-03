from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from types import SimpleNamespace

import chromadb
import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base, SourceDocument
from src.pipelines import ingest
from src.search import fts_index
from src.search import hybrid
from src.services import notices_dense_rebuild, staged_dense_rebuild
from src.services.canonical_lineage import build_canonical_lineage_report
from src.vectorstore import chroma_client


def _frame(*, changed: bool = False, deleted: bool = False, title: str = "학칙", url: str = "https://example.test/one") -> pd.DataFrame:
    rows = [
        {"chunk_id": "one", "doc_id": "rules:one", "chunk_text": "첫 조항 변경" if changed else "첫 조항", "title": title, "source": "rules", "url": url},
        {"chunk_id": "two", "doc_id": "rules:two", "chunk_text": "둘째 조항", "title": "장학 규정", "source": "rules", "url": "https://example.test/two"},
    ]
    return pd.DataFrame(rows[:1] if deleted else rows)


def _snapshot(client, collection: str) -> dict[str, tuple[str, dict, np.ndarray]]:
    result = client.get_collection(collection).get(include=["documents", "metadatas", "embeddings"])
    return {
        chunk_id: (result["documents"][index], result["metadatas"][index], result["embeddings"][index])
        for index, chunk_id in enumerate(result["ids"])
    }


def test_replacement_reuses_only_matching_inputs_and_matches_full_rebuild(monkeypatch, tmp_path: Path, caplog):
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    monkeypatch.setenv("RAG_COLLECTION_POINTER_FILE", str(tmp_path / "pointers.json"))
    monkeypatch.setattr(chroma_client, "get_client", lambda: client)
    chroma_client.get_collection.cache_clear()
    monkeypatch.setitem(
        ingest.DATASET_ARTIFACTS,
        "rules",
        ingest.DatasetArtifacts("rules", "incremental_rules", tmp_path / "rules.parquet"),
    )
    monkeypatch.setattr(ingest, "_train_lexical_indices", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(ingest.config, "EMBED_MODEL_REVISION", "revision-1")
    embedded: list[list[str]] = []

    def fake_encode(texts):
        batch = list(texts)
        embedded.append(batch)
        return np.asarray([
            [float(value) for value in hashlib.sha256((ingest.config.EMBED_MODEL_REVISION + text).encode()).digest()[:4]]
            for text in batch
        ], dtype=np.float32)

    monkeypatch.setattr(ingest, "encode_texts", fake_encode)
    client.get_or_create_collection("incremental_rules", metadata={"hnsw:space": "cosine"})
    with caplog.at_level(logging.INFO):
        first, _, _ = ingest._persist_replacing_collection("rules", "incremental_rules", _frame())
        initial = _snapshot(client, "incremental_rules")
        assert [len(batch) for batch in embedded] == [2]
        assert "embedding_input_hash" not in first.columns
        assert "embedding_input_field" not in first.columns
        assert not {"embedding_input_hash", "embedding_input_field"} & set(
            pd.read_parquet(tmp_path / "rules.parquet").columns
        )
        assert initial["one"][1]["embedding_input_field"] == "retrieval_text"
        assert initial["one"][1]["embedding_input_hash"] == ingest._embedding_input_hash(initial["one"][0])
        ingest.update_collection_metadata_from_frame("rules", _frame())
        assert _snapshot(client, "incremental_rules")["one"][1]["embedding_input_hash"] == initial["one"][1]["embedding_input_hash"]

        ingest._persist_replacing_collection("rules", "incremental_rules", _frame())
        assert [len(batch) for batch in embedded] == [2]

        metadata_only = _frame(url="https://example.test/updated")
        metadata_result, _, _ = ingest._persist_replacing_collection("rules", "incremental_rules", metadata_only)
        assert [len(batch) for batch in embedded] == [2]
        assert _snapshot(client, "incremental_rules")["one"][1]["url"] == "https://example.test/updated"
        # A frame returned by ingest can be reused without changing its corpus revision.
        repeated, _, _ = ingest._persist_replacing_collection("rules", "incremental_rules", metadata_result)
        assert repeated["corpus_revision"].tolist() == metadata_result["corpus_revision"].tolist()
        assert [len(batch) for batch in embedded] == [2]

        ingest._persist_replacing_collection("rules", "incremental_rules", _frame(title="개정 학칙"))
        assert [len(batch) for batch in embedded] == [2, 1]

        ingest._persist_replacing_collection("rules", "incremental_rules", _frame(changed=True))
        assert [len(batch) for batch in embedded] == [2, 1, 1]
        changed = _snapshot(client, "incremental_rules")
        np.testing.assert_array_equal(changed["two"][2], initial["two"][2])
        assert changed["one"][1]["embedding_input_hash"] != initial["one"][1]["embedding_input_hash"]

        final_frame = _frame(changed=True, deleted=True)
        ingest._persist_replacing_collection("rules", "incremental_rules", final_frame)
        assert [len(batch) for batch in embedded] == [2, 1, 1]
        assert set(_snapshot(client, "incremental_rules")) == {"one"}

        monkeypatch.setattr(ingest.config, "EMBED_MODEL_REVISION", "revision-2")
        final_frame = _frame(changed=True)
        incremental, _, _ = ingest._persist_replacing_collection("rules", "incremental_rules", final_frame)
        assert [len(batch) for batch in embedded] == [2, 1, 1, 2]
        expected = _snapshot(client, "incremental_rules")

        client.get_or_create_collection("full_rules", metadata={"hnsw:space": "cosine"})
        ingest._persist_replacing_collection("rules", "full_rules", final_frame)
        full = _snapshot(client, "full_rules")

    assert set(expected) == set(full)
    for chunk_id in expected:
        assert expected[chunk_id][:2] == full[chunk_id][:2]
        np.testing.assert_array_equal(expected[chunk_id][2], full[chunk_id][2])
    assert "reused=2 embedded=0" in caplog.text
    assert "deleted=1" in caplog.text

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add_all([
            SourceDocument(dataset="rules", source_type="fixture", source_id=name, source_url=f"fixture://{name}", document_key=f"rules:{name}", status="active")
            for name in ("one", "two")
        ])
        session.commit()
        report = build_canonical_lineage_report(
            session,
            artifacts={"rules": ingest.DatasetArtifacts("rules", "incremental_rules", tmp_path / "rules.parquet")},
        )
        assert report["gate_passed"] is True
        assert incremental["corpus_revision"].nunique() == 1

        # The legacy notice writer can leave a chunk_text vector with no input hash.
        # A metadata-only refresh must not certify it as a retrieval_text vector.
        client.get_collection("incremental_rules").upsert(
            ids=["one"], documents=["legacy chunk text"],
            metadatas=[{"doc_id": "rules:one"}], embeddings=[[1.0, 2.0, 3.0, 4.0]],
        )
        ingest.update_collection_metadata_from_frame("rules", final_frame)
        assert _snapshot(client, "incremental_rules")["one"][1]["embedding_input_hash"] == ""
    finally:
        session.close()
        engine.dispose()
        chroma_client.get_collection.cache_clear()


def test_fts_write_is_skipped_only_for_matching_revision_and_tokenizer(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(fts_index, "FTS_DIR", tmp_path)
    monkeypatch.setattr(ingest, "train_bm25", lambda *_args, **_kwargs: (None, None))
    calls: list[str] = []
    original_build = fts_index.build_fts_index

    def tracked_build(dataset, texts, ids, **kwargs):
        calls.append(kwargs["corpus_revision"])
        return original_build(dataset, texts, ids, **kwargs)

    monkeypatch.setattr(fts_index, "build_fts_index", tracked_build)
    ingest._train_lexical_indices("rules", ["첫 조항"], ["one"], corpus_revision="rules:a")
    ingest._train_lexical_indices("rules", ["첫 조항"], ["one"], corpus_revision="rules:a")
    assert calls == ["rules:a"]
    ingest._train_lexical_indices("rules", ["첫 조항 변경"], ["one"], corpus_revision="rules:b")
    assert calls == ["rules:a", "rules:b"]
    monkeypatch.setattr(fts_index, "TFIDF_TOKENIZER", "default")
    monkeypatch.setattr(hybrid, "TFIDF_TOKENIZER", "default")
    ingest._train_lexical_indices("rules", ["첫 조항 변경"], ["one"], corpus_revision="rules:b")
    assert calls == ["rules:a", "rules:b", "rules:b"]
    assert fts_index.load_fts_index("rules").tokenizer_name == "default"
    ingest._train_lexical_indices("rules", ["첫 조항 변경"], ["one"], corpus_revision="rules:b")
    assert calls == ["rules:a", "rules:b", "rules:b"]


def test_fts_rebuilds_when_korean_backend_changes_without_name_change(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(fts_index, "FTS_DIR", tmp_path)
    monkeypatch.setattr(fts_index, "TFIDF_TOKENIZER", "korean")
    monkeypatch.setattr(hybrid, "TFIDF_TOKENIZER", "korean")
    monkeypatch.setattr(ingest, "train_bm25", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(hybrid, "_load_kiwi", lambda: None)
    calls: list[str] = []
    original_build = fts_index.build_fts_index

    def tracked_build(dataset, texts, ids, **kwargs):
        calls.append(fts_index.tokenizer_backend("korean"))
        return original_build(dataset, texts, ids, **kwargs)

    monkeypatch.setattr(fts_index, "build_fts_index", tracked_build)
    ingest._train_lexical_indices("rules", ["첫 조항"], ["one"], corpus_revision="rules:a")
    ingest._train_lexical_indices("rules", ["첫 조항"], ["one"], corpus_revision="rules:a")
    assert calls == ["light_korean"]
    assert fts_index.load_fts_index("rules").tokenizer_name == "korean"

    class FakeKiwi:
        def tokenize(self, _text):
            return [SimpleNamespace(form="조항", tag="NNG")]

    monkeypatch.setattr(hybrid, "_load_kiwi", lambda: FakeKiwi())
    ingest._train_lexical_indices("rules", ["첫 조항"], ["one"], corpus_revision="rules:a")
    assert calls == ["light_korean", "kiwi"]
    assert fts_index.load_fts_index("rules").tokenizer_name == "korean"
    ingest._train_lexical_indices("rules", ["첫 조항"], ["one"], corpus_revision="rules:a")
    assert calls == ["light_korean", "kiwi"]


def test_mixed_replacement_matches_full_rebuild_and_lineage(monkeypatch, tmp_path: Path, caplog):
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    monkeypatch.setenv("RAG_COLLECTION_POINTER_FILE", str(tmp_path / "pointers.json"))
    monkeypatch.setattr(chroma_client, "get_client", lambda: client)
    chroma_client.get_collection.cache_clear()
    artifact = ingest.DatasetArtifacts("rules", "incremental_rules", tmp_path / "rules.parquet")
    monkeypatch.setitem(ingest.DATASET_ARTIFACTS, "rules", artifact)
    monkeypatch.setattr(ingest, "_train_lexical_indices", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(ingest.config, "EMBED_MODEL_REVISION", "mixed-revision")
    calls: list[list[str]] = []

    def fake_encode(texts):
        batch = list(texts)
        calls.append(batch)
        return np.asarray([
            [float(value) for value in hashlib.sha256(text.encode()).digest()[:4]]
            for text in batch
        ], dtype=np.float32)

    monkeypatch.setattr(ingest, "encode_texts", fake_encode)
    client.get_or_create_collection("incremental_rules", metadata={"hnsw:space": "cosine"})
    client.get_or_create_collection("full_rules", metadata={"hnsw:space": "cosine"})

    initial = pd.concat([_frame(), pd.DataFrame([{
        "chunk_id": "three", "doc_id": "rules:three", "chunk_text": "삭제될 조항",
        "title": "폐지 규정", "source": "rules", "url": "https://example.test/three",
    }])], ignore_index=True)
    target = pd.concat([_frame(changed=True), pd.DataFrame([{
        "chunk_id": "four", "doc_id": "rules:four", "chunk_text": "새 조항",
        "title": "신설 규정", "source": "rules", "url": "https://example.test/four",
    }])], ignore_index=True)

    try:
        ingest._persist_replacing_collection("rules", "incremental_rules", initial)
        before = _snapshot(client, "incremental_rules")
        with caplog.at_level(logging.INFO):
            ingest._persist_replacing_collection("rules", "incremental_rules", target)
        incremental = _snapshot(client, "incremental_rules")
        assert set(incremental) == {"one", "two", "four"}
        assert calls[1] == [incremental["one"][0], incremental["four"][0]]
        assert len(calls) == 2
        np.testing.assert_array_equal(incremental["two"][2], before["two"][2])
        assert "reused=1 embedded=2" in caplog.text
        assert "deleted=1" in caplog.text

        ingest._persist_replacing_collection("rules", "full_rules", target)
        full = _snapshot(client, "full_rules")
        assert len(calls) == 3 and len(calls[2]) == 3
        assert set(incremental) == set(full)
        for chunk_id in full:
            assert incremental[chunk_id][:2] == full[chunk_id][:2]
            np.testing.assert_array_equal(incremental[chunk_id][2], full[chunk_id][2])

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        try:
            session.add_all([
                SourceDocument(dataset="rules", source_type="fixture", source_id=name,
                               source_url=f"fixture://{name}", document_key=f"rules:{name}", status="active")
                for name in ("one", "two", "four")
            ])
            session.commit()
            for collection in ("incremental_rules", "full_rules"):
                report = build_canonical_lineage_report(
                    session,
                    artifacts={"rules": ingest.DatasetArtifacts("rules", collection, artifact.chunk_path)},
                )
                assert report["gate_passed"] is True, report["violations"]
        finally:
            session.close()
            engine.dispose()
    finally:
        chroma_client.get_collection.cache_clear()


def test_staged_payload_embeds_live_retrieval_text_and_is_reusable(monkeypatch, tmp_path: Path):
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    monkeypatch.setenv("RAG_COLLECTION_POINTER_FILE", str(tmp_path / "pointers.json"))
    monkeypatch.setattr(chroma_client, "get_client", lambda: client)
    chroma_client.get_collection.cache_clear()
    monkeypatch.setitem(
        ingest.DATASET_ARTIFACTS,
        "rules",
        ingest.DatasetArtifacts("rules", "incremental_rules", tmp_path / "rules.parquet"),
    )
    monkeypatch.setattr(ingest, "_train_lexical_indices", lambda *_args, **_kwargs: (None, None))
    calls: list[list[str]] = []

    def fake_encode(texts):
        batch = list(texts)
        calls.append(batch)
        return np.ones((len(batch), 4), dtype=np.float32)

    monkeypatch.setattr(ingest, "encode_texts", fake_encode)
    client.get_or_create_collection("incremental_rules", metadata={"hnsw:space": "cosine"})
    try:
        ingest._persist_replacing_collection("rules", "incremental_rules", _frame())
        frame = pd.read_parquet(tmp_path / "rules.parquet")
        assert not {"embedding_input_hash", "embedding_input_field"} & set(frame.columns)
        for payload in (staged_dense_rebuild._batch_payload, notices_dense_rebuild._batch_payload):
            ids, documents, metadatas = payload(frame)
            assert ids == ["one", "two"]
            # Staged/notices rebuilds now embed the same retrieval_text as live
            # ingest (previously chunk_text), certified with the same hash.
            assert documents == frame["retrieval_text"].tolist()
            for document, metadata in zip(documents, metadatas):
                assert metadata["embedding_input_field"] == "retrieval_text"
                assert metadata["embedding_input_hash"] == ingest._embedding_input_hash(document)
        live = _snapshot(client, "incremental_rules")
        assert live["one"][1]["embedding_input_field"] == "retrieval_text"
        assert live["one"][1]["embedding_input_hash"] == ingest._embedding_input_hash(live["one"][0])
        assert [live[chunk_id][0] for chunk_id in ids] == documents
        assert [live[chunk_id][1]["embedding_input_hash"] for chunk_id in ids] == [
            metadata["embedding_input_hash"] for metadata in metadatas
        ]
        client.get_collection("incremental_rules").upsert(
            ids=ids, documents=documents, metadatas=metadatas,
            embeddings=np.ones((len(ids), 4), dtype=np.float32),
        )
        ingest._persist_replacing_collection("rules", "incremental_rules", _frame())
        # Staged vectors are certified for the live input, so live ingest reuses
        # them instead of re-embedding (previously asserted [2, 2]).
        assert [len(batch) for batch in calls] == [2]

        ingest.persist_dataset_artifacts_only("rules", _frame())
        artifact_only = pd.read_parquet(tmp_path / "rules.parquet")
        assert not {"embedding_input_hash", "embedding_input_field"} & set(artifact_only.columns)
    finally:
        chroma_client.get_collection.cache_clear()


def test_staged_build_hash_uses_current_model_revision(monkeypatch, tmp_path: Path):
    monkeypatch.setitem(
        ingest.DATASET_ARTIFACTS,
        "rules",
        ingest.DatasetArtifacts("rules", "revision_rules", tmp_path / "rules.parquet"),
    )
    monkeypatch.setattr(ingest, "_train_lexical_indices", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(ingest.config, "EMBED_MODEL_REVISION", "revision-A")
    ingest.persist_dataset_artifacts_only("rules", _frame())
    artifact = tmp_path / "rules.parquet"
    frame = pd.read_parquet(artifact)
    assert not {"embedding_input_hash", "embedding_input_field"} & set(frame.columns)
    old_hashes = [ingest._embedding_input_hash(text) for text in frame["retrieval_text"]]

    monkeypatch.setattr(ingest.config, "EMBED_MODEL_REVISION", "revision-B")
    for payload in (staged_dense_rebuild._batch_payload, notices_dense_rebuild._batch_payload):
        ids, documents, metadatas = payload(frame)
        assert ids == ["one", "two"]
        assert documents == frame["retrieval_text"].tolist()
        for old_hash, document, metadata in zip(old_hashes, documents, metadatas):
            assert metadata["embedding_input_field"] == "retrieval_text"
            assert metadata["embedding_input_hash"] == ingest._embedding_input_hash(document)
            assert metadata["embedding_input_hash"] != old_hash

    encoded: list[list[str]] = []

    def fake_encode(texts):
        batch = list(texts)
        encoded.append(batch)
        return np.ones((len(batch), 4), dtype=np.float32)

    result = staged_dense_rebuild.build_staged_dataset(
        chroma_dir=tmp_path / "isolated-chroma",
        dataset="rules",
        batch_size=2,
        artifact_paths={"rules": artifact},
        encode_documents=fake_encode,
        encode_representative_queries=lambda texts: np.ones((len(list(texts)), 4), dtype=np.float32),
        representative_queries={"rules": ("대표 질문",)},
        enforce_batch_range=False,
    )
    assert result["status"] == "verified"
    assert encoded == [frame["retrieval_text"].tolist()]
    collection = chromadb.PersistentClient(path=str(tmp_path / "isolated-chroma")).get_collection("revision_rules")
    stored = collection.get(include=["documents", "metadatas"])
    assert len(stored["ids"]) == 2
    for document, metadata in zip(stored["documents"], stored["metadatas"]):
        assert metadata["embedding_input_field"] == "retrieval_text"
        assert metadata["embedding_input_hash"] == ingest._embedding_input_hash(document)
