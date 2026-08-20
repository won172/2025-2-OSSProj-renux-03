from __future__ import annotations

import csv
from datetime import date
from pathlib import Path


EVALUATION_SET = Path(__file__).with_name("evaluation_set.csv")
RAG_ROOT = Path(__file__).resolve().parents[1]


def _rows() -> list[dict[str, str]]:
    with EVALUATION_SET.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_evaluation_set_has_fixed_dates_and_safety_margin():
    rows = _rows()
    assert len(rows) >= 60
    assert all(row["as_of"] and date.fromisoformat(row["as_of"]) for row in rows)


def test_evaluation_set_covers_temporal_registration_boundaries():
    rows = _rows()
    questions = {row["question"]: row for row in rows}
    required = {
        "수강신청 언제야?",
        "학부 수강신청 언제야?",
        "대학원 수강신청 언제야?",
        "2023학년도 수강신청 규정 알려줘",
        "1학기 종강일은 언제야?",
        "여름 방학은 언제 시작해?",
        "2학기 개강일 알려줘",
    }
    assert required <= set(questions)
    assert "대학원" in questions["학부 수강신청 언제야?"]["forbidden_keywords"]
    assert "학부" in questions["대학원 수강신청 언제야?"]["forbidden_keywords"]
    assert "2026" in questions["2023학년도 수강신청 규정 알려줘"]["forbidden_keywords"]


def test_evaluation_set_covers_active_notice_deadline_boundaries():
    rows = _rows()
    matching = [
        row
        for row in rows
        if row["question"] == "현재 진행 중인 공모전 알려줘"
    ]

    assert {row["as_of"] for row in matching} >= {
        "2026-07-31",
        "2030-01-01",
    }
    current = next(
        row for row in matching if row["as_of"] == "2026-07-31"
    )
    future = next(
        row for row in matching if row["as_of"] == "2030-01-01"
    )
    assert "2024-09-20" in current["forbidden_keywords"]
    assert (
        future["expected_fallback_reason"]
        == "active_deadline_filter_eliminated_all"
    )
    assert any(
        row["question"]
        == "2024 DB 이노베이션챌린지 공모전 마감일 알려줘"
        for row in rows
    )


def test_clean_test_environment_contract_is_python_311_and_asyncio_enabled():
    assert (RAG_ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.11"
    requirements = (RAG_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "pytest>=" in requirements
    assert "pytest-asyncio>=" in requirements
