from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import rag_service  # noqa: E402


def _fixture_candidate(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    chunks = tmp_path / "artifacts" / "chunks" / "notices.parquet"
    vectorizers = tmp_path / "artifacts" / "vectorizers"
    chunks.parent.mkdir(parents=True)
    vectorizers.mkdir(parents=True)
    chunks.write_bytes(b"candidate-chunks-v1")
    (vectorizers / "notices_tfidf.pkl").write_bytes(b"candidate-vectorizer-v1")
    (vectorizers / "manifest.json").write_text('{"version":1}\n', encoding="utf-8")

    monkeypatch.setattr(rag_service.rag_config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(rag_service.rag_config, "OPENAI_QUERY_ANALYSIS_MODEL", "query-model")
    monkeypatch.setattr(rag_service.rag_config, "OPENAI_ROUTER_MODEL", "router-model")
    monkeypatch.setattr(rag_service.rag_config, "OPENAI_EVIDENCE_MODEL", "evidence-model")
    monkeypatch.setattr(rag_service.rag_config, "OPENAI_GROUNDING_MODEL", "grounding-model")
    monkeypatch.setattr(rag_service, "VECTORIZER_DIR", vectorizers)
    monkeypatch.setattr(
        rag_service,
        "DATASET_ARTIFACTS",
        {
            "notices": SimpleNamespace(
                chunk_path=chunks,
                csv_path=tmp_path / "missing.csv",
                collection="notices",
            )
        },
    )
    monkeypatch.setattr(rag_service, "count_items", lambda collection: 42)
    monkeypatch.setenv("RAG_BUILD_REVISION", "revision-a")
    rag_service._evaluation_hash_cache.clear()
    return chunks, vectorizers


def test_evaluation_fingerprint_is_self_hashed_and_excludes_private_runtime_values(monkeypatch, tmp_path):
    _fixture_candidate(monkeypatch, tmp_path)

    payload = rag_service._build_evaluation_fingerprint()
    unsigned = {key: value for key, value in payload.items() if key != "fingerprint_sha256"}
    expected = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["fingerprint_sha256"] == expected
    assert payload["build_revision"] == "revision-a"
    assert payload["datasets"][0]["chroma_count"] == 42
    assert payload["datasets"][0]["cached_chunk_count"] is None
    assert payload["datasets"][0]["dense_index_ready"] is False
    assert payload["dense_index_ready"] is False
    assert payload["runtime_config"]["query_analysis_model"] == "query-model"
    assert payload["runtime_config"]["router_model"] == "router-model"
    assert payload["runtime_config"]["evidence_selection_model"] == "evidence-model"
    assert payload["runtime_config"]["grounding_model"] == "grounding-model"
    assert str(tmp_path) not in serialized
    assert "api_key" not in serialized.lower()
    assert "secret" not in serialized.lower()


def test_evaluation_fingerprint_changes_with_artifact_and_build_revision(monkeypatch, tmp_path):
    chunks, _ = _fixture_candidate(monkeypatch, tmp_path)
    original = rag_service._build_evaluation_fingerprint()["fingerprint_sha256"]

    chunks.write_bytes(b"candidate-chunks-v2-and-different")
    rag_service._evaluation_hash_cache.clear()
    artifact_changed = rag_service._build_evaluation_fingerprint()["fingerprint_sha256"]
    monkeypatch.setenv("RAG_BUILD_REVISION", "revision-b")
    revision_changed = rag_service._build_evaluation_fingerprint()["fingerprint_sha256"]

    assert artifact_changed != original
    assert revision_changed != artifact_changed
