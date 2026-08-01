"""학부·대학원 대상 신호를 수집과 질의에서 동일하게 해석한다."""
from __future__ import annotations

import re
import unicodedata


def compact_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"\s+", "", normalized)


def derive_audience(value: object) -> str:
    compact = compact_text(value)
    has_graduate = any(
        term in compact
        for term in ("대학원", "대학원실", "석사", "박사", "graduate")
    )
    has_undergraduate = any(
        term in compact
        for term in ("학부", "학사과정", "undergraduate")
    )
    if has_graduate and has_undergraduate:
        return "common"
    if has_graduate:
        return "graduate"
    if has_undergraduate:
        return "undergraduate"
    return "common"


def query_audience(query: str) -> str:
    compact = compact_text(query)
    explicit = derive_audience(query)
    if explicit != "common":
        return explicit
    # 대상 신호 없는 일반 수강신청 문의는 주 사용자층인 학부로 해석한다.
    if "수강신청" in compact:
        return "undergraduate"
    return "common"


__all__ = ["compact_text", "derive_audience", "query_audience"]
