"""학과명 → 소속 단과대 매핑 유틸.

'통계학과 졸업요건'처럼 학과명으로 물으면, 졸업기준 자료는 단과대(이과대학) 단위라
검색이 실패한다. 질문에서 학과명을 찾아 소속 단과대를 알려줘, 검색어에 단과대명을
보강할 수 있게 한다(query-time bridge).
"""
from __future__ import annotations

import functools
from typing import Dict, List, Optional

import pandas as pd

from src.config import DATA_DIR

_GRAD_TERMS = ("졸업", "요건", "학점", "이수", "수료", "졸업기준")


@functools.lru_cache(maxsize=1)
def _dept_to_college() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    path = DATA_DIR / "dongguk_departments_catalog.csv"
    try:
        df = pd.read_csv(path).fillna("").astype(str)
        for _, row in df.iterrows():
            dept = str(row.get("department_name", "")).strip()
            college = str(row.get("college_name", "")).strip()
            if dept and college and college != "대학":
                mapping[dept] = college
    except Exception:
        pass
    return mapping


def college_for_query(query: str) -> Optional[str]:
    """질문에 등장한 학과명을 찾아 소속 단과대를 반환(없으면 None). 긴 이름 우선 매칭."""
    if not query:
        return None
    mapping = _dept_to_college()
    for dept in sorted(mapping, key=len, reverse=True):
        if dept in query:
            return mapping[dept]
    return None


def college_grad_queries(query: str) -> List[str]:
    """졸업·이수 관련 질문에 학과가 언급되면 '<단과대> 졸업기준' 검색어를 보강해 반환."""
    if not query or not any(t in query for t in _GRAD_TERMS):
        return []
    college = college_for_query(query)
    return [f"{college} 졸업기준"] if college else []


def college_of(major: Optional[str]) -> Optional[str]:
    """학과명(정확히 일치)으로 소속 단과대를 직접 조회."""
    if not major:
        return None
    return _dept_to_college().get(major.strip())


def personalized_grad_queries(query: str, major: Optional[str]) -> List[str]:
    """로그인 사용자의 학과(프로필)를 기준으로 졸업/요건 검색어를 보강.

    질문에 학과를 안 밝혀도(예: "졸업요건 알려줘") 본인 학과·단과대 기준 자료가
    검색되도록, 졸업·이수 관련 질문일 때 '<학과> 졸업기준'과 '<단과대> 졸업기준'을 더한다.
    """
    if not query or not major or not any(t in query for t in _GRAD_TERMS):
        return []
    major = major.strip()
    out = [f"{major} 졸업기준"]
    college = college_of(major)
    if college:
        out.append(f"{college} 졸업기준")
    return out


def user_scope_label(major: Optional[str]) -> str:
    """'통계학과 (이과대학)' 형태의 소속 라벨. 단과대 미상이면 학과만, 학과 미상이면 빈 문자열."""
    if not major:
        return ""
    major = major.strip()
    college = college_of(major)
    return f"{major} ({college})" if college else major


__all__ = [
    "college_for_query",
    "college_grad_queries",
    "college_of",
    "personalized_grad_queries",
    "user_scope_label",
]
