from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config  # noqa: E402
from src.pipelines.ingest import DATASET_ARTIFACTS  # noqa: E402
from src.services.staged_dense_rebuild import (  # noqa: E402
    DATASETS,
    DatasetVerificationError,
    GracefulStop,
    SourceArtifactChangedError,
    UnsafeChromaPathError,
    build_staged_dataset,
    build_staged_datasets,
    checkpoint_dir,
    deterministic_build_id,
    load_dataset_snapshot,
    staged_build_status,
    validate_isolated_chroma_dir,
    verify_staged_dataset,
)


class FakeIsolatedStore:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict]] = {}
        self.metadatas: dict[str, dict] = {}

    def collection_exists(self, name: str) -> bool:
        return name in self.collections

    def collection_metadata(self, name: str):
        return dict(self.metadatas[name])

    def ensure_collection(self, name: str, metadata) -> None:
        self.collections.setdefault(name, {})
        self.metadatas.setdefault(name, dict(metadata))

    def count(self, name: str) -> int:
        return len(self.collections[name])

    def ids(self, name: str) -> list[str]:
        return list(self.collections[name])

    def upsert(self, name, *, ids, documents, metadatas, embeddings) -> None:
        collection = self.collections[name]
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

    def digest(self, name: str) -> str:
        digest = hashlib.sha256()
        for chunk_id, item in sorted(self.collections[name].items()):
            digest.update(chunk_id.encode())
            digest.update(item["document"].encode())
            digest.update(item["embedding"].tobytes())
        return digest.hexdigest()


def _artifacts(tmp_path: Path, count: int = 5) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for dataset in DATASETS:
        path = tmp_path / f"{dataset}.csv"
        pd.DataFrame(
            {
                "chunk_id": [f"{dataset}-{index}" for index in range(count)],
                "chunk_text": [f"{dataset} 테스트 본문 {index}" for index in range(count)],
                "source": [dataset] * count,
                "title": [f"{dataset} 제목 {index}" for index in range(count)],
            }
        ).to_csv(path, index=False)
        paths[dataset] = path
    return paths


def _encode(texts) -> np.ndarray:
    values = list(texts)
    return np.asarray(
        [[float(index + 1), float(len(value)), 0.25, 0.5] for index, value in enumerate(values)],
        dtype=np.float32,
    )


def _queries() -> dict[str, tuple[str, ...]]:
    return {dataset: (f"{dataset} 대표 질문",) for dataset in DATASETS}


def test_build_all_six_datasets_isolated_and_verified(tmp_path: Path):
    target = tmp_path / "new-chroma"
    artifacts = _artifacts(tmp_path)
    store = FakeIsolatedStore()

    result = build_staged_datasets(
        chroma_dir=target,
        selection="all",
        batch_size=2,
        artifact_paths=artifacts,
        store=store,
        encode_documents=_encode,
        encode_representative_queries=_encode,
        representative_queries=_queries(),
        enforce_batch_range=False,
    )

    assert result["status"] == "verified"
    assert result["activation_supported"] is False
    assert set(result["datasets"]) == set(DATASETS)
    for dataset in DATASETS:
        collection = DATASET_ARTIFACTS[dataset].collection
        record = result["datasets"][dataset]
        assert record["status"] == "verified"
        assert record["completed_count"] == record["expected_count"] == 5
        assert record["verification"]["passed"] is True
        assert store.count(collection) == 5
        assert store.metadatas[collection]["rag:dataset"] == dataset
        assert store.metadatas[collection]["rag:staged_only"] is True
    assert not (target.parent / "collection_pointers.json").exists()


def test_real_chroma_adapter_builds_and_verifies_only_the_isolated_target(tmp_path: Path):
    target = tmp_path / "real-chroma"
    artifacts = _artifacts(tmp_path, count=3)

    result = build_staged_dataset(
        chroma_dir=target,
        dataset="meals",
        batch_size=2,
        artifact_paths=artifacts,
        encode_documents=_encode,
        encode_representative_queries=_encode,
        representative_queries=_queries(),
        enforce_batch_range=False,
    )

    assert result["status"] == "verified"
    assert result["verification"]["actual_count"] == 3
    assert result["verification"]["observed_embedding_dimensions"] == [4]
    assert result["verification"]["representative_searches"][0]["valid"] is True
    assert (target / "chroma.sqlite3").is_file()
    assert not config.CHROMA_DIR.joinpath(".staged-dense-root.json").exists()


def test_pause_and_resume_matches_uninterrupted_build(tmp_path: Path):
    artifacts = _artifacts(tmp_path, count=7)
    resumed_store = FakeIsolatedStore()
    stop_checks = 0

    def pause_after_batch() -> bool:
        nonlocal stop_checks
        stop_checks += 1
        return stop_checks > 1

    paused = build_staged_dataset(
        chroma_dir=tmp_path / "resumed",
        dataset="notices",
        batch_size=2,
        artifact_paths=artifacts,
        store=resumed_store,
        encode_documents=_encode,
        encode_representative_queries=_encode,
        representative_queries=_queries(),
        should_stop=pause_after_batch,
        enforce_batch_range=False,
    )
    assert paused["status"] == "paused"
    assert paused["completed_count"] == 2

    resumed = build_staged_dataset(
        chroma_dir=tmp_path / "resumed",
        dataset="notices",
        batch_size=2,
        artifact_paths=artifacts,
        store=resumed_store,
        encode_documents=_encode,
        encode_representative_queries=_encode,
        representative_queries=_queries(),
        enforce_batch_range=False,
    )
    clean_store = FakeIsolatedStore()
    clean = build_staged_dataset(
        chroma_dir=tmp_path / "clean",
        dataset="notices",
        batch_size=2,
        artifact_paths=artifacts,
        store=clean_store,
        encode_documents=_encode,
        encode_representative_queries=_encode,
        representative_queries=_queries(),
        enforce_batch_range=False,
    )
    collection = DATASET_ARTIFACTS["notices"].collection
    assert resumed["status"] == clean["status"] == "verified"
    assert resumed_store.digest(collection) == clean_store.digest(collection)
    assert resumed["verification"]["actual_ids_sha256"] == clean["verification"]["actual_ids_sha256"]


def test_resume_rejects_source_artifact_change(tmp_path: Path):
    artifacts = _artifacts(tmp_path, count=4)
    store = FakeIsolatedStore()
    checks = 0

    def pause() -> bool:
        nonlocal checks
        checks += 1
        return checks > 1

    build_staged_dataset(
        chroma_dir=tmp_path / "target",
        dataset="rules",
        batch_size=2,
        artifact_paths=artifacts,
        store=store,
        encode_documents=_encode,
        encode_representative_queries=_encode,
        representative_queries=_queries(),
        should_stop=pause,
        enforce_batch_range=False,
    )
    frame = pd.read_csv(artifacts["rules"])
    frame.loc[len(frame)] = ["rules-new", "변경된 본문", "rules", "변경 제목"]
    frame.to_csv(artifacts["rules"], index=False)
    with pytest.raises(SourceArtifactChangedError):
        build_staged_dataset(
            chroma_dir=tmp_path / "target",
            dataset="rules",
            batch_size=2,
            artifact_paths=artifacts,
            store=store,
            encode_documents=_encode,
            encode_representative_queries=_encode,
            representative_queries=_queries(),
            enforce_batch_range=False,
        )


def test_verification_rejects_missing_id_and_wrong_dimension(tmp_path: Path):
    artifacts = _artifacts(tmp_path, count=3)
    store = FakeIsolatedStore()
    checkpoint = build_staged_dataset(
        chroma_dir=tmp_path / "target",
        dataset="staff",
        batch_size=2,
        artifact_paths=artifacts,
        store=store,
        encode_documents=_encode,
        encode_representative_queries=_encode,
        representative_queries=_queries(),
        enforce_batch_range=False,
    )
    collection = checkpoint["collection"]
    store.collections[collection].pop("staff-2")
    first = next(iter(store.collections[collection].values()))
    first["embedding"] = np.ones(3, dtype=np.float32)
    with pytest.raises(DatasetVerificationError) as error:
        verify_staged_dataset(
            chroma_dir=tmp_path / "target",
            dataset="staff",
            artifact_paths=artifacts,
            store=store,
            encode_representative_queries=_encode,
            representative_queries=_queries(),
        )
    assert "collection_count_mismatch" in str(error.value)
    assert "missing_collection_ids" in str(error.value)
    assert "embedding_dimension_mismatch" in str(error.value)


def test_all_records_dataset_failure_and_continues_other_datasets(tmp_path: Path):
    artifacts = _artifacts(tmp_path, count=2)
    store = FakeIsolatedStore()

    def fail_staff(texts):
        values = list(texts)
        # Staged builds embed retrieval_text (context header + body), so match
        # the staff body rather than the text prefix.
        if values and "staff 테스트 본문" in values[0]:
            raise RuntimeError("synthetic staff failure")
        return _encode(values)

    result = build_staged_datasets(
        chroma_dir=tmp_path / "target",
        selection="all",
        batch_size=2,
        artifact_paths=artifacts,
        store=store,
        encode_documents=fail_staff,
        encode_representative_queries=_encode,
        representative_queries=_queries(),
        enforce_batch_range=False,
    )
    assert result["status"] == "failed"
    assert result["datasets"]["staff"]["status"] == "failed"
    assert result["datasets"]["staff"]["failed_ids"] == ["staff-0", "staff-1"]
    assert "synthetic staff failure" in result["failures"]["staff"]
    assert result["datasets"]["meals"]["status"] == "verified"


def test_sigterm_request_pauses_before_any_embedding(tmp_path: Path):
    artifacts = _artifacts(tmp_path, count=2)
    stop = GracefulStop()
    stop._handle(15, None)
    result = build_staged_dataset(
        chroma_dir=tmp_path / "target",
        dataset="meals",
        batch_size=2,
        artifact_paths=artifacts,
        store=FakeIsolatedStore(),
        encode_documents=_encode,
        encode_representative_queries=_encode,
        representative_queries=_queries(),
        should_stop=lambda: stop.requested,
        enforce_batch_range=False,
    )
    assert result["status"] == "paused"
    assert result["completed_count"] == 0
    assert result["failed_ids"] == []


def test_deterministic_build_id_is_stable_for_same_source(tmp_path: Path):
    artifacts = _artifacts(tmp_path, count=2)
    first = load_dataset_snapshot("courses", artifact_paths=artifacts)
    second = load_dataset_snapshot("courses", artifact_paths=artifacts)
    assert deterministic_build_id(first) == deterministic_build_id(second)
    assert deterministic_build_id(first).startswith("courses-")


def test_live_corrupt_and_artifacts_paths_are_rejected():
    with pytest.raises(UnsafeChromaPathError):
        validate_isolated_chroma_dir(config.CHROMA_DIR)
    with pytest.raises(UnsafeChromaPathError):
        validate_isolated_chroma_dir(config.CHROMA_DIR / "nested")
    with pytest.raises(UnsafeChromaPathError):
        validate_isolated_chroma_dir(
            config.CHROMA_DIR.with_name("db_chroma.corrupt-20260721-0012")
        )
    with pytest.raises(UnsafeChromaPathError):
        validate_isolated_chroma_dir(config.ARTIFACT_DIR / "another-build")


def test_unknown_nonempty_target_is_not_adopted(tmp_path: Path):
    target = tmp_path / "unknown"
    target.mkdir()
    (target / "chroma.sqlite3").write_text("not ours", encoding="utf-8")
    with pytest.raises(UnsafeChromaPathError, match="no staged-root marker"):
        build_staged_datasets(
            chroma_dir=target,
            selection="notices",
            batch_size=2,
            artifact_paths=_artifacts(tmp_path),
            store=FakeIsolatedStore(),
            encode_documents=_encode,
            encode_representative_queries=_encode,
            representative_queries=_queries(),
            enforce_batch_range=False,
        )


def test_status_is_read_only_for_uninitialized_target(tmp_path: Path):
    target = tmp_path / "status-only"
    result = staged_build_status(target)
    assert result["root_initialized"] is False
    assert result["activation_supported"] is False
    assert not target.exists()
    assert not checkpoint_dir(target).exists()


def test_legacy_notice_checkpoints_are_never_read_or_modified(tmp_path: Path):
    legacy_dir = tmp_path / "legacy-rebuilds"
    legacy_dir.mkdir()
    gap = legacy_dir / "notices-gap.json"
    clean = legacy_dir / "notices-clean.json"
    gap.write_text("failed 8320", encoding="utf-8")
    clean.write_text("paused 1920", encoding="utf-8")
    before = {gap: gap.read_bytes(), clean: clean.read_bytes()}

    result = build_staged_datasets(
        chroma_dir=tmp_path / "brand-new-chroma",
        selection="meals",
        batch_size=2,
        artifact_paths=_artifacts(tmp_path, count=2),
        store=FakeIsolatedStore(),
        encode_documents=_encode,
        encode_representative_queries=_encode,
        representative_queries=_queries(),
        enforce_batch_range=False,
    )
    assert result["status"] == "verified"
    assert {path: path.read_bytes() for path in before} == before
    assert checkpoint_dir(tmp_path / "brand-new-chroma") != legacy_dir


def test_cli_requires_chroma_dir_and_has_no_activation_command():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "rebuild_dense_indices_isolated.py"
    spec = importlib.util.spec_from_file_location("rebuild_dense_indices_isolated", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parser = module.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["status"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--chroma-dir", "/tmp/new-chroma", "activate"])
    args = parser.parse_args(
        ["--chroma-dir", "/tmp/new-chroma", "build", "--dataset", "all", "--batch-size", "256"]
    )
    assert args.dataset == "all"
    assert args.batch_size == 256
