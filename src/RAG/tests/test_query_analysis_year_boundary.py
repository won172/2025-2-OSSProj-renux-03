from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils.query_years import (
    extract_explicit_years,
    filter_unstated_year_values,
    introduces_unstated_year,
    user_history_only,
)


def test_llm_cannot_add_a_year_absent_from_user_question():
    allowed = extract_explicit_years("2학기 수강신청 기간")
    safe = filter_unstated_year_values(
        ["2023-2학기 수강신청 기간", "수강신청 일정"],
        allowed_years=allowed,
    )

    assert safe == ["수강신청 일정"]
    assert introduces_unstated_year("2023-2학기 수강신청 기간", allowed) is True


def test_explicit_compact_academic_year_is_preserved():
    allowed = extract_explicit_years("26-2 수강신청 기간")

    assert extract_explicit_years("26-2 수강신청") == {2026}
    assert introduces_unstated_year("2026학년도 2학기 수강신청 기간", allowed) is False


def test_only_user_history_can_authorize_a_followup_year():
    history = user_history_only(
        "사용자: 2026학년도 2학기 일정 알려줘\n"
        "도우미: 2023년 일정도 참고하세요"
    )
    allowed = extract_explicit_years(history)

    assert allowed == {2026}
    assert introduces_unstated_year("2026학년도 수강신청 기간", allowed) is False
    assert introduces_unstated_year("2023학년도 수강신청 기간", allowed) is True
