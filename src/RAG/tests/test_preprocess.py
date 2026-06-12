"""preprocess.py 정제/청킹 단위 테스트.

실행: cd src/RAG && python -m pytest tests/ -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.preprocess import (  # noqa: E402
    chunk_text,
    normalize_unicode,
    normalize_whitespace,
    standardize_date,
    strip_html,
    to_chunks,
)


# ---------- strip_html ----------

def test_strip_html_removes_script_and_style_bodies():
    html = '<p>공지</p><script>var x=1;alert("evil");</script><style>.a{color:red}</style>본문'
    out = strip_html(html)
    assert "alert" not in out
    assert "color" not in out
    assert "공지" in out and "본문" in out


def test_strip_html_unescapes_entities():
    out = strip_html("<p>A&nbsp;&amp;&lt;B&gt;</p>")
    assert "&nbsp;" not in out and "&amp;" not in out
    assert "&" in out


def test_strip_html_preserves_block_boundaries():
    out = strip_html("<div>첫째</div><div>둘째</div>")
    assert "첫째" in out and "둘째" in out
    # 블록 경계가 줄바꿈/공백으로 분리되어 단어가 붙지 않아야 함
    assert "첫째둘째" not in out.replace("\n", "").replace(" ", "") or "\n" in out


def test_strip_html_non_string():
    assert strip_html(None) == ""
    assert strip_html(123) == ""  # type: ignore[arg-type]


# ---------- normalize_unicode ----------

def test_normalize_unicode_fullwidth_and_invisible():
    out = normalize_unicode("（전각）１２３​끝")
    assert "（" not in out and "）" not in out
    assert "123" in out
    assert "​" not in out


# ---------- standardize_date ----------

def test_standardize_date_formats():
    assert standardize_date("2026-06-05") == "2026-06-05"
    assert standardize_date("2026.06.05") == "2026-06-05"
    assert standardize_date("2026.06.05.") == "2026-06-05"  # 공지 게시일 형식
    assert standardize_date("2026.6.5") == "2026-06-05"  # 한 자리 월/일
    assert standardize_date("2026. 06. 05") == "2026-06-05"  # 공백 포함
    assert standardize_date("2026년 6월 5일") == "2026-06-05"
    assert standardize_date("등록일 2026.06.09.") == "2026-06-09"  # 내장 텍스트


def test_standardize_date_invalid():
    assert standardize_date("2026.13.45") is None  # 존재하지 않는 날짜
    assert standardize_date("없음") is None
    assert standardize_date(None) is None
    assert standardize_date("") is None


def test_standardize_date_date_objects():
    from datetime import date, datetime

    assert standardize_date(date(2026, 6, 5)) == "2026-06-05"
    assert standardize_date(datetime(2026, 6, 5, 12, 30)) == "2026-06-05"


# ---------- chunking ----------

def test_chunk_text_respects_size():
    text = " ".join(f"문장{i}입니다." for i in range(100))
    chunks = chunk_text(text, size=200, overlap=50)
    assert len(chunks) > 1
    assert all(len(c) <= 220 for c in chunks)  # splitter 여유 포함


def test_to_chunks_skips_empty_segments():
    docs = [{"doc_id": "d1", "title": "제목", "text": "   "}]
    chunks = to_chunks(docs, chunk_size=100, include_title=True)
    # 빈 본문이라도 chunk_text 폴백([text])에서 공백 세그먼트는 제외돼야 함
    assert all(c["chunk_text"].strip() for c in chunks)


def test_to_chunks_includes_title_prefix():
    docs = [{"doc_id": "d1", "title": "장학 공지", "text": "신청 기간 안내"}]
    chunks = to_chunks(docs, chunk_size=100, include_title=True)
    assert chunks[0]["chunk_text"].startswith("[장학 공지]")
