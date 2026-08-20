"""공지의 학과별 공개 범위를 정규화하는 작은 공용 규칙 모음."""
from __future__ import annotations

import re


PUBLIC_VISIBILITY = "public"
DEPARTMENT_VISIBILITY = "department"
DEPARTMENT_NOTICE_BOARDS = frozenset({"학과지식", "학과행사", "학과공지"})

_PUBLIC_VALUES = {"public", "all", "전체", "전체공개", "공개"}
_DEPARTMENT_VALUES = {"department", "dept", "major", "학과", "학과전용", "소속학과"}
_DEPARTMENT_LINE_RE = re.compile(
    r"(?im)^\s*(?:주관(?:\s*학과)?|주관부서|학과)\s*:\s*(?P<department>[^\n]+?)\s*$"
)


def clean_department(value: object) -> str:
    """비어 있지 않은 학과명을 비교 가능한 한 줄 문자열로 만든다."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_notice_visibility(
    value: object,
    department: object,
    *,
    default: str = PUBLIC_VISIBILITY,
) -> str:
    """허용된 범위만 반환하고, 대상 학과 없는 학과 전용 범위는 공개로 되돌린다."""
    raw = str(value or "").strip().lower().replace(" ", "")
    fallback = str(default or PUBLIC_VISIBILITY).strip().lower()
    if raw in _DEPARTMENT_VALUES:
        candidate = DEPARTMENT_VISIBILITY
    elif raw in _PUBLIC_VALUES:
        candidate = PUBLIC_VISIBILITY
    elif not raw:
        candidate = fallback
    else:
        candidate = PUBLIC_VISIBILITY

    if candidate == DEPARTMENT_VISIBILITY and clean_department(department):
        return DEPARTMENT_VISIBILITY
    return PUBLIC_VISIBILITY


def extract_department_from_notice_content(content: object) -> str:
    """기존 수동 공지 본문의 ``주관: 학과명`` 표기에서 대상 학과를 복구한다."""
    match = _DEPARTMENT_LINE_RE.search(str(content or ""))
    return clean_department(match.group("department")) if match else ""


__all__ = [
    "DEPARTMENT_NOTICE_BOARDS",
    "DEPARTMENT_VISIBILITY",
    "PUBLIC_VISIBILITY",
    "clean_department",
    "extract_department_from_notice_content",
    "normalize_notice_visibility",
]
