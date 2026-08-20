"""Canonical payload primitives shared by collection, runtime, and indexing.

The crawler may receive a pandas row, a SQLAlchemy value, or a JSON object.  A
source document must not get a different identity merely because one of those
representations was used.  This module keeps the representation deliberately
small: normalize JSON-compatible values, serialize them deterministically, and
hash only retrieval-affecting fields.
"""
from __future__ import annotations

from datetime import date, datetime, time
import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any, Iterable


CANONICAL_PAYLOAD_SCHEMA_VERSION = 1


def normalize_payload(value: Any) -> Any:
    """Return a deterministic JSON-compatible value.

    Mapping keys are converted to strings, mappings are sorted at serialization
    time, and list order is preserved.  List order is part of a crawler's
    contract; callers that receive an unordered collection must sort it before
    passing it here.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): normalize_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_payload(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [normalize_payload(item) for item in sorted(value, key=str)]

    # pandas/numpy scalar values and other crawler-specific objects generally
    # have a stable string representation.  Avoid importing pandas into this
    # low-level contract module solely for optional scalar types.
    try:
        if hasattr(value, "item"):
            return normalize_payload(value.item())
    except (TypeError, ValueError):
        pass
    return str(value)


def canonical_json(value: Any, *, exclude_fields: Iterable[str] = ()) -> str:
    """Serialize a payload with stable key order and no insignificant spaces."""
    excluded = {str(field) for field in exclude_fields}
    normalized = normalize_payload(value)
    if isinstance(normalized, dict) and excluded:
        normalized = {
            key: item for key, item in normalized.items() if key not in excluded
        }
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(
    value: Any,
    *,
    exclude_fields: Iterable[str] = (),
    algorithm: str = "sha256",
) -> str:
    """Hash the canonical retrieval payload.

    Collection timestamps, run IDs, and parser bookkeeping should be passed in
    ``exclude_fields`` so a re-collection with unchanged content keeps the same
    source revision.
    """
    try:
        digest = hashlib.new(algorithm)
    except ValueError as exc:
        raise ValueError(f"Unsupported canonical hash algorithm: {algorithm}") from exc
    digest.update(
        canonical_json(value, exclude_fields=exclude_fields).encode("utf-8")
    )
    return digest.hexdigest()


def source_document_key(dataset: str, source_id: str) -> str:
    """Build the stable cross-dataset document identity."""
    dataset_text = str(dataset or "").strip()
    source_text = str(source_id or "").strip()
    if not dataset_text or not source_text:
        raise ValueError("dataset and source_id are required for document identity")
    return f"{dataset_text}:{source_text}"


__all__ = [
    "CANONICAL_PAYLOAD_SCHEMA_VERSION",
    "canonical_hash",
    "canonical_json",
    "normalize_payload",
    "source_document_key",
]
