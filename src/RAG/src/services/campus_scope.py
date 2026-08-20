"""Campus classification and the retrieval safety boundary.

The v1 product serves Seoul and BMC students only. WISE is classified so that
its documents and explicit question markers can be quarantined, never so that
an ordinary product request can opt into WISE. ``unknown`` remains eligible for
untrusted legacy rows. Records from the six curated main-campus corpora receive
a provenance fallback only after every WISE, BMC, and Seoul identity signal has
been checked.
"""
from __future__ import annotations

from enum import Enum
import re
from typing import Any, Iterable, Mapping

import pandas as pd


class CampusScope(str, Enum):
    SEOUL = "seoul"
    BMC = "bmc"
    WISE = "wise"
    SHARED = "shared"
    UNKNOWN = "unknown"


_CANONICAL_SCOPES = {scope.value for scope in CampusScope}
_WISE_RE = re.compile(
    r"(?i)(?:\bwise\s*(?:campus|캠퍼스)?\b|wise캠퍼스|동국대학교\s*wise|"
    r"와이즈\s*캠퍼스|경주\s*캠퍼스|경주캠퍼스|"
    r"(?:^|[./_-])wise(?:[./_-]|$)|wise\.dongguk\.ac\.kr)"
)
_BMC_RE = re.compile(r"(?i)(?:\bbmc\b|바이오\s*메디\s*캠퍼스|바이오메디캠퍼스)")
_SEOUL_RE = re.compile(r"(?i)(?:서울\s*캠퍼스|서울캠퍼스|필동캠퍼스)")
_TRUSTED_SHARED_RE = re.compile(r"(?:캠퍼스\s*공통\s*규정|전\s*캠퍼스\s*공통|공통\s*규정)")
_IDENTITY_KEYS = (
    "filename", "source_file", "relative_dir", "path", "raw_path",
    "normalized_path", "url", "source_url", "document_key", "title",
)
_CONTENT_KEYS = (
    "topics", "category", "department", "college_name", "source", "chunk_text", "text",
)
_TRUSTED_MAIN_CAMPUS_DEFAULTS = {
    "notices": CampusScope.SHARED,
    "rules": CampusScope.SHARED,
    "schedule": CampusScope.SHARED,
    "courses": CampusScope.SHARED,
    "staff": CampusScope.SHARED,
    "meals": CampusScope.SEOUL,
}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def query_explicitly_requests_wise(question: str) -> bool:
    """Return true for a WISE marker that must be rejected or quarantined."""
    return bool(_WISE_RE.search(_clean(question)))


def classify_campus_scope(metadata: Mapping[str, Any] | None = None, **values: Any) -> CampusScope:
    """Conservatively classify one document from identity-bearing metadata.

    Strong filename/path/URL/title identity wins over metadata and body prose.
    Trusted canonical metadata or multiple explicit campus identities may be
    ``shared``. Absence of a signal is ``unknown`` rather than guessed as Seoul.
    """
    fields: dict[str, Any] = dict(metadata) if metadata is not None else {}
    fields.update(values)
    explicit = _clean(fields.get("campus_scope")).lower()

    # Document identity always outranks prose.  A WISE-only filename/path/title
    # cannot become shared merely because one body clause says "양 캠퍼스".
    identity = "\n".join(
        _clean(fields.get(key)) for key in _IDENTITY_KEYS if _clean(fields.get(key))
    )
    identity_wise = bool(_WISE_RE.search(identity))
    identity_bmc = bool(_BMC_RE.search(identity))
    identity_seoul = bool(_SEOUL_RE.search(identity))
    if sum((identity_wise, identity_bmc, identity_seoul)) > 1:
        return CampusScope.SHARED
    if identity_wise:
        return CampusScope.WISE
    if identity_bmc:
        return CampusScope.BMC
    if identity_seoul:
        return CampusScope.SEOUL

    # Canonical metadata is trusted only after exclusive identity evidence has
    # been ruled out. ``shared`` therefore remains available for curated common
    # documents without allowing weak body wording to override a WISE title.
    if explicit in _CANONICAL_SCOPES:
        return CampusScope(explicit)

    content = "\n".join(
        _clean(fields.get(key)) for key in _CONTENT_KEYS if _clean(fields.get(key))
    )
    wise = bool(_WISE_RE.search(content))
    bmc = bool(_BMC_RE.search(content))
    seoul = bool(_SEOUL_RE.search(content))
    if _TRUSTED_SHARED_RE.search(identity) or sum((wise, bmc, seoul)) > 1:
        # Multiple explicit campus names are stronger than generic "양 캠퍼스"
        # prose and constitute a verifiable common-document condition.
        return CampusScope.SHARED
    if wise:
        return CampusScope.WISE
    if bmc:
        return CampusScope.BMC
    if seoul:
        return CampusScope.SEOUL
    return CampusScope.UNKNOWN


def enrich_documents_with_campus_scope(documents: Iterable[dict[str, Any]]) -> None:
    """Add canonical campus metadata in place before chunking/indexing."""
    for document in documents:
        document["campus_scope"] = classify_campus_scope(document).value


def campus_scope_for_row(row: Mapping[str, Any]) -> CampusScope:
    """Classify both new and metadata-less legacy retrieval rows."""
    classified = classify_campus_scope(row)
    if classified is not CampusScope.UNKNOWN:
        return classified
    source = _clean(row.get("source") or row.get("dataset")).lower()
    return _TRUSTED_MAIN_CAMPUS_DEFAULTS.get(source, CampusScope.UNKNOWN)


def apply_campus_safety_boundary(
    hits: pd.DataFrame,
    *,
    allow_wise: bool,
) -> tuple[pd.DataFrame, int]:
    """The single post-retrieval boundary shared by every search path.

    ``shared`` is allowed because it explicitly applies to more than one
    campus. ``unknown`` is allowed for backwards compatibility, but only after
    reclassification from every legacy WISE-bearing field.  This avoids hiding
    the majority of old Seoul records while still blocking detectable WISE
    material from candidates, sources, and answer context. ``allow_wise`` is a
    compatibility parameter for low-level callers; the public v1 API never
    sets it to true.
    """
    if hits.empty:
        return hits.copy(), 0

    classified = hits.copy()
    classified["campus_scope"] = [campus_scope_for_row(row).value for _, row in classified.iterrows()]
    classified["campus_allow_wise"] = bool(allow_wise)
    if allow_wise:
        return classified.reset_index(drop=True), 0

    safe = classified[classified["campus_scope"] != CampusScope.WISE.value].copy()
    blocked = len(classified) - len(safe)
    return safe.reset_index(drop=True), blocked


__all__ = [
    "CampusScope",
    "apply_campus_safety_boundary",
    "campus_scope_for_row",
    "classify_campus_scope",
    "enrich_documents_with_campus_scope",
    "query_explicitly_requests_wise",
]
