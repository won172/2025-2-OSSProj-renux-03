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
_LEADING_RULE_CODE_RE = re.compile(r"^\s*(\d+-\d+-\d+)(?!\d)")
OFFICIAL_RULE_SOURCE_TYPE = "official_rule_web"


def canonical_rule_title(value: object) -> str:
    title = str(value or "").strip().lower()
    title = _EXTENSION_RE.sub("", title)
    title = _TRAILING_VERSION_RE.sub("", title)
    return _NON_WORD_RE.sub("", title)


def canonical_rule_key(value: object) -> str:
    normalized = canonical_rule_title(value)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def rule_code_of(value: object) -> str:
    """제목/파일명 앞의 규정번호(예: ``2-1-1``)를 돌려준다. 없으면 빈 문자열."""
    match = _LEADING_RULE_CODE_RE.match(str(value or ""))
    return match.group(1) if match else ""


def rule_code_key(code: str) -> str:
    return hashlib.sha1(f"rule-code:{code}".encode("utf-8")).hexdigest()


def code_identity_codes(
    codes: "pd.Series",
    title_keys: "pd.Series",
    official_mask: "pd.Series",
) -> set[str]:
    """규정번호를 동일성 기준으로 써도 안전한 번호 집합.

    공식 현행판이 쓰는 번호이고, 그 번호를 쓰는 규정(정규화 제목)이 공식 행 안에서도
    (현재·과거 공식 행 모두) 하나, 비공식(HWP 스냅샷) 행 안에서도 하나뿐인 경우다.
    서로 다른 규정이 번호를 공유하면(예: 서울·WISE 캠퍼스 규정이 같은 번호) 잘못 묶어
    한쪽을 현행에서 빼지 않도록 제목 기준을 유지한다.
    """

    def single_title_codes(mask: "pd.Series") -> tuple[set[str], set[str]]:
        frame = pd.DataFrame({"code": codes[mask], "key": title_keys[mask]})
        frame = frame[frame["code"].ne("")]
        distinct = frame.groupby("code")["key"].nunique()
        return set(distinct.index), set(distinct[distinct > 1].index)

    official_codes, official_colliding = single_title_codes(official_mask)
    _snapshot_codes, snapshot_colliding = single_title_codes(~official_mask)
    return official_codes - official_colliding - snapshot_colliding


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

    title_keys = annotated[title_col].map(canonical_rule_key)
    annotated["canonical_key"] = title_keys
    if "source_type" in annotated.columns:
        # 공식 현행판과 같은 규정번호를 쓰는 행은 제목이 달라도(상세 제목 표기 차이, 개칭)
        # 같은 규정으로 묶어 최신판 하나만 is_latest가 되게 한다. 번호 없는 행은 제목 기준.
        codes = annotated[title_col].map(rule_code_of)
        if "rule_code" in annotated.columns:
            explicit = annotated["rule_code"].fillna("").astype(str).str.strip()
            codes = explicit.where(explicit.ne(""), codes)
        official_mask = annotated["source_type"].fillna("").astype(str).eq(OFFICIAL_RULE_SOURCE_TYPE)
        eligible = code_identity_codes(codes, title_keys, official_mask)
        if eligible:
            use_code = codes.isin(eligible)
            annotated.loc[use_code, "canonical_key"] = codes[use_code].map(rule_code_key)
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


__all__ = [
    "annotate_rule_versions",
    "canonical_rule_key",
    "canonical_rule_title",
    "code_identity_codes",
    "rule_code_key",
    "rule_code_of",
]
