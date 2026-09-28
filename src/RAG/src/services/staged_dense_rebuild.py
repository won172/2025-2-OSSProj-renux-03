"""Isolated, resumable dense builds for every RAG dataset.

This module never resolves or writes an active collection pointer.  Every
operation is scoped to a caller-supplied Chroma directory that must be outside
the configured live artifacts tree.  Producing a verified build therefore
cannot activate it.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import signal
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

import chromadb
import numpy as np
import pandas as pd

from src import config
from src.models.embedding import encode_queries, encode_texts
from src.pipelines.ingest import (
    DATASET_ARTIFACTS,
    EMBEDDING_INPUT_FIELD_COLUMN,
    EMBEDDING_INPUT_HASH_COLUMN,
    _embedding_input_hash,
)


LOGGER = logging.getLogger(__name__)
DATASETS = ("notices", "rules", "schedule", "courses", "staff", "meals")
MIN_BATCH_SIZE = 256
MAX_BATCH_SIZE = 500
ROOT_MARKER_SCHEMA = 1
CHECKPOINT_SCHEMA = 1

REPRESENTATIVE_QUERIES: dict[str, tuple[str, ...]] = {
    "notices": (
        "국가장학금 신청 공지",
        "휴학 신청 안내",
        "교환학생 모집",
        "취업 추천채용",
        "중앙동아리 모집",
    ),
    "rules": (
        "졸업 학점 규정",
        "휴학 가능 기간",
        "복수전공 이수 요건",
        "성적 평가 기준",
        "학사경고 규정",
    ),
    "schedule": (
        "1학기 개강일",
        "수강신청 기간",
        "중간고사 일정",
        "기말고사 일정",
        "학위수여식 일정",
    ),
    "courses": (
        "통계학 전공 과목",
        "데이터 분석 강의",
        "교과목 학점",
        "선수과목 안내",
        "전공필수 교과목",
    ),
    "staff": (
        "학사지원팀 연락처",
        "장학 담당 부서 전화번호",
        "국제처 문의",
        "취업센터 연락처",
        "학생상담센터 전화번호",
    ),
    "meals": (
        "오늘 상록원 메뉴",
        "학생식당 점심",
        "누리터 식단",
        "솥앤누들 메뉴",
        "학식 가격",
    ),
}


class StagedDenseBuildError(RuntimeError):
    pass


class UnsafeChromaPathError(StagedDenseBuildError):
    pass


class SourceArtifactChangedError(StagedDenseBuildError):
    pass


class DatasetVerificationError(StagedDenseBuildError):
    pass


class IsolatedDenseStore(Protocol):
    def collection_exists(self, name: str) -> bool: ...
    def collection_metadata(self, name: str) -> Mapping[str, object]: ...
    def ensure_collection(self, name: str, metadata: Mapping[str, object]) -> None: ...
    def count(self, name: str) -> int: ...
    def ids(self, name: str) -> list[str]: ...
    def upsert(
        self,
        name: str,
        *,
        ids: Sequence[str],
        documents: Sequence[str],
        metadatas: Sequence[Mapping[str, object]],
        embeddings: np.ndarray,
    ) -> None: ...
    def embedding_dimensions(self, name: str, *, batch_size: int = 500) -> tuple[set[int], int]: ...
    def query(self, name: str, *, query_embeddings: np.ndarray, n_results: int) -> dict[str, Any]: ...


class PathChromaDenseStore:
    """Chroma adapter bound only to the explicitly supplied staging path."""

    def __init__(self, chroma_dir: Path) -> None:
        self.path = chroma_dir.resolve()
        self.client = chromadb.PersistentClient(path=str(self.path))

    def _collection(self, name: str):
        try:
            return self.client.get_collection(name=name)
        except chromadb.errors.NotFoundError as exc:
            raise StagedDenseBuildError(f"staged collection is missing: {name}") from exc

    def collection_exists(self, name: str) -> bool:
        try:
            self.client.get_collection(name=name)
            return True
        except chromadb.errors.NotFoundError:
            return False

    def collection_metadata(self, name: str) -> Mapping[str, object]:
        return dict(self._collection(name).metadata or {})

    def ensure_collection(self, name: str, metadata: Mapping[str, object]) -> None:
        if self.collection_exists(name):
            return
        self.client.create_collection(name=name, metadata=dict(metadata))

    def count(self, name: str) -> int:
        return int(self._collection(name).count())

    def ids(self, name: str) -> list[str]:
        result = self._collection(name).get(include=[], limit=None)
        return [str(value) for value in result.get("ids", [])]

    def upsert(
        self,
        name: str,
        *,
        ids: Sequence[str],
        documents: Sequence[str],
        metadatas: Sequence[Mapping[str, object]],
        embeddings: np.ndarray,
    ) -> None:
        self._collection(name).upsert(
            ids=list(ids),
            documents=list(documents),
            metadatas=list(metadatas),
            embeddings=np.asarray(embeddings).tolist(),
        )

    def embedding_dimensions(self, name: str, *, batch_size: int = 500) -> tuple[set[int], int]:
        collection = self._collection(name)
        dimensions: set[int] = set()
        offset = 0
        while True:
            result = collection.get(include=["embeddings"], limit=batch_size, offset=offset)
            ids = result.get("ids") or []
            embeddings = result.get("embeddings")
            if not ids:
                break
            if embeddings is None or len(embeddings) != len(ids):
                raise DatasetVerificationError("collection returned incomplete embeddings")
            dimensions.update(len(vector) for vector in embeddings)
            offset += len(ids)
        return dimensions, offset

    def query(self, name: str, *, query_embeddings: np.ndarray, n_results: int) -> dict[str, Any]:
        return self._collection(name).query(
            query_embeddings=np.asarray(query_embeddings).tolist(),
            n_results=n_results,
            include=["distances"],
        )


@dataclass(frozen=True)
class DatasetSnapshot:
    dataset: str
    collection: str
    path: Path
    frame: pd.DataFrame
    artifact_sha256: str
    expected_ids_sha256: str
    expected_rows_sha256: str

    @property
    def count(self) -> int:
        return len(self.frame)


class GracefulStop(AbstractContextManager["GracefulStop"]):
    """Request a checkpointed pause after the current batch on TERM/INT."""

    def __init__(self) -> None:
        self.requested = False
        self.signal_number: int | None = None
        self._previous: dict[int, Any] = {}

    def _handle(self, signal_number: int, _frame) -> None:
        self.requested = True
        self.signal_number = signal_number
        LOGGER.warning("signal %s received; pausing after the current batch", signal_number)

    def __enter__(self) -> "GracefulStop":
        for signal_number in (signal.SIGTERM, signal.SIGINT):
            self._previous[signal_number] = signal.getsignal(signal_number)
            signal.signal(signal_number, self._handle)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        for signal_number, previous in self._previous.items():
            signal.signal(signal_number, previous)
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_same_or_nested(path: Path, candidate_root: Path) -> bool:
    return path == candidate_root or candidate_root in path.parents or path in candidate_root.parents


def validate_isolated_chroma_dir(chroma_dir: Path) -> Path:
    """Reject live, corrupt, artifacts-tree, and overly broad targets."""
    target = chroma_dir.expanduser().resolve()
    live = config.CHROMA_DIR.resolve()
    artifacts_root = config.ARTIFACT_DIR.resolve()
    if target == Path(target.anchor):
        raise UnsafeChromaPathError("filesystem root cannot be a staged Chroma directory")
    if _is_same_or_nested(target, live):
        raise UnsafeChromaPathError(f"configured live Chroma path is forbidden: {target}")
    if target == artifacts_root or artifacts_root in target.parents:
        raise UnsafeChromaPathError(f"live artifacts tree is forbidden: {target}")
    corrupt_roots = [path.resolve() for path in live.parent.glob(f"{live.name}.corrupt-*")]
    if any(part.startswith(f"{live.name}.corrupt") for part in target.parts):
        raise UnsafeChromaPathError(f"corrupt Chroma path is forbidden: {target}")
    if any(_is_same_or_nested(target, corrupt) for corrupt in corrupt_roots):
        raise UnsafeChromaPathError(f"preserved corrupt Chroma path is forbidden: {target}")
    if target.exists() and not target.is_dir():
        raise UnsafeChromaPathError(f"staged Chroma target is not a directory: {target}")
    return target


def root_marker_path(chroma_dir: Path) -> Path:
    target = validate_isolated_chroma_dir(chroma_dir)
    return target.with_name(f".{target.name}.staged-dense-root.json")


def checkpoint_dir(chroma_dir: Path) -> Path:
    target = validate_isolated_chroma_dir(chroma_dir)
    return target.with_name(f".{target.name}.staged-dense-checkpoints")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def initialize_staged_root(chroma_dir: Path) -> Path:
    target = validate_isolated_chroma_dir(chroma_dir)
    marker_path = root_marker_path(target)
    if marker_path.exists():
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if (
            not isinstance(marker, dict)
            or marker.get("schema_version") != ROOT_MARKER_SCHEMA
            or Path(str(marker.get("chroma_dir", ""))).resolve() != target
            or marker.get("activation_supported") is not False
        ):
            raise UnsafeChromaPathError(f"invalid staged root marker: {marker_path}")
        target.mkdir(parents=True, exist_ok=True)
        return target
    if target.exists() and any(target.iterdir()):
        raise UnsafeChromaPathError(
            "non-empty target has no staged-root marker; refusing to adopt unknown Chroma data"
        )
    target.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(
        marker_path,
        {
            "schema_version": ROOT_MARKER_SCHEMA,
            "kind": "isolated-rag-dense-build",
            "chroma_dir": str(target),
            "activation_supported": False,
            "created_at": _utc_now(),
        },
    )
    return target


def require_existing_staged_root(chroma_dir: Path) -> Path:
    target = validate_isolated_chroma_dir(chroma_dir)
    marker_path = root_marker_path(target)
    if not target.is_dir() or not marker_path.is_file():
        raise UnsafeChromaPathError("staged Chroma root/marker does not exist")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if (
        not isinstance(marker, dict)
        or marker.get("schema_version") != ROOT_MARKER_SCHEMA
        or Path(str(marker.get("chroma_dir", ""))).resolve() != target
        or marker.get("activation_supported") is not False
    ):
        raise UnsafeChromaPathError("staged Chroma marker is invalid")
    return target


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hash_values(values: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(value.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _configured_artifact_path(dataset: str) -> Path:
    artifacts = DATASET_ARTIFACTS[dataset]
    path = artifacts.chunk_path.resolve()
    if not path.exists() and artifacts.csv_path.exists():
        path = artifacts.csv_path.resolve()
    return path


def load_dataset_snapshot(
    dataset: str,
    *,
    artifact_paths: Mapping[str, Path] | None = None,
) -> DatasetSnapshot:
    if dataset not in DATASETS:
        raise ValueError(f"unsupported dataset: {dataset}")
    configured = None if artifact_paths is None else artifact_paths.get(dataset)
    path = (configured.resolve() if configured is not None else _configured_artifact_path(dataset))
    if not path.is_file():
        raise FileNotFoundError(f"{dataset} chunk artifact not found: {path}")
    artifact_sha256 = _sha256_file(path)
    frame = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_parquet(path)
    missing = {"chunk_id", "chunk_text"} - set(frame.columns)
    if missing:
        raise StagedDenseBuildError(f"{dataset} artifact missing columns: {sorted(missing)}")
    frame = frame.copy()
    frame["chunk_id"] = frame["chunk_id"].astype(str)
    frame["chunk_text"] = frame["chunk_text"].fillna("").astype(str)
    if frame.empty:
        raise StagedDenseBuildError(f"{dataset} artifact contains no chunks")
    duplicates = frame.loc[frame["chunk_id"].duplicated(keep=False), "chunk_id"].tolist()
    if duplicates:
        raise StagedDenseBuildError(f"{dataset} artifact has duplicate IDs: {duplicates[:20]}")
    blanks = frame.loc[frame["chunk_text"].str.strip() == "", "chunk_id"].tolist()
    if blanks:
        raise StagedDenseBuildError(f"{dataset} artifact has blank text: {blanks[:20]}")
    ids = frame["chunk_id"].tolist()
    return DatasetSnapshot(
        dataset=dataset,
        collection=DATASET_ARTIFACTS[dataset].collection,
        path=path,
        frame=frame,
        artifact_sha256=artifact_sha256,
        expected_ids_sha256=_hash_values(sorted(ids)),
        expected_rows_sha256=_hash_values(
            f"{chunk_id}\0{text}"
            for chunk_id, text in zip(ids, frame["chunk_text"].tolist())
        ),
    )


def _embedding_configuration() -> dict[str, Any]:
    return {
        "model": config.EMBED_MODEL_NAME,
        "revision": config.EMBED_MODEL_REVISION,
        "device": config.EMBED_DEVICE,
        "passage_prefix": config.EMBED_PASSAGE_PREFIX,
        "query_prefix": config.EMBED_QUERY_PREFIX,
        "normalize": True,
    }


def deterministic_build_id(snapshot: DatasetSnapshot) -> str:
    embedding_hash = hashlib.sha256(
        json.dumps(_embedding_configuration(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"{snapshot.dataset}-{snapshot.artifact_sha256[:12]}-{embedding_hash[:8]}"


def dataset_checkpoint_path(chroma_dir: Path, dataset: str) -> Path:
    return checkpoint_dir(chroma_dir) / f"{dataset}.json"


def aggregate_manifest_path(chroma_dir: Path) -> Path:
    return checkpoint_dir(chroma_dir) / "manifest.json"


def _load_json(path: Path, *, description: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StagedDenseBuildError(f"{description} not found: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StagedDenseBuildError(f"{description} is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise StagedDenseBuildError(f"{description} is not an object: {path}")
    return payload


def load_dataset_checkpoint(chroma_dir: Path, dataset: str) -> dict[str, Any]:
    checkpoint = _load_json(dataset_checkpoint_path(chroma_dir, dataset), description=f"{dataset} checkpoint")
    if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA or checkpoint.get("dataset") != dataset:
        raise StagedDenseBuildError(f"invalid {dataset} checkpoint schema")
    return checkpoint


def _new_dataset_checkpoint(
    chroma_dir: Path,
    snapshot: DatasetSnapshot,
    batch_size: int,
) -> dict[str, Any]:
    return {
        "schema_version": CHECKPOINT_SCHEMA,
        "activation_supported": False,
        "target_chroma_dir": str(chroma_dir.resolve()),
        "dataset": snapshot.dataset,
        "collection": snapshot.collection,
        "build_id": deterministic_build_id(snapshot),
        "source_artifact": str(snapshot.path),
        "source_artifact_sha256": snapshot.artifact_sha256,
        "expected_count": snapshot.count,
        "expected_ids_sha256": snapshot.expected_ids_sha256,
        "expected_rows_sha256": snapshot.expected_rows_sha256,
        "embedding": _embedding_configuration(),
        "embedding_dimension": None,
        "batch_size": batch_size,
        "completed_count": 0,
        "completed_batches": 0,
        "failed_ids": [],
        "last_error": None,
        "status": "building",
        "verification": None,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }


def _validate_resume(
    checkpoint: dict[str, Any],
    chroma_dir: Path,
    snapshot: DatasetSnapshot,
    batch_size: int,
) -> None:
    expected = {
        "activation_supported": False,
        "target_chroma_dir": str(chroma_dir.resolve()),
        "collection": snapshot.collection,
        "build_id": deterministic_build_id(snapshot),
        "source_artifact_sha256": snapshot.artifact_sha256,
        "expected_count": snapshot.count,
        "expected_ids_sha256": snapshot.expected_ids_sha256,
        "expected_rows_sha256": snapshot.expected_rows_sha256,
        "embedding": _embedding_configuration(),
        "batch_size": batch_size,
    }
    mismatches = [key for key, value in expected.items() if checkpoint.get(key) != value]
    if mismatches:
        raise SourceArtifactChangedError(f"{snapshot.dataset} resume contract changed: {mismatches}")
    completed = int(checkpoint.get("completed_count", 0))
    if not 0 <= completed <= snapshot.count:
        raise StagedDenseBuildError(f"invalid completed_count for {snapshot.dataset}: {completed}")
    if completed != snapshot.count and completed % batch_size != 0:
        raise StagedDenseBuildError(f"non-boundary checkpoint for {snapshot.dataset}: {completed}")


def _metadata_value(value: Any) -> str | int | float | bool:
    if value is None:
        return ""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and math.isnan(value):
            return ""
        return value
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value)


def _batch_payload(frame: pd.DataFrame) -> tuple[list[str], list[str], list[dict[str, object]]]:
    ids = frame["chunk_id"].astype(str).tolist()
    documents = frame["chunk_text"].astype(str).tolist()
    metadatas = [
        {str(key): _metadata_value(value) for key, value in row.items()}
        for row in frame.drop(
            columns=["chunk_text", EMBEDDING_INPUT_HASH_COLUMN, EMBEDDING_INPUT_FIELD_COLUMN],
            errors="ignore",
        ).to_dict(orient="records")
    ]
    for document, metadata in zip(documents, metadatas):
        metadata[EMBEDDING_INPUT_FIELD_COLUMN] = "chunk_text"
        metadata[EMBEDDING_INPUT_HASH_COLUMN] = _embedding_input_hash(document, field="chunk_text")
    return ids, documents, metadatas


def _progress(checkpoint: dict[str, Any], *, started_at: float, started_count: int) -> dict[str, Any]:
    elapsed = max(time.monotonic() - started_at, 1e-9)
    completed = int(checkpoint["completed_count"])
    total = int(checkpoint["expected_count"])
    speed = max(0, completed - started_count) / elapsed
    remaining = max(0, total - completed)
    return {
        "event": "staged_dense_dataset_progress",
        "dataset": checkpoint["dataset"],
        "build_id": checkpoint["build_id"],
        "completed": completed,
        "total": total,
        "percent": round(completed / total * 100, 2),
        "chunks_per_second": round(speed, 3),
        "eta_seconds": None if speed <= 0 else round(remaining / speed, 1),
        "failed_ids": checkpoint.get("failed_ids", []),
    }


def _validate_store_ownership(
    store: IsolatedDenseStore,
    checkpoint: dict[str, Any],
    *,
    is_new: bool,
) -> None:
    collection = str(checkpoint["collection"])
    if not store.collection_exists(collection):
        if int(checkpoint.get("completed_count", 0)) > 0:
            raise StagedDenseBuildError(
                f"{checkpoint['dataset']} collection disappeared after checkpointed progress"
            )
        store.ensure_collection(
            collection,
            {
                "hnsw:space": "cosine",
                "rag:dataset": str(checkpoint["dataset"]),
                "rag:build_id": str(checkpoint["build_id"]),
                "rag:source_sha256": str(checkpoint["source_artifact_sha256"]),
                "rag:staged_only": True,
            },
        )
        return
    metadata = store.collection_metadata(collection)
    if metadata.get("rag:build_id") != checkpoint["build_id"]:
        raise StagedDenseBuildError(
            f"{checkpoint['dataset']} collection belongs to another build"
        )
    if is_new and store.count(collection) > 0:
        raise StagedDenseBuildError(
            f"{checkpoint['dataset']} collection is non-empty without a matching checkpoint"
        )


def build_staged_dataset(
    *,
    chroma_dir: Path,
    dataset: str,
    batch_size: int = MIN_BATCH_SIZE,
    artifact_paths: Mapping[str, Path] | None = None,
    store: IsolatedDenseStore | None = None,
    encode_documents: Callable[[Iterable[str]], np.ndarray] = encode_texts,
    encode_representative_queries: Callable[[Iterable[str]], np.ndarray] = encode_queries,
    representative_queries: Mapping[str, Sequence[str]] = REPRESENTATIVE_QUERIES,
    should_stop: Callable[[], bool] | None = None,
    enforce_batch_range: bool = True,
) -> dict[str, Any]:
    if dataset not in DATASETS:
        raise ValueError(f"unsupported dataset: {dataset}")
    if enforce_batch_range and not MIN_BATCH_SIZE <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between {MIN_BATCH_SIZE} and {MAX_BATCH_SIZE}")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    target = initialize_staged_root(chroma_dir)
    snapshot = load_dataset_snapshot(dataset, artifact_paths=artifact_paths)
    cp_path = dataset_checkpoint_path(target, dataset)
    is_new = not cp_path.exists()
    checkpoint = (
        _new_dataset_checkpoint(target, snapshot, batch_size)
        if is_new
        else load_dataset_checkpoint(target, dataset)
    )
    if not is_new:
        _validate_resume(checkpoint, target, snapshot, batch_size)
    store = store or PathChromaDenseStore(target)
    _validate_store_ownership(store, checkpoint, is_new=is_new)

    checkpoint["status"] = "building"
    checkpoint["failed_ids"] = []
    checkpoint["last_error"] = None
    checkpoint["updated_at"] = _utc_now()
    _write_json_atomic(cp_path, checkpoint)

    offset = int(checkpoint["completed_count"])
    started_count = offset
    started_at = time.monotonic()
    while offset < snapshot.count:
        if should_stop is not None and should_stop():
            checkpoint["status"] = "paused"
            checkpoint["updated_at"] = _utc_now()
            _write_json_atomic(cp_path, checkpoint)
            return checkpoint
        end = min(offset + batch_size, snapshot.count)
        ids, documents, metadatas = _batch_payload(snapshot.frame.iloc[offset:end])
        try:
            embeddings = np.asarray(encode_documents(documents))
            if embeddings.ndim != 2 or embeddings.shape[0] != len(ids) or embeddings.shape[1] <= 0:
                raise StagedDenseBuildError(
                    f"invalid {dataset} embedding shape at {offset}:{end}: {embeddings.shape}"
                )
            dimension = int(embeddings.shape[1])
            expected_dimension = checkpoint.get("embedding_dimension")
            if expected_dimension is not None and int(expected_dimension) != dimension:
                raise StagedDenseBuildError(
                    f"{dataset} embedding dimension changed: {expected_dimension} -> {dimension}"
                )
            checkpoint["embedding_dimension"] = dimension
            store.upsert(
                snapshot.collection,
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings,
            )
        except Exception as exc:
            checkpoint["status"] = "failed"
            checkpoint["failed_ids"] = ids
            checkpoint["last_error"] = f"{type(exc).__name__}: {exc}"
            checkpoint["updated_at"] = _utc_now()
            _write_json_atomic(cp_path, checkpoint)
            LOGGER.error(
                json.dumps(
                    {
                        "event": "staged_dense_dataset_batch_failed",
                        "dataset": dataset,
                        "range": [offset, end],
                        "failed_ids": ids,
                        "error": checkpoint["last_error"],
                    },
                    ensure_ascii=False,
                )
            )
            raise
        offset = end
        checkpoint["completed_count"] = offset
        checkpoint["completed_batches"] = int(checkpoint.get("completed_batches", 0)) + 1
        checkpoint["failed_ids"] = []
        checkpoint["last_error"] = None
        checkpoint["updated_at"] = _utc_now()
        checkpoint["progress"] = _progress(
            checkpoint,
            started_at=started_at,
            started_count=started_count,
        )
        _write_json_atomic(cp_path, checkpoint)
        LOGGER.info(json.dumps(checkpoint["progress"], ensure_ascii=False))

    if _sha256_file(snapshot.path) != snapshot.artifact_sha256:
        checkpoint["status"] = "failed"
        checkpoint["last_error"] = "source artifact changed during build"
        checkpoint["updated_at"] = _utc_now()
        _write_json_atomic(cp_path, checkpoint)
        raise SourceArtifactChangedError(checkpoint["last_error"])
    checkpoint["status"] = "built"
    checkpoint["updated_at"] = _utc_now()
    _write_json_atomic(cp_path, checkpoint)
    return verify_staged_dataset(
        chroma_dir=target,
        dataset=dataset,
        artifact_paths=artifact_paths,
        store=store,
        encode_representative_queries=encode_representative_queries,
        representative_queries=representative_queries,
    )


def verify_staged_dataset(
    *,
    chroma_dir: Path,
    dataset: str,
    artifact_paths: Mapping[str, Path] | None = None,
    store: IsolatedDenseStore | None = None,
    encode_representative_queries: Callable[[Iterable[str]], np.ndarray] = encode_queries,
    representative_queries: Mapping[str, Sequence[str]] = REPRESENTATIVE_QUERIES,
) -> dict[str, Any]:
    target = require_existing_staged_root(chroma_dir)
    checkpoint = load_dataset_checkpoint(target, dataset)
    snapshot = load_dataset_snapshot(dataset, artifact_paths=artifact_paths)
    _validate_resume(checkpoint, target, snapshot, int(checkpoint["batch_size"]))
    store = store or PathChromaDenseStore(target)
    collection = str(checkpoint["collection"])
    if not store.collection_exists(collection):
        raise DatasetVerificationError(f"{dataset} collection is missing")

    actual_ids = store.ids(collection)
    actual_id_set = set(actual_ids)
    expected_ids = set(snapshot.frame["chunk_id"].tolist())
    actual_count = store.count(collection)
    duplicate_count = len(actual_ids) - len(actual_id_set)
    missing_ids = sorted(expected_ids - actual_id_set)
    unexpected_ids = sorted(actual_id_set - expected_ids)
    actual_ids_sha256 = _hash_values(sorted(actual_id_set))
    dimensions, embedded_count = store.embedding_dimensions(collection)
    expected_dimension = int(checkpoint.get("embedding_dimension") or 0)
    errors: list[str] = []
    if snapshot.artifact_sha256 != checkpoint["source_artifact_sha256"]:
        errors.append("source_artifact_sha256_mismatch")
    if actual_count != snapshot.count:
        errors.append("collection_count_mismatch")
    if duplicate_count:
        errors.append("duplicate_collection_ids")
    if missing_ids:
        errors.append("missing_collection_ids")
    if unexpected_ids:
        errors.append("unexpected_collection_ids")
    if actual_ids_sha256 != snapshot.expected_ids_sha256:
        errors.append("collection_id_hash_mismatch")
    if embedded_count != actual_count:
        errors.append("embedding_count_mismatch")
    if dimensions != {expected_dimension}:
        errors.append("embedding_dimension_mismatch")

    searches: list[dict[str, Any]] = []
    queries = tuple(representative_queries[dataset])
    if not queries:
        errors.append("representative_queries_missing")
    if not errors:
        query_vectors = np.asarray(encode_representative_queries(queries))
        if query_vectors.shape != (len(queries), expected_dimension):
            errors.append("representative_query_dimension_mismatch")
        else:
            results = store.query(
                collection,
                query_embeddings=query_vectors,
                n_results=min(5, actual_count),
            )
            result_ids = results.get("ids") or []
            distances = results.get("distances") or []
            if len(result_ids) != len(queries):
                errors.append("representative_search_result_count_mismatch")
            else:
                for index, query in enumerate(queries):
                    ids = [str(value) for value in result_ids[index]]
                    query_distances = distances[index] if index < len(distances) else []
                    valid = bool(ids) and set(ids).issubset(expected_ids) and all(
                        math.isfinite(float(distance)) for distance in query_distances
                    )
                    if not valid:
                        errors.append(f"representative_search_failed:{index}")
                    searches.append({"query": query, "ids": ids, "valid": valid})

    verification = {
        "verified_at": _utc_now(),
        "passed": not errors,
        "expected_count": snapshot.count,
        "actual_count": actual_count,
        "duplicate_ids": duplicate_count,
        "missing_count": len(missing_ids),
        "missing_ids": missing_ids[:100],
        "unexpected_count": len(unexpected_ids),
        "unexpected_ids": unexpected_ids[:100],
        "expected_ids_sha256": snapshot.expected_ids_sha256,
        "actual_ids_sha256": actual_ids_sha256,
        "embedding_dimension": expected_dimension,
        "observed_embedding_dimensions": sorted(dimensions),
        "representative_searches": searches,
        "errors": errors,
    }
    checkpoint["verification"] = verification
    checkpoint["status"] = "verified" if not errors else "verification_failed"
    checkpoint["updated_at"] = _utc_now()
    _write_json_atomic(dataset_checkpoint_path(target, dataset), checkpoint)
    if errors:
        raise DatasetVerificationError(f"{dataset} verification failed: {errors}")
    return checkpoint


def _new_manifest(chroma_dir: Path, selection: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "activation_supported": False,
        "target_chroma_dir": str(chroma_dir.resolve()),
        "selection": selection,
        "status": "building",
        "datasets": {},
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }


def _dataset_manifest_record(checkpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": checkpoint.get("status"),
        "build_id": checkpoint.get("build_id"),
        "collection": checkpoint.get("collection"),
        "completed_count": checkpoint.get("completed_count", 0),
        "expected_count": checkpoint.get("expected_count", 0),
        "failed_ids": checkpoint.get("failed_ids", []),
        "last_error": checkpoint.get("last_error"),
        "verification": checkpoint.get("verification"),
    }


def build_staged_datasets(
    *,
    chroma_dir: Path,
    selection: str,
    batch_size: int = MIN_BATCH_SIZE,
    artifact_paths: Mapping[str, Path] | None = None,
    store: IsolatedDenseStore | None = None,
    encode_documents: Callable[[Iterable[str]], np.ndarray] = encode_texts,
    encode_representative_queries: Callable[[Iterable[str]], np.ndarray] = encode_queries,
    representative_queries: Mapping[str, Sequence[str]] = REPRESENTATIVE_QUERIES,
    should_stop: Callable[[], bool] | None = None,
    enforce_batch_range: bool = True,
) -> dict[str, Any]:
    if selection not in {*DATASETS, "all"}:
        raise ValueError(f"unsupported dataset selection: {selection}")
    target = initialize_staged_root(chroma_dir)
    store = store or PathChromaDenseStore(target)
    manifest_path = aggregate_manifest_path(target)
    manifest = (
        _load_json(manifest_path, description="aggregate build manifest")
        if manifest_path.exists()
        else _new_manifest(target, selection)
    )
    if manifest.get("target_chroma_dir") != str(target) or manifest.get("activation_supported") is not False:
        raise StagedDenseBuildError("aggregate manifest belongs to another target")
    manifest["selection"] = selection
    selected = DATASETS if selection == "all" else (selection,)
    failures: dict[str, str] = {}
    paused = False
    for dataset in selected:
        try:
            checkpoint = build_staged_dataset(
                chroma_dir=target,
                dataset=dataset,
                batch_size=batch_size,
                artifact_paths=artifact_paths,
                store=store,
                encode_documents=encode_documents,
                encode_representative_queries=encode_representative_queries,
                representative_queries=representative_queries,
                should_stop=should_stop,
                enforce_batch_range=enforce_batch_range,
            )
            manifest["datasets"][dataset] = _dataset_manifest_record(checkpoint)
            if checkpoint["status"] == "paused":
                paused = True
                manifest["status"] = "paused"
                manifest["updated_at"] = _utc_now()
                _write_json_atomic(manifest_path, manifest)
                break
        except Exception as exc:
            try:
                checkpoint = load_dataset_checkpoint(target, dataset)
                record = _dataset_manifest_record(checkpoint)
            except Exception:
                record = {
                    "status": "failed",
                    "build_id": None,
                    "collection": DATASET_ARTIFACTS[dataset].collection,
                    "completed_count": 0,
                    "expected_count": 0,
                    "failed_ids": [],
                    "verification": None,
                }
            record["status"] = "failed"
            record["last_error"] = f"{type(exc).__name__}: {exc}"
            manifest["datasets"][dataset] = record
            failures[dataset] = record["last_error"]
        manifest["updated_at"] = _utc_now()
        _write_json_atomic(manifest_path, manifest)

    if not paused:
        manifest["status"] = "failed" if failures else "verified"
    manifest["failures"] = failures
    manifest["updated_at"] = _utc_now()
    _write_json_atomic(manifest_path, manifest)
    return manifest


def verify_staged_datasets(
    *,
    chroma_dir: Path,
    selection: str,
    artifact_paths: Mapping[str, Path] | None = None,
    store: IsolatedDenseStore | None = None,
    encode_representative_queries: Callable[[Iterable[str]], np.ndarray] = encode_queries,
    representative_queries: Mapping[str, Sequence[str]] = REPRESENTATIVE_QUERIES,
) -> dict[str, Any]:
    if selection not in {*DATASETS, "all"}:
        raise ValueError(f"unsupported dataset selection: {selection}")
    target = require_existing_staged_root(chroma_dir)
    store = store or PathChromaDenseStore(target)
    selected = DATASETS if selection == "all" else (selection,)
    manifest = _new_manifest(target, selection)
    failures: dict[str, str] = {}
    for dataset in selected:
        try:
            checkpoint = verify_staged_dataset(
                chroma_dir=target,
                dataset=dataset,
                artifact_paths=artifact_paths,
                store=store,
                encode_representative_queries=encode_representative_queries,
                representative_queries=representative_queries,
            )
            manifest["datasets"][dataset] = _dataset_manifest_record(checkpoint)
        except Exception as exc:
            failures[dataset] = f"{type(exc).__name__}: {exc}"
            try:
                checkpoint = load_dataset_checkpoint(target, dataset)
                record = _dataset_manifest_record(checkpoint)
            except Exception:
                record = {"status": "failed", "failed_ids": []}
            record["status"] = "failed"
            record["last_error"] = failures[dataset]
            manifest["datasets"][dataset] = record
    manifest["status"] = "failed" if failures else "verified"
    manifest["failures"] = failures
    manifest["updated_at"] = _utc_now()
    _write_json_atomic(aggregate_manifest_path(target), manifest)
    return manifest


def staged_build_status(chroma_dir: Path) -> dict[str, Any]:
    """Read checkpoint state only; never opens or creates a Chroma client."""
    target = validate_isolated_chroma_dir(chroma_dir)
    marker = root_marker_path(target)
    cp_dir = checkpoint_dir(target)
    result: dict[str, Any] = {
        "schema_version": 1,
        "activation_supported": False,
        "target_chroma_dir": str(target),
        "root_initialized": target.is_dir() and marker.is_file(),
        "datasets": {},
    }
    if not result["root_initialized"]:
        return result
    for dataset in DATASETS:
        path = cp_dir / f"{dataset}.json"
        if path.exists():
            result["datasets"][dataset] = _load_json(path, description=f"{dataset} checkpoint")
    manifest_path = cp_dir / "manifest.json"
    result["manifest"] = (
        _load_json(manifest_path, description="aggregate manifest")
        if manifest_path.exists()
        else None
    )
    return result


__all__ = [
    "DATASETS",
    "MAX_BATCH_SIZE",
    "MIN_BATCH_SIZE",
    "REPRESENTATIVE_QUERIES",
    "DatasetVerificationError",
    "GracefulStop",
    "IsolatedDenseStore",
    "PathChromaDenseStore",
    "SourceArtifactChangedError",
    "StagedDenseBuildError",
    "UnsafeChromaPathError",
    "build_staged_dataset",
    "build_staged_datasets",
    "checkpoint_dir",
    "deterministic_build_id",
    "initialize_staged_root",
    "load_dataset_checkpoint",
    "load_dataset_snapshot",
    "staged_build_status",
    "validate_isolated_chroma_dir",
    "verify_staged_dataset",
    "verify_staged_datasets",
]
