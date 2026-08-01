"""규정 원문의 개정판 계열과 현행판을 결정적으로 표시한다."""
from __future__ import annotations

import hashlib
import re

import pandas as pd


_TRAILING_VERSION_RE = re.compile(
    r"\s*\(\s*(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})\s*\.?\s*\)\s*$"
)
_EXTENSION_RE = re.compile(r"\.(?:hwp|hwpx|html?|pdf)$", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[^0-9a-zA-Z가-힣]+")


def canonical_rule_title(value: object) -> str:
    title = str(value or "").strip().lower()
    title = _EXTENSION_RE.sub("", title)
    title = _TRAILING_VERSION_RE.sub("", title)
    return _NON_WORD_RE.sub("", title)


def canonical_rule_key(value: object) -> str:
    normalized = canonical_rule_title(value)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def _filename_version(value: object) -> pd.Timestamp:
    title = _EXTENSION_RE.sub("", str(value or "").strip())
    match = _TRAILING_VERSION_RE.search(title)
    if match is None:
        return pd.NaT
    return pd.to_datetime("-".join(match.groups()), errors="coerce")


def annotate_rule_versions(frame: pd.DataFrame) -> pd.DataFrame:
    """같은 규정의 모든 청크에 ``canonical_key``와 ``is_latest``를 붙인다.

    과거판은 삭제하지 않는다. 검색 단계가 연도 미지정 질문에서만 ``False``를
    제외하므로, 명시적인 과거 연도 질의는 계속 과거 규정을 찾을 수 있다.
    """
    if frame.empty:
        annotated = frame.copy()
        annotated["canonical_key"] = pd.Series(dtype=str)
        annotated["is_latest"] = pd.Series(dtype=bool)
        return annotated

    annotated = frame.copy()
    title_col = next(
        (name for name in ("title", "filename", "규정명") if name in annotated.columns),
        None,
    )
    if title_col is None:
        annotated["canonical_key"] = ""
        annotated["is_latest"] = True
        return annotated

    annotated["canonical_key"] = annotated[title_col].map(canonical_rule_key)
    published = (
        pd.to_datetime(annotated["published_at"], errors="coerce")
        if "published_at" in annotated.columns
        else pd.Series(pd.NaT, index=annotated.index)
    )
    filenames = annotated.get(
        "filename",
        annotated.get(title_col, pd.Series("", index=annotated.index)),
    )
    annotated["_version_date"] = published.fillna(filenames.map(_filename_version))
    annotated["_version_order"] = range(len(annotated))

    document_col = next(
        (name for name in ("doc_id", "source_version", "rule_id", "chunk_id") if name in annotated.columns),
        None,
    )
    if document_col is None:
        annotated["_version_document"] = annotated.index.astype(str)
        document_col = "_version_document"

    documents = (
        annotated[["canonical_key", document_col, "_version_date", "_version_order"]]
        .drop_duplicates(subset=["canonical_key", document_col], keep="last")
        .sort_values(
            ["canonical_key", "_version_date", "_version_order"],
            ascending=[True, True, True],
            na_position="first",
            kind="stable",
        )
    )
    latest = documents.groupby("canonical_key", sort=False).tail(1)
    latest_documents = set(zip(latest["canonical_key"], latest[document_col].astype(str)))
    annotated["is_latest"] = [
        (key, str(document)) in latest_documents
        for key, document in zip(annotated["canonical_key"], annotated[document_col])
    ]
    return annotated.drop(
        columns=["_version_date", "_version_order", "_version_document"],
        errors="ignore",
    )


__all__ = ["annotate_rule_versions", "canonical_rule_key", "canonical_rule_title"]
