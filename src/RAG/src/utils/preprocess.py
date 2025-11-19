"""노트북에서 가져온 텍스트 정제 및 청크 준비 유틸리티입니다."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Iterable, List, Optional

from pandas import DataFrame


_TAG_SCRIPT_STYLE = re.compile(r"(?is)<(script|style).*?>.*?</\\1>")
_TAG_BREAK = re.compile(r"(?is)<br\\s*/?>")
_TAG_PARAGRAPH = re.compile(r"(?is)</p>")
_TAG_GENERIC = re.compile(r"(?is)<.*?>")
_WHITESPACE = re.compile(r"[ \t\u00A0]+")


def strip_html(text: str | None) -> str:
    """HTML 태그를 제거하고 줄바꿈을 정리합니다."""
    if not isinstance(text, str):
        return ""
    text = _TAG_SCRIPT_STYLE.sub(" ", text)
    text = _TAG_BREAK.sub("\n", text)
    text = _TAG_PARAGRAPH.sub("\n", text)
    text = _TAG_GENERIC.sub(" ", text)
    return text


def normalize_whitespace(text: str | None) -> str:
    """노트북에서 정의한 공백 정규화 규칙을 적용합니다."""
    if not isinstance(text, str):
        return ""
    text = _WHITESPACE.sub(" ", text)
    text = re.sub(r"(\d)\n([가-힣])", r"\1\2", text)
    text = re.sub(r"([가-힣])\n(\d)", r"\1 \2", text)
    text = re.sub(r"\n([()])", r"\1", text)
    text = re.sub(r"([()])\n", r"\1", text)
    text = re.sub(r"\n([.,!?·])", r"\1", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"\s*([()])\s*", r"\1", text)
    text = re.sub(r"\s*([.,!?·:/])\s*", r"\1 ", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+'|'\s+", "'", text)
    text = re.sub(r"([.!?])\s+(?=[가-힣A-Z0-9])", r"\1\n", text)
    return text.strip()


def standardize_date(value: str | None) -> Optional[str]:
    """날짜 문자열을 YYYY-MM-DD 형식으로 맞춥니다."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    for pattern in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y년 %m월 %d일"):
        try:
            return datetime.strptime(value, pattern).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def make_doc_id(*parts: object) -> str:
    """문서마다 고정된 SHA1 식별자를 생성합니다."""
    raw = "|".join(str(p) for p in parts if p)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def make_chunk_id(doc_id: str, index: int) -> str:
    """청크마다 고정된 SHA1 식별자를 생성합니다."""
    raw = f"{doc_id}|{index}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def apply_cleaning(df: DataFrame, content_col: str, date_col: str | None = None) -> DataFrame:
    """사본에 `clean_text`와 필요하면 `clean_date` 열을 추가해 반환합니다."""
    out = df.copy()
    out["clean_text"] = out[content_col].apply(strip_html).apply(normalize_whitespace)
    if date_col and date_col in out.columns:
        out["clean_date"] = out[date_col].apply(standardize_date)
    return out


def build_document_rows(
    df: DataFrame,
    content_col: str,
    title_col: str,
    topic_col: str,
    date_col: str | None,
    url_col: str | None,
    attachment_col: str | None,
) -> List[dict]:
    """청크 작업에 사용할 문서 딕셔너리 목록을 생성합니다."""
    docs: List[dict] = []
    for _, row in df.iterrows():
        published = row.get("clean_date") if "clean_date" in row else row.get(date_col)
        doc = {
            "doc_id": make_doc_id(row.get(title_col), row.get(topic_col), published),
            "title": row.get(title_col, ""),
            "published_at": published,
            "topics": row.get(topic_col, ""),
            "url": row.get(url_col, ""),
            "attachments": row.get(attachment_col, ""),
            "text": row.get("clean_text", ""),
            "org": "Dongguk Univ",
            "lang": "ko",
            "privacy_level": "public",
        }
        docs.append(doc)
    return docs


def chunk_text(text: str, size: int, overlap: int) -> List[str]:
    """긴 문서를 단순 문자 단위로 분할합니다."""
    if not text:
        return []
    text = normalize_whitespace(text)
    if not text:
        return []
    step = max(1, size - overlap)
    segments: List[str] = []
    for start in range(0, len(text), step):
        segment = text[start : start + size]
        segments.append(segment)
        if start + size >= len(text):
            break
    return segments


def to_chunks(
    docs: Iterable[dict],
    *,
    chunk_size: int | None = None,
    chunk_overlap: int = 0,
    include_title: bool = True,
) -> List[dict]:
    """문서 딕셔너리를 Chroma가 사용할 수 있는 청크 딕셔너리로 바꿉니다."""
    chunks: List[dict] = []
    for doc in docs:
        text = doc.get("text") or ""
        segments = [text]
        if chunk_size:
            segments = chunk_text(text, chunk_size, chunk_overlap) or [text]

        for idx, segment in enumerate(segments):
            if include_title and doc.get("title"):
                chunk_body = f"[{doc['title']}]\n\n{segment}".strip()
            else:
                chunk_body = segment.strip()
            chunk = {
                "chunk_id": make_chunk_id(doc["doc_id"], idx),
                "doc_id": doc["doc_id"],
                "chunk_text": chunk_body,
                "position": idx,
                "token_len": len(chunk_body.split()),
            }
            chunk.update({k: v for k, v in doc.items() if k not in {"text"}})
            chunks.append(chunk)
    return chunks

__all__ = [
    "strip_html",
    "normalize_whitespace",
    "standardize_date",
    "make_doc_id",
    "make_chunk_id",
    "apply_cleaning",
    "build_document_rows",
    "chunk_text",
    "to_chunks",
]
