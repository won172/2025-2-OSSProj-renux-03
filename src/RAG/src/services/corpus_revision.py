"""Deterministic corpus revisions shared by every search projection."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import pandas as pd


REVISION_COLUMN = "corpus_revision"
REVISION_SCHEMA_VERSION = 1


def _clean(value: Any) -> Any:
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def compute_corpus_revision(dataset: str, frame: pd.DataFrame) -> str:
    """Hash the complete retrieval projection independent of row order.

    The revision deliberately includes retrieval text and metadata, but excludes
    its own output column. A metadata-only change therefore invalidates every
    derivative just like a text change does.
    """
    dataset_name = str(dataset or "").strip()
    if not dataset_name:
        raise ValueError("dataset is required for corpus revision")
    if frame.empty or "chunk_id" not in frame.columns:
        raise ValueError("a non-empty frame with chunk_id is required")

    columns = sorted(str(column) for column in frame.columns if column != REVISION_COLUMN)
    ordered = frame.copy()
    ordered["chunk_id"] = ordered["chunk_id"].astype(str)
    ordered.sort_values("chunk_id", kind="stable", inplace=True)

    digest = hashlib.sha256()
    header = {
        "schema_version": REVISION_SCHEMA_VERSION,
        "dataset": dataset_name,
        "columns": columns,
    }
    digest.update(json.dumps(header, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    for _, row in ordered.iterrows():
        record = {column: _clean(row.get(column)) for column in columns}
        digest.update(b"\n")
        digest.update(
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    return f"{dataset_name}:{digest.hexdigest()}"


def stamp_corpus_revision(dataset: str, frame: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Return a copy whose every row carries the same corpus revision."""
    revision = compute_corpus_revision(dataset, frame)
    stamped = frame.copy()
    stamped[REVISION_COLUMN] = revision
    return stamped, revision


def frame_corpus_revision(frame: pd.DataFrame) -> str | None:
    """Return one revision only when the full frame agrees on it."""
    if frame.empty or REVISION_COLUMN not in frame.columns:
        return None
    values = {
        str(value).strip()
        for value in frame[REVISION_COLUMN].tolist()
        if str(value).strip() and str(value).strip().lower() not in {"nan", "none"}
    }
    return next(iter(values)) if len(values) == 1 else None


__all__ = [
    "REVISION_COLUMN",
    "compute_corpus_revision",
    "frame_corpus_revision",
    "stamp_corpus_revision",
]
