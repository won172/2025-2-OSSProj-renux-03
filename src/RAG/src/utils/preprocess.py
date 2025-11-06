"""Text cleaning and chunk preparation utilities taken from the notebook."""
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
    """Remove HTML tags and normalise line breaks."""
    if not isinstance(text, str):
        return ""
    text = _TAG_SCRIPT_STYLE.sub(" ", text)
    text = _TAG_BREAK.sub("\n", text)
    text = _TAG_PARAGRAPH.sub("\n", text)
    text = _TAG_GENERIC.sub(" ", text)
    return text


def normalize_whitespace(text: str | None) -> str:
    """Apply the whitespace heuristics defined in the notebook."""
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
    """Normalise date strings into YYYY-MM-DD format."""
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
    """Create a deterministic SHA1 identifier for a document."""
    raw = "|".join(str(p) for p in parts if p)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def make_chunk_id(doc_id: str, index: int) -> str:
    """Create a deterministic SHA1 identifier for a chunk."""
    raw = f"{doc_id}|{index}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def apply_cleaning(df: DataFrame, content_col: str, date_col: str | None = None) -> DataFrame:
    """Return a copy of *df* with `clean_text` and optional `clean_date` columns."""
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
    """Create the list of document dictionaries used for chunking."""
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
    """Rudimentary character-based chunking used for long documents."""
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
    """Transform document dicts into chunk dicts consumable by Chroma."""
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
