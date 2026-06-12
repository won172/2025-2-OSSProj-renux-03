"""노트북에서 가져온 텍스트 정제 및 청크 준비 유틸리티입니다."""
from __future__ import annotations

import hashlib
import html as html_module
import re
import unicodedata
from datetime import datetime, date
from typing import Any, Iterable, List, Optional

from pandas import DataFrame


# 주의: 이전 버전은 r"</\\1>"처럼 raw string 안에 이중 백슬래시를 써서
# 백레퍼런스가 동작하지 않았고, script/style 본문(JS/CSS 코드)이
# 임베딩 텍스트에 그대로 섞여 들어갔다. 아래는 수정된 패턴(정규식 폴백용).
_TAG_SCRIPT_STYLE = re.compile(r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>")
_TAG_BREAK = re.compile(r"(?is)<br\s*/?>")
_TAG_PARAGRAPH = re.compile(r"(?is)</(p|div|li|tr|h[1-6])>")
_TAG_GENERIC = re.compile(r"(?is)<[^>]*>")
_WHITESPACE = re.compile(r"[ \t ]+")
# zero-width space/joiner, BOM, soft hyphen 등 보이지 않는 문자
_INVISIBLE_CHARS = re.compile(r"[​‌‍⁠﻿­]")


def strip_html(text: str | None) -> str:
    """HTML에서 본문 텍스트를 추출합니다.

    BeautifulSoup이 있으면 그것을 사용해 script/style/주석을 안전하게 제거하고
    블록 요소 경계를 줄바꿈으로 보존한다(중첩/비정형 마크업에 견고).
    없으면 정규식 폴백을 사용한다. 어느 경로든 HTML 엔티티를 해제한다.
    """
    if not isinstance(text, str):
        return ""
    if not text.strip():
        return ""

    # 빠른 경로: 태그/엔티티가 없으면 그대로 반환
    if "<" not in text and "&" not in text:
        return text

    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(text, "html.parser")
        for node in soup(["script", "style", "noscript", "iframe", "head"]):
            node.decompose()
        # 블록 요소 경계를 줄바꿈으로 보존 (표/목록 구조 평탄화 완화)
        return soup.get_text("\n")
    except Exception:
        cleaned = _TAG_SCRIPT_STYLE.sub(" ", text)
        cleaned = _TAG_BREAK.sub("\n", cleaned)
        cleaned = _TAG_PARAGRAPH.sub("\n", cleaned)
        cleaned = _TAG_GENERIC.sub(" ", cleaned)
        return html_module.unescape(cleaned)


def normalize_unicode(text: str | None) -> str:
    """임베딩/TF-IDF 일관성을 위한 유니코드 정규화.

    - NFKC: 전각 문자(（）１２ｱ 등)·합성 문자를 표준형으로 통일
    - zero-width/BOM/soft hyphen 등 보이지 않는 문자 제거
    """
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFKC", text)
    return _INVISIBLE_CHARS.sub("", text)


def normalize_whitespace(text: str | None) -> str:
    """노트북에서 정의한 공백 정규화 규칙을 적용합니다."""
    if not isinstance(text, str):
        return ""
    text = normalize_unicode(text)
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


# "2026.06.05." / "2026. 6. 5" / "2026-6-5" / "2026년 6월 5일" 등 유연 매칭
_DATE_PATTERN = re.compile(
    r"(?P<y>\d{4})\s*[.\-/년]\s*(?P<m>\d{1,2})\s*[.\-/월]\s*(?P<d>\d{1,2})\s*[.일]?"
)


def standardize_date(value: Any | None) -> Optional[str]:
    """날짜 값을 YYYY-MM-DD 형식으로 맞춥니다.

    공지 게시일("2026.06.09.")처럼 구분자 뒤 마침표가 붙거나, 한 자리 월/일,
    구분자 주변 공백이 있는 형식도 허용한다. 실패 시 None.
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):  # datetime.date 객체인 경우 처리
        return value.strftime("%Y-%m-%d")

    if not isinstance(value, str):
        return None

    value = value.strip()
    if not value:
        return None

    match = _DATE_PATTERN.search(value)
    if not match:
        return None
    try:
        parsed = date(int(match.group("y")), int(match.group("m")), int(match.group("d")))
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%d")


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
    """문장 경계를 우선 고려해 분할하고, LangChain 분할기를 우선 시도합니다."""
    if not text:
        return []

    normalized = normalize_whitespace(text)
    if not normalized:
        return []

    try:
        # LangChain의 RecursiveCharacterTextSplitter를 사용해 문장 단위로 최대 길이를 지키며 분할
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(
            separators=["\n\n", "\n", ". ", ".\n", "! ", "? ", " "],
            chunk_size=size,
            chunk_overlap=overlap,
            length_function=len,
            add_start_index=False,
        )
        docs = splitter.split_text(normalized)
        return [seg.strip() for seg in docs if seg.strip()]
    except ImportError:
        import logging
        logging.warning("langchain_text_splitters is not installed. Falling back to simple text splitting.")
        # 의존성 누락 등 예외 시 기존 단순 슬라이싱으로 폴백
        step = max(1, size - overlap)
        segments: List[str] = []
        for start in range(0, len(normalized), step):
            segment = normalized[start : start + size]
            segments.append(segment)
            if start + size >= len(normalized):
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
            segment = segment.strip()
            if not segment:
                # 빈 세그먼트는 임베딩 가치가 없으므로 제외
                continue
            if include_title and doc.get("title"):
                chunk_body = f"[{doc['title']}]\n\n{segment}".strip()
            else:
                chunk_body = segment
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
    "normalize_unicode",
    "normalize_whitespace",
    "standardize_date",
    "make_doc_id",
    "make_chunk_id",
    "apply_cleaning",
    "build_document_rows",
    "chunk_text",
    "to_chunks",
]
