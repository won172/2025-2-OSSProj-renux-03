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


def test_notice_line_break_does_not_split_academic_year_token():
    normalized = normalize_whitespace("수강신청 기간: 2026\n학년도 2학기")

    assert "2026학년도" in normalized


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


# ---------- 숫자 값 보존 (회귀) ----------

def test_normalize_whitespace_keeps_gpa_thresholds_intact():
    """평점 기준이 쪼개지면 장학·졸업·학사경고 답변의 핵심 수치가 무너진다.

    이 규칙이 없던 동안 코퍼스의 40.8%(13,250/32,508 청크)에서 숫자가 갈라져
    있었다. "평점 3.5 이상"이 "평점 3. 5 이상"으로 임베딩되고 근거 텍스트로도
    그대로 LLM에 들어갔다.
    """
    assert normalize_whitespace("평점 3.5 이상") == "평점 3.5 이상"
    assert normalize_whitespace("평균 학점이 2.0 미만인 경우") == "평균 학점이 2.0 미만인 경우"


def test_normalize_whitespace_keeps_amounts_and_ratios_intact():
    assert normalize_whitespace("등록금 1,250,000원") == "등록금 1,250,000원"
    assert normalize_whitespace("비율 1/2 기준") == "비율 1/2 기준"


def test_normalize_whitespace_keeps_numeric_dates_intact():
    assert normalize_whitespace("개정 2004.04.03, 2023.11.21") == "개정 2004.04.03, 2023.11.21"
    assert normalize_whitespace("<개정 ’06.12.15>") == "<개정 ’06.12.15>"


def test_normalize_whitespace_still_breaks_sentences():
    """숫자 보호가 문장 분리를 죽이면 안 된다 — 그건 이 규칙의 원래 목적이다."""
    assert normalize_whitespace("신청하세요. 감사합니다.") == "신청하세요.\n감사합니다."
    assert normalize_whitespace("끝났다. 2026년에는 달라진다.") == "끝났다.\n2026년에는 달라진다."


def test_normalize_whitespace_still_breaks_numbered_lists():
    assert normalize_whitespace("1. 신청 대상 2. 신청 기간") == "1.\n신청 대상 2.\n신청 기간"


def test_normalize_whitespace_breaks_after_a_phone_number_sentence():
    """전화번호 뒤 마침표는 문장 끝이다(오른쪽이 숫자가 아니므로 분리된다)."""
    assert (
        normalize_whitespace("문의: 02-2260-3699. 학사지원팀입니다.")
        == "문의: 02-2260-3699.\n학사지원팀입니다."
    )
