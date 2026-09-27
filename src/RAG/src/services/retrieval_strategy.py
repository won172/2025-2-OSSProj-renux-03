"""Choose the first retrieval path for narrowly scoped academic questions."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence


_COHORT_RE = re.compile(r"(?<!\d)(?:\d{2}|20\d{2})\s*학번")
_COURSE_CODE_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2,6}\d{3,5}(?![A-Za-z0-9])")


@dataclass(frozen=True)
class RetrievalStrategy:
    mode: str
    dataset: str | None = None


def choose_retrieval_strategy(query: str, route: Sequence[str]) -> RetrievalStrategy:
    """Use relation evidence first only where the query has an exact scope.

    Multi-dataset and general prose questions retain the hybrid path. A plan
    is only an attempt: absent or stale graph evidence must fall back to the
    existing lexical/vector retrieval at request time.
    """

    if len(route) != 1:
        return RetrievalStrategy("hybrid")
    dataset = route[0]
    compact = re.sub(r"\s+", "", query).lower()
    if dataset == "staff" and any(
        term in compact for term in ("전화", "연락처", "이메일", "메일", "담당자", "사무실")
    ):
        return RetrievalStrategy("structured", dataset)
    if dataset == "courses" and (
        _COURSE_CODE_RE.search(query)
        or any(term in compact for term in ("개설학과", "어느학과", "어느전공", "학수번호", "과목코드"))
    ):
        return RetrievalStrategy("structured", dataset)
    if (
        dataset == "rules"
        and _COHORT_RE.search(query)
        and "조기졸업" not in compact
        and any(term in compact for term in ("졸업", "교양", "학업이수"))
    ):
        return RetrievalStrategy("structured", dataset)
    return RetrievalStrategy("hybrid")
