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

# 줄바꿈이 아닌 공백. 정규화 규칙이 ``\s``를 쓰면 줄·문단 경계까지 함께 먹어서
# 분할기가 쓸 구조가 남지 않는다. 아래 규칙은 전부 이 클래스를 쓴다.
_H = r"[^\S\n]"

# 블록 요소 경계는 문단 경계로 올린다. ``get_text("\n")``은 ``<b>``/``<span>``
# 같은 인라인 노드 사이에도 줄바꿈을 넣기 때문에, 단일 ``\n``만으로는 진짜
# 블록 경계와 인라인 분절을 구분할 수 없다.
_BLOCK_TAGS = (
    "p", "div", "li", "tr", "section", "article", "blockquote",
    "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
)

# URL과 이메일은 구두점이 값의 일부다. 구두점 정규화 앞에서 통째로 빼두었다가
# 마지막에 되돌린다. 이 보호가 없던 동안 공지 청크의 36.8%, 과목 청크의 95.6%에
# "https: / / www. dongguk. edu/ apply? id=3" 형태로 깨진 링크가 들어갔다.
_URL_OR_EMAIL = re.compile(
    r"(?:https?|ftp)://[^\s<>\"\'()\[\]]+"
    r"|www\.[^\s<>\"\'()\[\]]+"
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
    # 스킴 없는 호스트명("cloud.dongguk.edu", "dgu.kr")도 하나의 토큰이다.
    # 보호하지 않으면 "cloud. dongguk. edu"가 되어 정확 일치 검색이 죽는다.
    r"|(?<![\w@.])[A-Za-z][A-Za-z0-9-]+(?:\.[A-Za-z0-9-]{2,})*"
    r"\.(?:edu|kr|com|net|org|io|co|jp)(?![\w.])"
)
# 공문서는 "1. 개요 / 가. 명칭" 처럼 숫자와 한글 순서표를 섞어 쓴다.
_ORDINAL_LETTERS = "가나다라마바사아자차카타파하"
# 줄머리 항목 번호("1. 신청 대상", "가. 명칭")의 마침표는 문장 끝이 아니다. 문장
# 분리 규칙이 여기서 줄을 바꾸면 번호만 줄 끝에 남아 청크 경계에서 내용과 갈라진다.
# 한글 순서표는 줄머리에 고정해서 찾는다. 그러지 않으면 "…감사합니다."의 "다."가
# 항목 번호로 잡힌다.
_LINE_LEADING_ORDINAL = re.compile(
    rf"(?m)^[^\S\n]*(?:\d{{1,2}}|[{_ORDINAL_LETTERS}])\.(?=[^\S\n]|$)"
)
# 줄에 항목 번호만 남은 경우 다음 줄의 내용과 붙인다.
_ORDINAL_ONLY_LINE = re.compile(
    rf"(?m)^([^\S\n]*[{_ORDINAL_LETTERS}]\.)[^\S\n]*\n[^\S\n]*(?=\S)"
)

# 사설 영역(PUA) 문자라 본문에 나타나지 않고, 구두점·공백 규칙 어디에도 걸리지 않는다.
_SLOT = "\ue000{}\ue001"
_SLOT_RE = re.compile("\ue000(\\d+)\ue001")

# 앞줄이 이 문자로 끝나면 줄바꿈은 하드랩이 아니라 진짜 경계다.
# 콜론은 뺀다 — "모집기간:\n2026. 05. 10."처럼 라벨과 값을 갈라놓는다. 콜론 뒤에
# 목록이 오는 경우는 _LINE_STARTER 쪽에서 이미 경계로 잡는다.
_LINE_ENDER = r"[.!?)\]】》」”\"\'다요음임함됨]"
# 뒷줄이 이렇게 시작하면 앞줄과 이어 붙이면 안 되는 새 항목이다.
# 숫자 번호 뒤에 숫자가 이어지면 날짜("5.4. (월)")이고, 한글은 순서표 글자만 인정한다
# — 아무 글자나 받으면 "석."(석사 약어) 같은 줄이 새 항목으로 잘린다.
_LINE_STARTER = rf"(?:[-*·•○▶▪\[【<]|\d{{1,2}}[.)](?!\d)|[{_ORDINAL_LETTERS}][.)])"


def _stash_protected(text: str, pattern: re.Pattern[str], kept: List[str]) -> str:
    """``pattern``에 걸리는 구간을 자리표시자로 치환하고 원문을 ``kept``에 모읍니다."""

    def _replace(match: re.Match[str]) -> str:
        kept.append(match.group(0))
        return _SLOT.format(len(kept) - 1)

    return pattern.sub(_replace, text)


def _restore_protected(text: str, kept: List[str]) -> str:
    if not kept:
        return text
    return _SLOT_RE.sub(lambda match: kept[int(match.group(1))], text)


_WHITESPACE = re.compile(r"[ \t ]+")
# zero-width space/joiner, BOM, soft hyphen 등 보이지 않는 문자
_INVISIBLE_CHARS = re.compile(r"[​‌‍⁠﻿­]")


def strip_html(text: str | None) -> str:
    """HTML에서 본문 텍스트를 추출합니다.

    BeautifulSoup이 있으면 그것을 사용해 script/style/주석을 안전하게 제거하고
    블록 요소 경계를 빈 줄로 보존한다(중첩/비정형 마크업에 견고).
    없으면 정규식 폴백을 사용한다. 어느 경로든 HTML 엔티티를 해제한다.
    """
    if not isinstance(text, str):
        return ""
    if not text.strip():
        return ""

    # 빠른 경로: HTML 태그가 없으면 URL 쿼리 문자열의 ``&`` 같은 값도
    # BeautifulSoup으로 넘길 이유가 없다. 엔티티만 안전하게 해제해 URL을
    # 문서로 오인하는 경고와 대량 청킹 시 불필요한 파싱 비용을 막는다.
    if "<" not in text:
        return html_module.unescape(text)

    try:
        from bs4 import BeautifulSoup, NavigableString

        soup = BeautifulSoup(text, "html.parser")
        for node in soup(["script", "style", "noscript", "iframe", "head"]):
            node.decompose()
        # 블록 경계에만 빈 줄을 남긴다. 인라인 노드 사이의 줄바꿈과 구분되어야
        # normalize_whitespace가 어느 줄바꿈을 지워도 되는지 판단할 수 있다.
        for node in soup.find_all("br"):
            node.replace_with(NavigableString("\n\n"))
        for node in soup.find_all(_BLOCK_TAGS):
            node.append(NavigableString("\n\n"))
        return soup.get_text("\n")
    except Exception:
        cleaned = _TAG_SCRIPT_STYLE.sub(" ", text)
        cleaned = _TAG_BREAK.sub("\n\n", cleaned)
        cleaned = _TAG_PARAGRAPH.sub("\n\n", cleaned)
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
    """공백을 정규화하되 분할에 쓸 줄·문단 경계는 남깁니다.

    이 함수의 출력이 곧 청크 분할기의 입력이다. 예전 구현은 모든 줄바꿈을
    공백으로 접고 ``\n{2,}``를 단일 ``\n``으로 눌렀기 때문에, 분할기의 첫
    구분자 ``"\n\n"``이 어떤 입력에서도 매치되지 않았다. 실질 분할 단위는
    문단이 아니라 이 함수가 뒤늦게 재삽입한 "문장"이었다.
    """
    if not isinstance(text, str):
        return ""
    text = normalize_unicode(text)

    protected: List[str] = []
    text = _stash_protected(text, _URL_OR_EMAIL, protected)

    text = _WHITESPACE.sub(" ", text)
    text = re.sub(r"(\d)\n([가-힣])", r"\1\2", text)
    text = re.sub(r"([가-힣])\n(\d)", r"\1 \2", text)
    text = re.sub(r"\n([()])", r"\1", text)
    text = re.sub(r"([()])\n", r"\1", text)
    text = re.sub(r"\n([.,!?·])", r"\1", text)

    # 항목 번호만 줄 끝에 남은 경우("1.\n대상: 재학생")는 종결이 아니라 하드랩이다.
    # 아래 조건부 병합이 마침표를 문장 끝으로 보기 전에 번호와 내용을 먼저 붙인다.
    text = re.sub(rf"(?<![\d.])(\d{{1,2}}\.){_H}*\n{_H}*(?=\S)", r"\1 ", text)
    text = _ORDINAL_ONLY_LINE.sub(r"\1 ", text)

    # 줄바꿈을 조건부로만 접는다. 공지 본문의 줄바꿈 202,784개 중 67.9%는
    # 하드랩이라 이어 붙이는 게 맞지만, 32.1%(65,026개)는 문장·항목이 실제로
    # 끝나는 지점이다. 예전처럼 전부 공백으로 바꾸면 이 코퍼스에 남아 있는
    # 유일한 구조 신호가 사라진다.
    text = re.sub(
        rf"(?<!\n)(?<!{_LINE_ENDER})\n(?!\n)(?!{_LINE_STARTER})",
        " ",
        text,
    )
    # 빈 줄은 하나로 정리하되 경계 자체는 남긴다(예전에는 단일 \n으로 눌렀다).
    text = re.sub(r"\n{2,}", "\n\n", text)

    text = re.sub(rf"{_H}*([()]){_H}*", r"\1", text)
    # 구두점 뒤 공백 정규화. 단, **숫자와 숫자 사이의 구두점은 값의 일부**라
    # 건드리지 않는다 — 날짜(2026.09.01), 평점(3.5), 학점 기준(2.0 미만),
    # 금액(1,250,000원), 비율(1/2)이 여기 걸린다.
    # 이 예외가 없던 동안 코퍼스의 40.8%(13,250/32,508 청크)에서 숫자가 쪼개져
    # 있었다: "평점 3.5 이상" → "평점 3. 5 이상", "1,250,000원" → "1, 250, 000원".
    # 장학·졸업·학사경고 답변의 핵심 수치가 그대로 임베딩과 근거 텍스트에 들어갔다.
    text = re.sub(rf"(?<!\d){_H}*([.,!?·:/]){_H}*", r"\1 ", text)
    text = re.sub(rf"(?<=\d){_H}*([.,!?·:/]){_H}*(?!\d)", r"\1 ", text)
    text = re.sub(rf"{_H}{{2,}}", " ", text)
    text = re.sub(rf"{_H}+'|'{_H}+", "'", text)

    # 순서가 중요하다. 항목 번호를 모두 줄머리로 올려 보호한 **다음에** 문장을
    # 나눠야 한다. 거꾸로 하면 "…말한다. 가. 이사장"이나 "가. 2026-1학기"에서
    # 번호만 줄 끝에 남기고 내용이 다음 줄로 갈라진다.
    ordinal_ahead = rf"(?=(?:\d{{1,2}}|[{_ORDINAL_LETTERS}])\.{_H})"
    # 문장 끝 뒤에 오는 항목 번호("…한다. 1. 학생"). 숫자 뒤 마침표(2026. 9. 1.)는 제외.
    text = re.sub(rf"(?<!\d)([.!?]){_H}+{ordinal_ahead}", r"\1\n", text)
    # 한 줄에 이어 붙은 항목 번호("… 대상 2. 신청 기간").
    text = re.sub(rf"(?<=[가-힣)]){_H}+{ordinal_ahead}", "\n", text)
    # 줄머리 항목 번호의 마침표는 문장 끝이 아니므로 분리 대상에서 뺀다.
    text = _stash_protected(text, _LINE_LEADING_ORDINAL, protected)

    # 문장 끝에서 줄을 바꾼다. 숫자 사이(2026. 9. 1.)는 문장 경계가 아니므로 제외한다.
    text = re.sub(rf"(?<!\d)([.!?]){_H}+(?=\d)", r"\1\n", text)
    text = re.sub(rf"([.!?]){_H}+(?=[가-힣A-Z])", r"\1\n", text)

    text = re.sub(rf"{_H}*\n{_H}*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = _restore_protected(text, protected)
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
