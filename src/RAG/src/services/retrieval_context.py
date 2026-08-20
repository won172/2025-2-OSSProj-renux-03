"""검색 전용 문맥 헤더와 파생 메타데이터를 만든다."""
from __future__ import annotations

import re
import unicodedata

import pandas as pd

from src.utils.audience import derive_audience


_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})\s*(?:학년도|년도|년)?")
_SEMESTER_RE = re.compile(r"([12])\s*학기")
_SHORT_PERIOD_RE = re.compile(r"(?<!\d)(\d{2})\s*[-./]\s*([12])(?!\d)")
_AUDIENCE_LABELS = {
    "undergraduate": "학부",
    "graduate": "대학원",
    "common": "공통",
}


def normalize_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"\s+", "", text)


def _first_value(row: pd.Series, *columns: str) -> str:
    for column in columns:
        value = row.get(column)
        if value is None or (not isinstance(value, str) and pd.isna(value)):
            continue
        text = str(value).strip()
        if text and text.lower() not in {"nan", "none", "nat"}:
            return text
    return ""


def _academic_period(row: pd.Series) -> str:
    material = " ".join(
        [
            _first_value(row, "title"),
            _first_value(row, "chunk_text")[:500],
        ]
    )
    year_match = _YEAR_RE.search(material)
    semester_match = _SEMESTER_RE.search(material)
    short_match = _SHORT_PERIOD_RE.search(material)
    year = int(year_match.group(1)) if year_match else None
    semester = int(semester_match.group(1)) if semester_match else None
    if short_match:
        year = year or 2000 + int(short_match.group(1))
        semester = semester or int(short_match.group(2))
    if year and semester:
        return f"{year}학년도 {semester}학기"
    if year:
        return f"{year}학년도"
    if semester:
        return f"{semester}학기"
    return ""


def build_retrieval_context(row: pd.Series) -> str:
    title = _first_value(row, "title", "filename") or "제목 미상"
    published = _first_value(
        row,
        "published_at",
        "effective_date",
        "schedule_start",
        "meal_date",
    )
    audience = _first_value(row, "audience") or derive_audience(
        " ".join(
            [
                title,
                _first_value(row, "category", "topics"),
                _first_value(row, "department"),
                _first_value(row, "chunk_text")[:300],
            ]
        )
    )
    period = _academic_period(row)
    parts = [f"문서: {title}"]
    if published:
        parts.append(f"기준일: {published}")
    if period:
        parts.append(f"학사시기: {period}")
    if audience != "common":
        parts.append(f"대상: {_AUDIENCE_LABELS[audience]}")
    return f"[{' · '.join(parts)}]"


def enrich_retrieval_fields(frame: pd.DataFrame) -> pd.DataFrame:
    """원문 청크는 보존하고 검색에만 쓰는 문맥 텍스트를 추가한다."""
    if frame.empty:
        enriched = frame.copy()
        for column in (
            "title_norm",
            "audience",
            "retrieval_context",
            "retrieval_text",
        ):
            enriched[column] = pd.Series(dtype=str)
        return enriched

    enriched = frame.copy()
    if "title" not in enriched.columns:
        enriched["title"] = ""
    enriched["title_norm"] = enriched["title"].map(normalize_title)

    derived_audience = enriched.apply(
        lambda row: derive_audience(
            " ".join(
                [
                    _first_value(row, "title", "filename"),
                    _first_value(row, "category", "topics"),
                    _first_value(row, "department"),
                    _first_value(row, "chunk_text")[:300],
                ]
            )
        ),
        axis=1,
    )
    if "audience" in enriched.columns:
        existing = enriched["audience"].astype(str).str.strip().str.lower()
        valid = existing.isin(_AUDIENCE_LABELS)
        enriched["audience"] = existing.where(valid, derived_audience)
    else:
        enriched["audience"] = derived_audience

    enriched["retrieval_context"] = enriched.apply(
        build_retrieval_context,
        axis=1,
    )
    body = enriched.get(
        "chunk_text",
        pd.Series("", index=enriched.index, dtype=str),
    ).fillna("").astype(str)
    enriched["retrieval_text"] = (
        enriched["retrieval_context"].astype(str) + "\n\n" + body
    ).str.strip()
    return enriched


__all__ = [
    "build_retrieval_context",
    "enrich_retrieval_fields",
    "normalize_title",
]
