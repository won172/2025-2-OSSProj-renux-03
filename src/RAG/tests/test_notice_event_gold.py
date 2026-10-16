"""공지 일정 추출 골드셋의 형식 검증.

정확도 임계값은 두지 않는다(현행 추출기는 기준선). DB 없이 CSV만 읽는다.
"""
from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path

import pytest

GOLD_PATH = Path(__file__).resolve().parent / "fixtures" / "notice_event_gold.csv"

REQUIRED_COLUMNS = [
    "source_key",
    "notice_id",
    "board",
    "title",
    "url",
    "published_date",
    "body_excerpt",
    "event_kind",
    "multi_event",
    "start_date",
    "end_date",
    "year_inferred",
    "target",
    "evidence_text",
    "content_location",
    "label_source",
    "human_verified",
    "human_review_priority",
    "notes",
]
EVENT_KINDS = {"application", "deadline", "event", "exam", "other", "none"}
LOCATIONS = {"title", "body", "attachment_only", "none"}
# evidence_text에 근거가 여러 개면 이 구분자로 나눈다. 각 조각이 제목 또는 본문의 정확한 부분 문자열이다.
EVIDENCE_SEPARATOR = " || "
# 공개 공지에 실린 기관(부서) 공용 주소만 허용한다. 개인 주소는 수집 단계에서 가린다.
ALLOWED_EMAILS = {"dgupass@dongguk.edu", "mind@dongguk.edu"}
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 원문 본문 전체는 싣지 않는다. 근거 주변 발췌만 둔다(전체 본문은 url 또는 eval --db로 확인).
EXCERPT_MAX_CHARS = 1200
LEAD_EXCERPT_MAX_CHARS = 200
EXCERPT_JOINER = " … "
PERSONAL_PATTERNS = [
    re.compile(r"01[016789][-. ]?\d{3,4}[-. ]?\d{4}"),  # 휴대전화
]


def _load():
    with GOLD_PATH.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames or [], list(reader)


def _iso(value: str) -> date | None:
    if not value:
        return None
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), value
    return date.fromisoformat(value)


def test_gold_file_is_small_and_has_required_columns():
    assert GOLD_PATH.stat().st_size < 600_000
    columns, rows = _load()
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    assert not missing
    assert 40 <= len(rows) <= 100


def test_gold_rows_are_unique():
    _, rows = _load()
    keys = [r["source_key"] for r in rows]
    assert len(keys) == len(set(keys))
    for row in rows:
        assert row["source_key"] == f"notices:{row['notice_id']}"


@pytest.mark.parametrize("row", _load()[1], ids=lambda r: r["source_key"])
def test_gold_row_schema(row):
    assert row["title"].strip()
    assert row["url"].startswith("https://")
    published = _iso(row["published_date"])
    assert published is not None
    assert row["event_kind"] in EVENT_KINDS
    assert row["content_location"] in LOCATIONS
    assert row["label_source"] == "auto_draft_claude"
    assert row["human_verified"] in {"false", "true"}
    assert row["human_review_priority"] in {"0", "1"}
    assert row["multi_event"] in {"false", "true"}
    assert row["year_inferred"] in {"false", "true"}

    start = _iso(row["start_date"])
    end = _iso(row["end_date"])
    if start and end:
        assert start <= end
    if start or end:
        # 날짜가 있으면 근거 문자열과 위치가 있어야 한다.
        assert row["evidence_text"]
        assert row["content_location"] in {"title", "body"}
    else:
        assert row["year_inferred"] == "false"
    if row["multi_event"] == "true":
        # 다회차 공지는 하나의 기간으로 묶지 않는다: 날짜 공란 + 회차 근거 + notes에 회차 목록.
        assert not start and not end
        assert row["evidence_text"]
        assert row["notes"].strip()
    if row["content_location"] in {"attachment_only", "none"}:
        assert not start and not end
    if row["event_kind"] == "none":
        assert not start and not end
    if not (start or end) and row["event_kind"] != "none":
        # 날짜를 비운 이유를 남긴다.
        assert row["notes"].strip()


@pytest.mark.parametrize("row", _load()[1], ids=lambda r: r["source_key"])
def test_evidence_is_exact_substring_of_title_or_body(row):
    evidence = row["evidence_text"]
    if not evidence:
        return
    snippets = evidence.split(EVIDENCE_SEPARATOR)
    for snippet in snippets:
        assert snippet.strip() == snippet and snippet, (row["source_key"], snippet)
        assert snippet in row["title"] or snippet in row["body_excerpt"], (row["source_key"], snippet)


@pytest.mark.parametrize("row", _load()[1], ids=lambda r: r["source_key"])
def test_content_location_is_where_date_evidence_appears(row):
    """content_location = 날짜 근거(evidence_text)가 나타나는 곳.

    title: 모든 근거 조각이 제목에 있음 / body: 제목에 없고 모든 조각이 본문에 있음 /
    attachment_only·none: 제목·본문에 날짜 근거가 없음(본문이 비어 첨부만 있으면 attachment_only).
    """
    evidence = row["evidence_text"]
    location = row["content_location"]
    if not evidence:
        assert location in {"attachment_only", "none"}
        return
    snippets = evidence.split(EVIDENCE_SEPARATOR)
    if all(s in row["title"] for s in snippets):
        assert location == "title"
    else:
        assert location == "body"
        assert all(s in row["body_excerpt"] for s in snippets)


def test_gold_has_no_mobile_numbers():
    _, rows = _load()
    for row in rows:
        for field in ("title", "body_excerpt", "target", "evidence_text", "notes"):
            for pattern in PERSONAL_PATTERNS:
                assert not pattern.search(row[field]), (row["source_key"], field)


@pytest.mark.parametrize("row", _load()[1], ids=lambda r: r["source_key"])
def test_body_excerpt_is_capped(row):
    excerpt = row["body_excerpt"]
    assert len(excerpt) <= EXCERPT_MAX_CHARS
    if not row["evidence_text"]:
        # 근거가 없는 행은 앞부분 짧은 발췌(또는 공란)만 두고 이유를 notes에 남긴다.
        assert len(excerpt) <= LEAD_EXCERPT_MAX_CHARS
        assert EXCERPT_JOINER not in excerpt
        assert row["notes"].strip()


def test_gold_has_no_full_body_column():
    columns, _ = _load()
    assert "body" not in columns


def test_gold_has_only_institutional_emails():
    _, rows = _load()
    for row in rows:
        for field in ("title", "body_excerpt", "target", "evidence_text", "notes"):
            found = set(EMAIL_PATTERN.findall(row[field]))
            assert found <= ALLOWED_EMAILS, (row["source_key"], field, found - ALLOWED_EMAILS)


def test_gold_csv_uses_lf_line_endings():
    assert b"\r\n" not in GOLD_PATH.read_bytes()


def test_human_review_subset_is_marked():
    _, rows = _load()
    flagged = [r for r in rows if r["human_review_priority"] == "1"]
    assert 15 <= len(flagged) <= 25
