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


def test_normalize_whitespace_keeps_list_markers_with_their_content():
    """항목 번호는 내용과 같은 줄에 있어야 한다.

    예전 동작은 "1.\n신청 대상 2.\n신청 기간"이었다. 항목 번호만 줄 끝에 남고
    다음 항목("2.")이 앞 항목의 내용에 붙는다. 분할기가 줄 경계를 쓰기 때문에
    "1."만 청크 끝에 남고 "신청 대상"은 다음 청크로 넘어갈 수 있었다.
    """
    assert normalize_whitespace("1. 신청 대상 2. 신청 기간") == "1. 신청 대상\n2. 신청 기간"


def test_normalize_whitespace_breaks_after_a_phone_number_sentence():
    """전화번호 뒤 마침표는 문장 끝이다(오른쪽이 숫자가 아니므로 분리된다)."""
    assert (
        normalize_whitespace("문의: 02-2260-3699. 학사지원팀입니다.")
        == "문의: 02-2260-3699.\n학사지원팀입니다."
    )


# ---------- 구조 보존 (회귀) ----------

def test_normalize_whitespace_keeps_urls_and_emails_intact():
    """URL의 구두점은 값의 일부다.

    구두점 정규화가 URL을 통과하던 동안 공지 청크의 36.8%, 과목 청크의 95.6%에
    "https: / / www. dongguk. edu/ apply? id=3" 형태로 깨진 링크가 들어갔다.
    신청 링크는 공지 답변에서 학생이 가장 필요로 하는 값이다.
    """
    assert (
        normalize_whitespace("신청: https://www.dongguk.edu/apply?id=3 에서 하세요")
        == "신청: https://www.dongguk.edu/apply?id=3 에서 하세요"
    )
    assert (
        normalize_whitespace("문의: haksa@dongguk.edu 로 보내주세요")
        == "문의: haksa@dongguk.edu 로 보내주세요"
    )


def test_normalize_whitespace_preserves_paragraph_boundaries():
    """분할기의 첫 구분자 ``"\n\n"``이 실제로 매치되어야 한다.

    예전 구현은 ``\n{2,}``를 단일 ``\n``으로 눌러서 문단 경계가 입력에
    남지 않았고, RecursiveCharacterTextSplitter의 문단 분할이 죽어 있었다.
    """
    out = normalize_whitespace("첫째 문단입니다.\n\n둘째 문단입니다.")
    assert "\n\n" in out


def test_normalize_whitespace_joins_hard_wraps_but_keeps_real_breaks():
    """하드랩만 잇고 진짜 줄 경계는 남긴다.

    공지 본문 줄바꿈 202,784개 중 67.9%는 하드랩이고 32.1%(65,026개)는 실제
    경계다. 예전처럼 전부 공백으로 접으면 이 코퍼스에 남은 유일한 구조 신호가
    사라진다.
    """
    # 앞줄이 문장부호 없이 끝나면 하드랩으로 보고 잇는다.
    assert "\n" not in normalize_whitespace("장학금 신청 기간을\n안내드립니다")
    # 앞줄이 종결되면 경계를 남긴다.
    assert "\n" in normalize_whitespace("안내드립니다.\n신청 방법은 다음과 같습니다")
    # 뒷줄이 새 항목으로 시작하면 경계를 남긴다.
    assert "\n" in normalize_whitespace("대상은 다음과 같다\n- 재학생")


def test_normalize_whitespace_is_idempotent():
    """to_chunks → chunk_text 경로에서 두 번 적용되므로 결과가 안정해야 한다."""
    sample = (
        "모집 안내입니다.\n\n1. 대상: 재학생 2. 기간: 2026.03.02 ~ 03.10\n"
        "신청은 https://www.dongguk.edu/apply?id=3 에서 합니다."
    )
    once = normalize_whitespace(sample)
    assert normalize_whitespace(once) == once


def test_strip_html_marks_block_boundaries_as_paragraphs():
    """블록 경계는 인라인 분절과 구분되어야 normalize_whitespace가 판단할 수 있다."""
    out = strip_html("<p>첫째 문단</p><p>둘째 문단</p>")
    assert "\n\n" in out
    # 인라인 태그는 문단 경계를 만들지 않는다.
    assert "\n\n" not in strip_html("<p>굵은 <b>강조</b> 텍스트</p>").strip()


def test_normalize_whitespace_keeps_korean_ordinals_with_their_content():
    """공문서의 "가. / 나." 순서표도 숫자 번호와 같은 규칙을 따라야 한다.

    예전 동작에서는 "…말한다. 가. 이사장"과 "가. 2026-1학기"가 모두 "가."만 줄
    끝에 남기고 내용과 갈라졌다(공지·학칙 샘플에서 6,145곳).
    """
    assert (
        normalize_whitespace("다음 각 목을 말한다. 가. 이사장 나. 교직원")
        == "다음 각 목을 말한다.\n가. 이사장\n나. 교직원"
    )
    assert normalize_whitespace("지원자격 가. 2026-1학기 재학생") == "지원자격\n가. 2026-1학기 재학생"
    # 항목 번호만 남은 줄은 다음 줄의 내용과 붙는다.
    assert normalize_whitespace("가.\n명칭: 현장체험 프로그램") == "가. 명칭: 현장체험 프로그램"


def test_normalize_whitespace_does_not_treat_dates_or_words_as_list_items():
    """날짜("5.4.")와 약어("석.")는 새 항목이 아니므로 앞줄과 잇는다."""
    assert "\n" not in normalize_whitespace("신청기한\n5.4. (월)17:00까지")
    assert normalize_whitespace("모집 과정은\n석. 박사 과정").startswith("모집 과정은 석.")
    # 콜론으로 끝난 라벨은 값과 같은 줄에 남는다.
    assert normalize_whitespace("모집기간:\n2026.05.10까지") == "모집기간: 2026.05.10까지"
