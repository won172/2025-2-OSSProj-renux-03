"""PII-free source-document quality reporting and deployment readiness gate."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
from typing import Any

from sqlalchemy.orm import Session

from src.database import Chunk, Notice, SourceDocument


@dataclass(frozen=True)
class DataQualityThresholds:
    max_parse_error_ratio: float = 0.05
    max_category_unknown_ratio: float = 0.20
    max_inactive_ratio: float = 0.30
    max_index_mismatch_ratio: float = 0.01

    @classmethod
    def from_env(cls) -> "DataQualityThresholds":
        def value(name: str, default: float) -> float:
            raw = os.getenv(name)
            if raw is None:
                return default
            parsed = float(raw)
            if not 0.0 <= parsed <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
            return parsed

        return cls(
            max_parse_error_ratio=value("RAG_DQ_MAX_PARSE_ERROR_RATIO", cls.max_parse_error_ratio),
            max_category_unknown_ratio=value("RAG_DQ_MAX_CATEGORY_UNKNOWN_RATIO", cls.max_category_unknown_ratio),
            max_inactive_ratio=value("RAG_DQ_MAX_INACTIVE_RATIO", cls.max_inactive_ratio),
            max_index_mismatch_ratio=value("RAG_DQ_MAX_INDEX_MISMATCH_RATIO", cls.max_index_mismatch_ratio),
        )


def data_quality_mode() -> str:
    mode = os.getenv("RAG_DATA_QUALITY_MODE", "observe").strip().lower()
    if mode not in {"observe", "strict"}:
        raise ValueError("RAG_DATA_QUALITY_MODE must be 'observe' or 'strict'")
    return mode


def _blank(value: Any) -> bool:
    return value is None or str(value).strip().lower() in {"", "unknown", "미분류", "none", "nan"}


def _ratio(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def build_notice_linkage_summary(
    session: Session,
    *,
    documents: list[SourceDocument] | None = None,
) -> dict[str, Any]:
    """Describe current notice index linkage from real relational rows.

    ``last_indexed_at`` is deliberately excluded: it is audit history and can
    remain populated after a chunk is removed (or be absent for a valid legacy
    chunk).  Both readiness reporting and the admin status endpoint use this
    helper so they cannot drift to different definitions of "indexed".
    """
    if documents is None:
        documents = (
            session.query(SourceDocument)
            .filter(SourceDocument.dataset == "notices")
            .order_by(SourceDocument.id.asc())
            .all()
        )
    linked_document_keys = {
        str(doc_id).strip()
        for (doc_id,) in (
            session.query(Chunk.doc_id)
            .join(Notice, Chunk.notice_id == Notice.id)
            .distinct()
            .all()
        )
        if str(doc_id or "").strip()
    }
    active_statuses = {"active", "updated"}
    linked_document_ids: list[int] = []
    indexable_linked_document_ids: list[int] = []
    mismatch_document_ids: list[int] = []
    active_count = 0
    updated_count = 0

    for document in documents:
        status = (document.status or "unknown").strip().lower()
        has_linked_chunk = bool(
            document.document_key
            and str(document.document_key) in linked_document_keys
        )
        if has_linked_chunk:
            linked_document_ids.append(document.id)
        if status == "active":
            active_count += 1
        elif status == "updated":
            updated_count += 1
        if status in active_statuses and has_linked_chunk:
            indexable_linked_document_ids.append(document.id)
        if (status in active_statuses) != has_linked_chunk:
            mismatch_document_ids.append(document.id)

    return {
        "total_documents": len(documents),
        "active_documents": active_count,
        "updated_documents": updated_count,
        "linked_document_ids": linked_document_ids,
        "linked_documents": len(linked_document_ids),
        "indexable_linked_document_ids": indexable_linked_document_ids,
        "indexable_linked_documents": len(indexable_linked_document_ids),
        "index_mismatch_document_ids": mismatch_document_ids,
        "index_mismatch": len(mismatch_document_ids),
    }


def build_source_document_quality_report(
    session: Session,
    *,
    dataset: str = "notices",
    thresholds: DataQualityThresholds | None = None,
    retry_limit: int | None = None,
) -> dict[str, Any]:
    """Aggregate source status without questions, answers, or user identifiers."""
    thresholds = thresholds or DataQualityThresholds.from_env()
    documents = (
        session.query(SourceDocument)
        .filter(SourceDocument.dataset == dataset)
        .order_by(SourceDocument.id.asc())
        .all()
    )
    if dataset != "notices":
        raise ValueError("source-document index linkage is currently supported for notices only")
    linkage = build_notice_linkage_summary(session, documents=documents)
    linked_document_ids = set(linkage["linked_document_ids"])
    total = len(documents)
    active_statuses = {"active", "updated"}
    parse_failed = []
    category_unknown = []
    inactive = []
    index_mismatch = []
    active_count = linkage["active_documents"]
    updated_count = linkage["updated_documents"]
    active_chunk_linked_count = 0
    indexable_chunk_linked_count = linkage["indexable_linked_documents"]
    retry_rows: list[dict[str, Any]] = []

    for document in documents:
        status = (document.status or "unknown").strip().lower()
        has_parse_error = status == "parse_failed" or not _blank(document.parse_error)
        missing_category = _blank(document.category)
        is_inactive = status not in active_statuses
        has_linked_chunk = document.id in linked_document_ids
        if status == "active":
            active_chunk_linked_count += int(has_linked_chunk)

        # Index state is derived from the real SourceDocument -> Notice -> Chunk
        # relationship. ``last_indexed_at`` is audit history, not current state.
        mismatched_index = (status in active_statuses) != has_linked_chunk

        reasons: list[str] = []
        if has_parse_error:
            parse_failed.append(document.id)
            reasons.append("parse_error")
        if missing_category:
            category_unknown.append(document.id)
            reasons.append("category_unknown")
        if is_inactive:
            inactive.append(document.id)
        if mismatched_index:
            index_mismatch.append(document.id)
            reasons.append("index_mismatch")

        if reasons:
            retry_rows.append(
                {
                    "document_key": document.document_key,
                    "source_id": document.source_id,
                    "source_url": document.source_url,
                    "status": status,
                    "reasons": reasons,
                }
            )

    counts = {
        "total": total,
        "active": active_count,
        "updated": updated_count,
        "active_chunk_linked": active_chunk_linked_count,
        "indexable_chunk_linked": indexable_chunk_linked_count,
        "parse_error": len(parse_failed),
        "category_unknown": len(category_unknown),
        "inactive": len(inactive),
        "index_mismatch": len(index_mismatch),
    }
    ratios = {
        "parse_error": _ratio(counts["parse_error"], total),
        "category_unknown": _ratio(counts["category_unknown"], total),
        "inactive": _ratio(counts["inactive"], total),
        "index_mismatch": _ratio(counts["index_mismatch"], total),
    }
    threshold_map = {
        "parse_error": thresholds.max_parse_error_ratio,
        "category_unknown": thresholds.max_category_unknown_ratio,
        "inactive": thresholds.max_inactive_ratio,
        "index_mismatch": thresholds.max_index_mismatch_ratio,
    }
    violations = [
        {
            "metric": metric,
            "actual": ratios[metric],
            "maximum": maximum,
        }
        for metric, maximum in threshold_map.items()
        if ratios[metric] > maximum
    ]
    retry_document_count = len(retry_rows)
    if retry_limit is not None:
        retry_rows = retry_rows[:max(0, retry_limit)]

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset,
        "contains_user_content": False,
        "counts": counts,
        "ratios": ratios,
        "thresholds": asdict(thresholds),
        "gate_passed": not violations,
        "violations": violations,
        "retry_documents": retry_rows,
        "retry_document_count": retry_document_count,
    }


__all__ = [
    "DataQualityThresholds",
    "build_notice_linkage_summary",
    "build_source_document_quality_report",
    "data_quality_mode",
]
