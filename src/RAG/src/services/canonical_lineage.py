"""PII-free lineage gate for canonical documents and derived search indexes."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd
from sqlalchemy.orm import Session

from src.database import SessionLocal, SourceDocument
from src.pipelines.ingest import DATASET_ARTIFACTS
from src.vectorstore.chroma_client import get_collection


PUBLISHED_STATUSES = ("active", "updated")
CollectionSnapshotLoader = Callable[[str], tuple[list[str], list[dict[str, Any] | None]]]


def _identity(value: Any) -> str:
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "null"} else text


def _load_artifact_frame(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"chunk artifact is missing: {path.name}")
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype=str).fillna("")
    raise ValueError(f"unsupported chunk artifact format: {path.suffix}")


def _load_collection_snapshot(collection_name: str) -> tuple[list[str], list[dict[str, Any] | None]]:
    result = get_collection(collection_name).get(include=["metadatas"], limit=None)
    ids = [_identity(value) for value in result.get("ids", [])]
    metadatas = list(result.get("metadatas", []) or [])
    return ids, metadatas


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _dataset_report(
    session: Session,
    dataset: str,
    artifact: Any,
    collection_snapshot_loader: CollectionSnapshotLoader,
) -> dict[str, Any]:
    source_values = [
        _identity(document_key)
        for (document_key,) in (
            session.query(SourceDocument.document_key)
            .filter(
                SourceDocument.dataset == dataset,
                SourceDocument.status.in_(PUBLISHED_STATUSES),
            )
            .all()
        )
    ]
    source_keys = {value for value in source_values if value}

    frame = _load_artifact_frame(Path(artifact.chunk_path))
    required_columns = {"chunk_id", "doc_id"}
    missing_columns = sorted(required_columns - set(frame.columns))
    if missing_columns:
        raise ValueError(f"chunk artifact is missing columns: {', '.join(missing_columns)}")

    artifact_chunk_values = [_identity(value) for value in frame["chunk_id"].tolist()]
    artifact_doc_values = [_identity(value) for value in frame["doc_id"].tolist()]
    artifact_chunk_ids = {value for value in artifact_chunk_values if value}
    artifact_doc_ids = {value for value in artifact_doc_values if value}
    artifact_parent_by_chunk = {
        chunk_id: doc_id
        for chunk_id, doc_id in zip(artifact_chunk_values, artifact_doc_values)
        if chunk_id
    }

    chroma_values, metadatas = collection_snapshot_loader(str(artifact.collection))
    chroma_ids = {value for value in chroma_values if value}
    metadata_missing = 0
    metadata_doc_id_mismatch = 0
    for index, chunk_id in enumerate(chroma_values):
        metadata = metadatas[index] if index < len(metadatas) else None
        if not isinstance(metadata, dict):
            metadata_missing += 1
            metadata_doc_id = ""
        else:
            metadata_doc_id = _identity(metadata.get("doc_id"))
        if metadata_doc_id != artifact_parent_by_chunk.get(chunk_id, ""):
            metadata_doc_id_mismatch += 1

    source_artifact_delta = len(source_keys - artifact_doc_ids) + len(artifact_doc_ids - source_keys)
    artifact_chroma_delta = len(artifact_chunk_ids - chroma_ids) + len(chroma_ids - artifact_chunk_ids)
    checks = {
        "empty_source_documents": int(not source_keys),
        "empty_artifact_chunks": int(not artifact_chunk_ids),
        "empty_chroma_chunks": int(not chroma_ids),
        "source_key_blank": sum(not value for value in source_values),
        "source_key_duplicate": len([value for value in source_values if value]) - len(source_keys),
        "artifact_doc_id_blank": sum(not value for value in artifact_doc_values),
        "artifact_chunk_id_blank": sum(not value for value in artifact_chunk_values),
        "artifact_chunk_id_duplicate": (
            len([value for value in artifact_chunk_values if value]) - len(artifact_chunk_ids)
        ),
        "chroma_id_blank": sum(not value for value in chroma_values),
        "chroma_id_duplicate": len([value for value in chroma_values if value]) - len(chroma_ids),
        "source_missing_artifact": len(source_keys - artifact_doc_ids),
        "artifact_missing_source": len(artifact_doc_ids - source_keys),
        "artifact_missing_chroma": len(artifact_chunk_ids - chroma_ids),
        "chroma_missing_artifact": len(chroma_ids - artifact_chunk_ids),
        "chroma_metadata_missing": metadata_missing,
        "chroma_metadata_doc_id_mismatch": metadata_doc_id_mismatch,
    }
    violations = [
        {"dataset": dataset, "metric": metric, "count": count}
        for metric, count in checks.items()
        if count > 0
    ]
    return {
        "dataset": dataset,
        "artifact": Path(artifact.chunk_path).name,
        "collection": str(artifact.collection),
        "counts": {
            "source_documents": len(source_keys),
            "artifact_documents": len(artifact_doc_ids),
            "artifact_chunks": len(artifact_chunk_values),
            "chroma_chunks": len(chroma_values),
            **checks,
        },
        "ratios": {
            "source_artifact_mismatch": _ratio(
                source_artifact_delta,
                len(source_keys | artifact_doc_ids),
            ),
            "artifact_chroma_mismatch": _ratio(
                artifact_chroma_delta,
                len(artifact_chunk_ids | chroma_ids),
            ),
            "chroma_metadata_mismatch": _ratio(
                metadata_doc_id_mismatch,
                len(chroma_values),
            ),
        },
        "gate_passed": not violations,
        "violations": violations,
        "error": None,
    }


def build_canonical_lineage_report(
    session: Session | None = None,
    *,
    artifacts: Mapping[str, Any] | None = None,
    collection_snapshot_loader: CollectionSnapshotLoader | None = None,
) -> dict[str, Any]:
    """Compare canonical parent keys with every published search projection.

    The report contains only aggregate counts, ratios, artifact names, and
    collection names.  It never emits titles, source payloads, user queries, or
    API credentials, so it is safe to expose through readiness and CI logs.
    """
    owns_session = session is None
    active_session = session or SessionLocal()
    artifact_map = artifacts or DATASET_ARTIFACTS
    snapshot_loader = collection_snapshot_loader or _load_collection_snapshot
    datasets: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    try:
        for dataset, artifact in artifact_map.items():
            try:
                report = _dataset_report(
                    active_session,
                    str(dataset),
                    artifact,
                    snapshot_loader,
                )
            except Exception as exc:  # noqa: BLE001 - preserve per-dataset evidence for readiness.
                report = {
                    "dataset": str(dataset),
                    "artifact": Path(artifact.chunk_path).name,
                    "collection": str(artifact.collection),
                    "counts": {},
                    "ratios": {},
                    "gate_passed": False,
                    "violations": [
                        {"dataset": str(dataset), "metric": "inspection_error", "count": 1}
                    ],
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc)[:300],
                    },
                }
            datasets.append(report)
            violations.extend(report["violations"])
    finally:
        if owns_session:
            active_session.close()

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contains_user_content": False,
        "gate_passed": not violations,
        "dataset_count": len(datasets),
        "datasets": datasets,
        "violations": violations,
    }


__all__ = ["build_canonical_lineage_report"]
