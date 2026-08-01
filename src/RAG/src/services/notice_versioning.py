"""연도별로 반복되는 공지의 동일 계열과 최신판을 표시한다."""
from __future__ import annotations

import hashlib
import re

import pandas as pd


_VERSION_PREFIX_RE = re.compile(r"^\s*[\[(]?\s*(?:수정|재공지|재안내|업데이트)\s*[\])]?\s*")
_ACADEMIC_YEAR_RE = re.compile(r"(?<!\d)20\d{2}\s*(?:학년도|년도|년)")
_DATE_RE = re.compile(
    r"(?<!\d)20\d{2}\s*[.\-/]\s*\d{1,2}(?:\s*[.\-/]\s*\d{1,2})?(?!\d)"
)
_SPACE_PUNCT_RE = re.compile(r"[^0-9a-zA-Z가-힣]+")


def canonical_notice_title(title: object) -> str:
    value = str(title or "").strip().lower()
    value = _VERSION_PREFIX_RE.sub("", value)
    value = _ACADEMIC_YEAR_RE.sub("", value)
    value = _DATE_RE.sub("", value)
    return _SPACE_PUNCT_RE.sub("", value)


def canonical_notice_key(title: object, board: object = "") -> str:
    normalized_title = canonical_notice_title(title)
    normalized_board = _SPACE_PUNCT_RE.sub("", str(board or "").strip().lower())
    material = f"{normalized_board}|{normalized_title}"
    return hashlib.sha1(material.encode("utf-8")).hexdigest()


def annotate_notice_versions(frame: pd.DataFrame) -> pd.DataFrame:
    """공지 프레임에 ``canonical_key``와 ``is_latest``를 추가한다.

    청크가 여러 개인 문서는 모두 같은 최신판 값을 갖는다. 원문은 삭제하지 않으며,
    명시적 과거 연도 질문에서 후단이 이 신호를 무시할 수 있도록 메타데이터만 붙인다.
    """
    if frame.empty:
        annotated = frame.copy()
        annotated["canonical_key"] = pd.Series(dtype=str)
        annotated["is_latest"] = pd.Series(dtype=bool)
        return annotated

    annotated = frame.copy()
    title_col = "title" if "title" in annotated.columns else "제목"
    board_col = next(
        (name for name in ("topics", "게시판", "board", "category") if name in annotated.columns),
        None,
    )
    annotated["canonical_key"] = [
        canonical_notice_key(
            row.get(title_col, ""),
            row.get(board_col, "") if board_col else "",
        )
        for _, row in annotated.iterrows()
    ]
    published_col = "published_at" if "published_at" in annotated.columns else "게시일"
    published = (
        pd.to_datetime(annotated[published_col], errors="coerce")
        if published_col in annotated.columns
        else pd.Series(pd.NaT, index=annotated.index)
    )
    annotated["_version_date"] = published
    annotated["_version_order"] = range(len(annotated))

    document_col = next(
        (name for name in ("doc_id", "document_key", "notice_id", "chunk_id") if name in annotated.columns),
        None,
    )
    if document_col is None:
        annotated["_version_document"] = annotated.index.astype(str)
        document_col = "_version_document"

    documents = (
        annotated[
            ["canonical_key", document_col, "_version_date", "_version_order"]
        ]
        .drop_duplicates(subset=["canonical_key", document_col], keep="last")
        .sort_values(
            ["canonical_key", "_version_date", "_version_order"],
            ascending=[True, True, True],
            na_position="first",
            kind="stable",
        )
    )
    latest_documents = set(
        zip(
            documents.groupby("canonical_key", sort=False).tail(1)["canonical_key"],
            documents.groupby("canonical_key", sort=False).tail(1)[document_col].astype(str),
        )
    )
    annotated["is_latest"] = [
        (key, str(document)) in latest_documents
        for key, document in zip(annotated["canonical_key"], annotated[document_col])
    ]
    return annotated.drop(
        columns=["_version_date", "_version_order", "_version_document"],
        errors="ignore",
    )


__all__ = [
    "annotate_notice_versions",
    "canonical_notice_key",
    "canonical_notice_title",
]
