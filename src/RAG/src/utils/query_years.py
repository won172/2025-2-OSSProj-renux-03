"""질의 재작성에서 사용자가 밝히지 않은 학년도가 생기지 않게 하는 순수 로직."""
from __future__ import annotations

import re
from typing import Iterable


_FOUR_DIGIT_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_SHORT_ACADEMIC_YEAR_RE = re.compile(
    r"(?<!\d)(\d{2})\s*(?:[-./]\s*[12](?!\d)|학번|학년도)"
)


def user_history_only(history_text: str) -> str:
    lines = []
    for line in str(history_text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("사용자:"):
            lines.append(stripped.split("사용자:", 1)[1].strip())
    return "\n".join(lines)


def extract_explicit_years(text: str) -> set[int]:
    years = {int(value) for value in _FOUR_DIGIT_YEAR_RE.findall(str(text or ""))}
    for value in _SHORT_ACADEMIC_YEAR_RE.findall(str(text or "")):
        years.add(2000 + int(value))
    return years


def introduces_unstated_year(text: str, allowed_years: set[int]) -> bool:
    return bool(extract_explicit_years(text) - allowed_years)


def filter_unstated_year_values(
    values: Iterable[str],
    *,
    allowed_years: set[int],
) -> list[str]:
    return [
        value
        for value in values
        if not introduces_unstated_year(value, allowed_years)
    ]


__all__ = [
    "extract_explicit_years",
    "filter_unstated_year_values",
    "introduces_unstated_year",
    "user_history_only",
]
