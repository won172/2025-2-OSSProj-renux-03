"""Staged, resumable dense-index rebuild for the notices corpus."""
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
    EMBEDDING_INPUT_FIELD,
    chroma_embedding_payload,
    with_embedding_input,
)
from src.services.maintenance_lock import maintenance_lock
from src.vectorstore.chroma_client import get_client, get_collection
from src.vectorstore.collection_pointer import (
    default_pointer_path,
    read_pointer_state,
    resolve_collection_name,
    write_collection_pointer,
)


LOGGER = logging.getLogger(__name__)
LOGICAL_COLLECTION = DATASET_ARTIFACTS["notices"].collection
DEFAULT_CHECKPOINT_DIR = config.ARTIFACT_DIR / "rebuilds" / "notices"
MIN_BATCH_SIZE = 256
MAX_BATCH_SIZE = 500
NOTICE_REPRESENTATIVE_QUERIES = (
    "수강신청 일정",
    "국가장학금 신청",
    "교내 장학 안내",
    "등록금 납부 기간",
    "휴학 신청 방법",
    "복학 신청 기간",
    "졸업 요건 안내",
    "다전공 신청",
    "교환학생 모집",
    "국제교류 프로그램",
    "기숙사 모집",
    "학생증 발급",
    "도서관 운영시간",
    "취업 추천채용",
    "현장실습 모집",
    "봉사활동 모집",
    "중앙동아리 모집",
    "건강검진 안내",
    "예비군 훈련",
    "교내 행사 안내",
)


class DenseBuildError(RuntimeError):
    pass


class ArtifactChangedError(DenseBuildError):
    pass


class BuildVerificationError(DenseBuildError):
    pass


class DenseBuildStore(Protocol):
    def collection_exists(self, name: str) -> bool: ...
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


class ChromaDenseBuildStore:
    def collection_exists(self, name: str) -> bool:
        try:
            get_client().get_collection(name=name)
            return True
        except chromadb.errors.NotFoundError:
            return False

    def ensure_collection(self, name: str, metadata: Mapping[str, object]) -> None:
        client = get_client()
        try:
            client.get_collection(name=name)
        except chromadb.errors.NotFoundError:
            client.create_collection(name=name, metadata=dict(metadata))
            get_collection.cache_clear()

    def count(self, name: str) -> int:
        return int(get_collection(name).count())

    def ids(self, name: str) -> list[str]:
        result = get_collection(name).get(include=[], limit=None)
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
        get_collection(name).upsert(
            ids=list(ids),
            documents=list(documents),
            metadatas=list(metadatas),
            embeddings=np.asarray(embeddings).tolist(),
        )

    def embedding_dimensions(self, name: str, *, batch_size: int = 500) -> tuple[set[int], int]:
        collection = get_collection(name)
        dimensions: set[int] = set()
        seen = 0
        while True:
            result = collection.get(include=["embeddings"], limit=batch_size, offset=seen)
            ids = result.get("ids") or []
            embeddings = result.get("embeddings")
            if not ids:
                break
            if embeddings is None or len(embeddings) != len(ids):
                raise BuildVerificationError("collection returned incomplete embeddings")
            for vector in embeddings:
                dimensions.add(len(vector))
            seen += len(ids)
        return dimensions, seen

    def query(self, name: str, *, query_embeddings: np.ndarray, n_results: int) -> dict[str, Any]:
        return get_collection(name).query(
            query_embeddings=np.asarray(query_embeddings).tolist(),
            n_results=n_results,
            include=["distances"],
        )


@dataclass(frozen=True)
class NoticeChunkSnapshot:
    path: Path
    frame: pd.DataFrame
    artifact_sha256: str
    expected_ids_sha256: str
    expected_rows_sha256: str

    @property
    def count(self) -> int:
        return len(self.frame)


class GracefulStop(AbstractContextManager["GracefulStop"]):
    """Turn SIGTERM/SIGINT into a request to stop after the current batch."""

    def __init__(self) -> None:
        self.requested = False
        self.signal_number: int | None = None
        self._previous: dict[int, Any] = {}

    def _handle(self, signal_number: int, _frame) -> None:
        self.requested = True
        self.signal_number = signal_number
        LOGGER.warning("received signal %s; pausing after the current safe batch", signal_number)

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


def _resolve_artifact_path(path: Path | None = None) -> Path:
    if path is not None:
        resolved = path.resolve()
    else:
        artifacts = DATASET_ARTIFACTS["notices"]
        resolved = artifacts.chunk_path.resolve()
        if not resolved.exists() and artifacts.csv_path.exists():
            resolved = artifacts.csv_path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"notice chunk artifact not found: {resolved}")
    return resolved


def load_notice_chunk_snapshot(path: Path | None = None) -> NoticeChunkSnapshot:
    artifact_path = _resolve_artifact_path(path)
    artifact_sha256 = _sha256_file(artifact_path)
    frame = pd.read_csv(artifact_path) if artifact_path.suffix.lower() == ".csv" else pd.read_parquet(artifact_path)
    missing_columns = {"chunk_id", "chunk_text"} - set(frame.columns)
    if missing_columns:
        raise DenseBuildError(f"notice artifact is missing columns: {sorted(missing_columns)}")
    frame = frame.copy()
    frame["chunk_id"] = frame["chunk_id"].astype(str)
    frame["chunk_text"] = frame["chunk_text"].fillna("").astype(str)
    if frame.empty:
        raise DenseBuildError("notice artifact contains no chunks")
    duplicate_ids = frame.loc[frame["chunk_id"].duplicated(keep=False), "chunk_id"].tolist()
    if duplicate_ids:
        raise DenseBuildError(f"notice artifact contains duplicate chunk IDs: {duplicate_ids[:20]}")
    if (frame["chunk_text"].str.strip() == "").any():
        failed = frame.loc[frame["chunk_text"].str.strip() == "", "chunk_id"].tolist()
        raise DenseBuildError(f"notice artifact contains blank chunk text: {failed[:20]}")
    frame = with_embedding_input(frame)
    if (frame[EMBEDDING_INPUT_FIELD].str.strip() == "").any():
        failed = frame.loc[frame[EMBEDDING_INPUT_FIELD].str.strip() == "", "chunk_id"].tolist()
        raise DenseBuildError(f"notice artifact contains blank embedding input: {failed[:20]}")

    sorted_ids = sorted(frame["chunk_id"].tolist())
    # Covers the embedded text too, so an enrichment change between pause and
    # resume (or build and activation) is refused.
    row_values = (
        f"{chunk_id}\0{text}\0{embedding_input}"
        for chunk_id, text, embedding_input in zip(
            frame["chunk_id"].tolist(),
            frame["chunk_text"].tolist(),
            frame[EMBEDDING_INPUT_FIELD].tolist(),
        )
    )
    return NoticeChunkSnapshot(
        path=artifact_path,
        frame=frame,
        artifact_sha256=artifact_sha256,
        expected_ids_sha256=_hash_values(sorted_ids),
        expected_rows_sha256=_hash_values(row_values),
    )


def _embedding_configuration() -> dict[str, Any]:
    return {
        "model": config.EMBED_MODEL_NAME,
        "revision": config.EMBED_MODEL_REVISION,
        "device": config.EMBED_DEVICE,
        "passage_prefix": config.EMBED_PASSAGE_PREFIX,
        "normalize": True,
        # Checkpoints/build IDs from the former chunk_text representation must
        # not be resumed, verified or activated as a retrieval_text build
        # (_validate_resume and verify_notice_dense_build refuse them).
        "input_field": EMBEDDING_INPUT_FIELD,
    }


def _safe_build_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-_")
    if not safe:
        raise ValueError("build_id must contain at least one letter or digit")
    return safe[:80]


def default_build_id(snapshot: NoticeChunkSnapshot) -> str:
    config_hash = hashlib.sha256(
        json.dumps(_embedding_configuration(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"notices-{snapshot.artifact_sha256[:12]}-{config_hash[:8]}"


def build_collection_name(build_id: str) -> str:
    return f"{LOGICAL_COLLECTION}__build__{_safe_build_id(build_id)}"


def checkpoint_path(build_id: str, checkpoint_dir: Path | None = None) -> Path:
    return (checkpoint_dir or DEFAULT_CHECKPOINT_DIR) / f"{_safe_build_id(build_id)}.json"


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


def load_checkpoint(build_id: str, checkpoint_dir: Path | None = None) -> dict[str, Any]:
    path = checkpoint_path(build_id, checkpoint_dir)
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DenseBuildError(f"checkpoint not found: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DenseBuildError(f"checkpoint is unreadable: {path}") from exc
    if not isinstance(checkpoint, dict) or checkpoint.get("schema_version") != 1:
        raise DenseBuildError(f"unsupported checkpoint schema: {path}")
    return checkpoint


def _new_checkpoint(snapshot: NoticeChunkSnapshot, build_id: str, batch_size: int) -> dict[str, Any]:
    physical_name = build_collection_name(build_id)
    base_collection = resolve_collection_name(LOGICAL_COLLECTION)
    if physical_name == base_collection:
        raise DenseBuildError("build collection cannot be the active collection")
    return {
        "schema_version": 1,
        "dataset": "notices",
        "build_id": build_id,
        "logical_collection": LOGICAL_COLLECTION,
        "build_collection": physical_name,
        "base_collection": base_collection,
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
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "verification": None,
    }


def _validate_resume(checkpoint: dict[str, Any], snapshot: NoticeChunkSnapshot, batch_size: int) -> None:
    expected = {
        "source_artifact_sha256": snapshot.artifact_sha256,
        "expected_count": snapshot.count,
        "expected_ids_sha256": snapshot.expected_ids_sha256,
        "expected_rows_sha256": snapshot.expected_rows_sha256,
        "embedding": _embedding_configuration(),
        "batch_size": batch_size,
    }
    mismatches = [key for key, value in expected.items() if checkpoint.get(key) != value]
    if mismatches:
        raise ArtifactChangedError(f"build resume inputs changed: {mismatches}")
    completed = int(checkpoint.get("completed_count", 0))
    if not 0 <= completed <= snapshot.count or completed % batch_size != 0 and completed != snapshot.count:
        raise DenseBuildError(f"invalid completed_count in checkpoint: {completed}")


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
    # Embed exactly what live ingest embeds (retrieval_text), and certify the
    # reuse hash over that same text so live ingest can reuse these vectors.
    ids = frame["chunk_id"].astype(str).tolist()
    documents, metadatas = chroma_embedding_payload(
        frame, preparation="rebuild", metadata_value=_metadata_value
    )
    return ids, documents, metadatas


def _progress_payload(
    checkpoint: dict[str, Any],
    *,
    started_at: float,
    started_count: int,
) -> dict[str, Any]:
    elapsed = max(time.monotonic() - started_at, 1e-9)
    completed = int(checkpoint["completed_count"])
    expected = int(checkpoint["expected_count"])
    speed = max(0, completed - started_count) / elapsed
    remaining = max(0, expected - completed)
    eta_seconds = None if speed <= 0 else round(remaining / speed, 1)
    return {
        "event": "notices_dense_rebuild_progress",
        "build_id": checkpoint["build_id"],
        "collection": checkpoint["build_collection"],
        "completed": completed,
        "total": expected,
        "percent": round(completed / expected * 100, 2),
        "chunks_per_second": round(speed, 3),
        "eta_seconds": eta_seconds,
        "failed_ids": checkpoint.get("failed_ids", []),
    }


def build_notice_dense_index(
    *,
    artifact_path: Path | None = None,
    build_id: str | None = None,
    batch_size: int = MIN_BATCH_SIZE,
    checkpoint_dir: Path | None = None,
    store: DenseBuildStore | None = None,
    encode_documents: Callable[[Iterable[str]], np.ndarray] = encode_texts,
    encode_representative_queries: Callable[[Iterable[str]], np.ndarray] = encode_queries,
    representative_queries: Sequence[str] = NOTICE_REPRESENTATIVE_QUERIES,
    should_stop: Callable[[], bool] | None = None,
    enforce_batch_range: bool = True,
) -> dict[str, Any]:
    """Build and verify a physical collection without touching the active one."""
    if enforce_batch_range and not MIN_BATCH_SIZE <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between {MIN_BATCH_SIZE} and {MAX_BATCH_SIZE}")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    snapshot = load_notice_chunk_snapshot(artifact_path)
    build_id = _safe_build_id(build_id or default_build_id(snapshot))
    cp_path = checkpoint_path(build_id, checkpoint_dir)
    store = store or ChromaDenseBuildStore()
    is_new = not cp_path.exists()
    checkpoint = _new_checkpoint(snapshot, build_id, batch_size) if is_new else load_checkpoint(build_id, checkpoint_dir)
    if not is_new:
        _validate_resume(checkpoint, snapshot, batch_size)

    build_collection = str(checkpoint["build_collection"])
    if resolve_collection_name(LOGICAL_COLLECTION) == build_collection:
        raise DenseBuildError("refusing to write to the active collection")
    if is_new and store.collection_exists(build_collection) and store.count(build_collection) > 0:
        raise DenseBuildError("orphaned build collection is non-empty; choose a new build ID")

    checkpoint["status"] = "building"
    checkpoint["failed_ids"] = []
    checkpoint["last_error"] = None
    checkpoint["updated_at"] = _utc_now()
    _write_json_atomic(cp_path, checkpoint)
    store.ensure_collection(
        build_collection,
        {
            "hnsw:space": "cosine",
            "rag:dataset": "notices",
            "rag:build_id": build_id,
            "rag:source_sha256": snapshot.artifact_sha256,
        },
    )

    started_at = time.monotonic()
    offset = int(checkpoint["completed_count"])
    started_count = offset
    while offset < snapshot.count:
        if should_stop is not None and should_stop():
            checkpoint["status"] = "paused"
            checkpoint["updated_at"] = _utc_now()
            _write_json_atomic(cp_path, checkpoint)
            return checkpoint
        end = min(offset + batch_size, snapshot.count)
        batch = snapshot.frame.iloc[offset:end]
        ids, documents, metadatas = _batch_payload(batch)
        try:
            embeddings = np.asarray(encode_documents(documents))
            if embeddings.ndim != 2 or embeddings.shape[0] != len(ids) or embeddings.shape[1] <= 0:
                raise DenseBuildError(
                    f"invalid embedding shape for batch {offset}:{end}: {embeddings.shape}"
                )
            dimension = int(embeddings.shape[1])
            expected_dimension = checkpoint.get("embedding_dimension")
            if expected_dimension is not None and int(expected_dimension) != dimension:
                raise DenseBuildError(
                    f"embedding dimension changed: {expected_dimension} -> {dimension}"
                )
            checkpoint["embedding_dimension"] = dimension
            store.upsert(
                build_collection,
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
                        "event": "notices_dense_rebuild_batch_failed",
                        "build_id": build_id,
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
        progress = _progress_payload(
            checkpoint,
            started_at=started_at,
            started_count=started_count,
        )
        checkpoint["progress"] = progress
        _write_json_atomic(cp_path, checkpoint)
        LOGGER.info(json.dumps(progress, ensure_ascii=False))

    if _sha256_file(snapshot.path) != snapshot.artifact_sha256:
        checkpoint["status"] = "failed"
        checkpoint["last_error"] = "source artifact changed during build"
        checkpoint["updated_at"] = _utc_now()
        _write_json_atomic(cp_path, checkpoint)
        raise ArtifactChangedError(checkpoint["last_error"])
    checkpoint["status"] = "built"
    checkpoint["updated_at"] = _utc_now()
    _write_json_atomic(cp_path, checkpoint)
    return verify_notice_dense_build(
        build_id=build_id,
        checkpoint_dir=checkpoint_dir,
        store=store,
        encode_representative_queries=encode_representative_queries,
        representative_queries=representative_queries,
    )


def verify_notice_dense_build(
    *,
    build_id: str,
    checkpoint_dir: Path | None = None,
    store: DenseBuildStore | None = None,
    encode_representative_queries: Callable[[Iterable[str]], np.ndarray] = encode_queries,
    representative_queries: Sequence[str] = NOTICE_REPRESENTATIVE_QUERIES,
) -> dict[str, Any]:
    checkpoint = load_checkpoint(build_id, checkpoint_dir)
    if checkpoint.get("embedding") != _embedding_configuration():
        raise BuildVerificationError(
            "build embedding configuration does not match the current configuration "
            f"(checkpoint={checkpoint.get('embedding')!r}); rebuild with a new build_id"
        )
    store = store or ChromaDenseBuildStore()
    collection = str(checkpoint["build_collection"])
    if not store.collection_exists(collection):
        raise BuildVerificationError(f"build collection is missing: {collection}")
    actual_ids = store.ids(collection)
    actual_count = store.count(collection)
    actual_id_set = set(actual_ids)
    source_snapshot = load_notice_chunk_snapshot(Path(checkpoint["source_artifact"]))
    expected_ids = set(source_snapshot.frame["chunk_id"].astype(str).tolist())
    duplicate_ids = len(actual_ids) - len(actual_id_set)
    missing_ids = sorted(expected_ids - actual_id_set)
    unexpected_ids = sorted(actual_id_set - expected_ids)
    actual_ids_sha256 = _hash_values(sorted(actual_id_set))
    dimensions, embedded_count = store.embedding_dimensions(collection)
    expected_dimension = int(checkpoint.get("embedding_dimension") or 0)

    errors: list[str] = []
    if source_snapshot.artifact_sha256 != checkpoint["source_artifact_sha256"]:
        errors.append("source_artifact_sha256_mismatch")
    if source_snapshot.expected_rows_sha256 != checkpoint.get("expected_rows_sha256"):
        errors.append("expected_rows_sha256_mismatch")
    if actual_count != int(checkpoint["expected_count"]):
        errors.append("collection_count_mismatch")
    if duplicate_ids:
        errors.append("duplicate_collection_ids")
    if missing_ids:
        errors.append("missing_collection_ids")
    if unexpected_ids:
        errors.append("unexpected_collection_ids")
    if actual_ids_sha256 != checkpoint["expected_ids_sha256"]:
        errors.append("collection_id_hash_mismatch")
    if embedded_count != actual_count:
        errors.append("embedding_count_mismatch")
    if dimensions != {expected_dimension}:
        errors.append("embedding_dimension_mismatch")

    searches: list[dict[str, Any]] = []
    if len(representative_queries) != 20 and representative_queries is NOTICE_REPRESENTATIVE_QUERIES:
        errors.append("representative_query_contract_mismatch")
    if representative_queries and not errors:
        query_vectors = np.asarray(encode_representative_queries(representative_queries))
        if query_vectors.ndim != 2 or query_vectors.shape != (len(representative_queries), expected_dimension):
            errors.append("representative_query_dimension_mismatch")
        else:
            results = store.query(
                collection,
                query_embeddings=query_vectors,
                n_results=min(5, actual_count),
            )
            result_ids = results.get("ids") or []
            result_distances = results.get("distances") or []
            if len(result_ids) != len(representative_queries):
                errors.append("representative_search_result_count_mismatch")
            else:
                for index, query in enumerate(representative_queries):
                    ids = [str(value) for value in result_ids[index]]
                    distances = result_distances[index] if index < len(result_distances) else []
                    valid = bool(ids) and set(ids).issubset(expected_ids) and all(
                        math.isfinite(float(distance)) for distance in distances
                    )
                    if not valid:
                        errors.append(f"representative_search_failed:{index}")
                    searches.append({"query": query, "ids": ids, "valid": valid})

    verification = {
        "verified_at": _utc_now(),
        "expected_count": int(checkpoint["expected_count"]),
        "actual_count": actual_count,
        "duplicate_ids": duplicate_ids,
        "missing_count": len(missing_ids),
        "missing_ids": missing_ids[:100],
        "unexpected_count": len(unexpected_ids),
        "unexpected_ids": unexpected_ids[:100],
        "expected_ids_sha256": checkpoint["expected_ids_sha256"],
        "actual_ids_sha256": actual_ids_sha256,
        "embedding_dimension": expected_dimension,
        "observed_embedding_dimensions": sorted(dimensions),
        "representative_searches": searches,
        "errors": errors,
        "passed": not errors,
    }
    checkpoint["verification"] = verification
    checkpoint["status"] = "verified" if not errors else "verification_failed"
    checkpoint["updated_at"] = _utc_now()
    _write_json_atomic(checkpoint_path(build_id, checkpoint_dir), checkpoint)
    if errors:
        raise BuildVerificationError(f"dense build verification failed: {errors}")
    return checkpoint


def activate_notice_dense_build(
    *,
    build_id: str,
    confirm_build_id: str,
    checkpoint_dir: Path | None = None,
    pointer_path: Path | None = None,
    lock_path: Path | None = None,
    store: DenseBuildStore | None = None,
    encode_representative_queries: Callable[[Iterable[str]], np.ndarray] = encode_queries,
    representative_queries: Sequence[str] = NOTICE_REPRESENTATIVE_QUERIES,
) -> dict[str, Any]:
    if confirm_build_id != build_id:
        raise DenseBuildError("activation confirmation must exactly match build_id")
    store = store or ChromaDenseBuildStore()
    pointer_path = pointer_path or default_pointer_path()
    with maintenance_lock(path=lock_path, blocking=False):
        checkpoint = verify_notice_dense_build(
            build_id=build_id,
            checkpoint_dir=checkpoint_dir,
            store=store,
            encode_representative_queries=encode_representative_queries,
            representative_queries=representative_queries,
        )
        current = resolve_collection_name(LOGICAL_COLLECTION, pointer_path)
        if current != checkpoint["base_collection"]:
            raise DenseBuildError(
                f"active collection changed since build started: {checkpoint['base_collection']} -> {current}"
            )
        target = str(checkpoint["build_collection"])
        write_collection_pointer(
            LOGICAL_COLLECTION,
            target,
            previous_name=current,
            build_id=build_id,
            source_artifact_sha256=checkpoint["source_artifact_sha256"],
            path=pointer_path,
        )
        try:
            if resolve_collection_name(LOGICAL_COLLECTION, pointer_path) != target:
                raise DenseBuildError("active pointer did not resolve to the verified build")
            if store.count(target) != int(checkpoint["expected_count"]):
                raise DenseBuildError("activated collection count changed during switch")
        except Exception:
            write_collection_pointer(
                LOGICAL_COLLECTION,
                current,
                previous_name=target,
                build_id=f"failed-activation-{build_id}",
                source_artifact_sha256=checkpoint["source_artifact_sha256"],
                path=pointer_path,
                rollback_of=build_id,
            )
            raise
        checkpoint["status"] = "active"
        checkpoint["activated_at"] = _utc_now()
        checkpoint["previous_collection"] = current
        checkpoint["updated_at"] = _utc_now()
        _write_json_atomic(checkpoint_path(build_id, checkpoint_dir), checkpoint)
        return {
            "status": "active",
            "build_id": build_id,
            "logical_collection": LOGICAL_COLLECTION,
            "active_collection": target,
            "previous_collection": current,
            "count": store.count(target),
        }


def rollback_notice_dense_pointer(
    *,
    confirm_active_collection: str,
    pointer_path: Path | None = None,
    lock_path: Path | None = None,
    store: DenseBuildStore | None = None,
) -> dict[str, Any]:
    store = store or ChromaDenseBuildStore()
    pointer_path = pointer_path or default_pointer_path()
    with maintenance_lock(path=lock_path, blocking=False):
        state = read_pointer_state(pointer_path)
        record = state["collections"].get(LOGICAL_COLLECTION)
        if not isinstance(record, dict):
            raise DenseBuildError("no activated notice pointer exists")
        current = str(record.get("active") or "")
        previous = str(record.get("previous") or "")
        if current != confirm_active_collection:
            raise DenseBuildError("rollback confirmation does not match the active collection")
        # The known-good pre-build state may intentionally be the current
        # sparse-degraded zero-vector collection.  It is still a valid rollback
        # target: restoring degraded reads is safer than leaving a bad build
        # active.  Only a missing physical collection is refused.
        if not previous or not store.collection_exists(previous):
            raise DenseBuildError("previous collection is missing; rollback refused")
        write_collection_pointer(
            LOGICAL_COLLECTION,
            previous,
            previous_name=current,
            build_id=f"rollback-{record.get('build_id') or 'unknown'}",
            source_artifact_sha256=str(record.get("source_artifact_sha256") or ""),
            path=pointer_path,
            rollback_of=str(record.get("build_id") or "unknown"),
        )
        return {
            "status": "rolled_back",
            "logical_collection": LOGICAL_COLLECTION,
            "active_collection": previous,
            "previous_collection": current,
            "count": store.count(previous),
        }


__all__ = [
    "ArtifactChangedError",
    "BuildVerificationError",
    "ChromaDenseBuildStore",
    "DenseBuildError",
    "GracefulStop",
    "LOGICAL_COLLECTION",
    "MAX_BATCH_SIZE",
    "MIN_BATCH_SIZE",
    "NOTICE_REPRESENTATIVE_QUERIES",
    "activate_notice_dense_build",
    "build_collection_name",
    "build_notice_dense_index",
    "checkpoint_path",
    "default_build_id",
    "load_checkpoint",
    "load_notice_chunk_snapshot",
    "rollback_notice_dense_pointer",
    "verify_notice_dense_build",
]
