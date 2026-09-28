"""Offline route-gate comparison against origin/main 4657709; no retrieval or LLM.

Run from src/RAG: python tests/measure_simple_query_bypass.py
The baseline copies the pre-change _can_skip_query_analysis predicate. Counts
assume a first-turn question and that the request reaches query analysis.
Typo/spacing analysis skips, identifier analysis skips, and single-document
score-based selector skips remain future work pending human-judged qrels.
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import rag_service as service  # noqa: E402
from src.services.retrieval_strategy import choose_retrieval_strategy  # noqa: E402


def baseline_analysis_skip(question: str, normalized: str, route: list[str]) -> bool:
    """The original route predicate, before this branch's new allowances."""
    if normalized.strip() != question.strip() or not service._has_school_info_terms(question):
        return False
    if route not in (["notices"], ["staff"], ["courses"], ["rules"]):
        return False
    if route == ["rules"] and choose_retrieval_strategy(question, route).mode != "structured":
        return False
    if route == ["notices"] and not any(term in question for term in (
        "공지", "장학", "모집", "공모전", "발표", "등록금", "입학", "입시",
        "채용", "신청", "교내", "캠퍼스", "시설", "도서관", "열람실", "와이파이",
        "분실물", "셔틀", "프린터", "학생증", "기숙사", "생활관", "식권",
        "편의점", "운영시간", "공부 공간", "공부공간", "학습 공간", "학습공간",
    )):
        return False
    if any(term in question for term in (
        "오늘", "내일", "모레", "이번", "다음", "현재", "지금", "요즘", "최근", "최신",
    )) and not (
        service._is_active_notice_state_query(question, route)
        or service._is_recent_notice_query(question, route)
    ):
        return False
    return True


def main() -> None:
    matrix = Path(__file__).with_name("golden_matrix.csv")
    counts: Counter[str] = Counter()
    routes: Counter[str] = Counter()
    with matrix.open(encoding="utf-8-sig", newline="") as source:
        for case in csv.DictReader(source):
            question = case["question"]
            normalized = service._query_for_analysis(question)
            route = service._resolve_retrieval_route(
                question, service.QueryAnalysisMeta(result=None, used=False, failed=False),
            )
            counts["questions"] += 1
            before = baseline_analysis_skip(question, normalized, route)
            after = service._can_skip_query_analysis(question, normalized, "")
            counts["analysis_before"] += before
            counts["analysis_after"] += after
            counts["newly_skipped"] += after and not before
            counts["no_longer_skipped"] += before and not after
            routes[";".join(route)] += 1
            # These preexisting notice branches are decidable from text and route.
            # Actual calls still require the request to reach evidence selection.
            if service._is_active_notice_state_query(question, route) or service._is_recent_notice_query(question, route):
                counts["known_selector_before"] += 1
                counts["known_selector_after"] += 1

    print(f"golden_matrix questions: {counts['questions']}")
    print(f"analysis skips before: {counts['analysis_before']}; after: {counts['analysis_after']}")
    print(
        f"analysis newly skipped: {counts['newly_skipped']}; "
        f"previous skips now analyzed: {counts['no_longer_skipped']}"
    )
    print(
        "selector skips decidable from notice text/route, before: "
        f"{counts['known_selector_before']}; after: {counts['known_selector_after']}"
    )
    print("selector skips requiring shortlist/SQL resolution, before: not measurable offline; after: not measurable offline")
    print("actual selector call count: not measurable offline (retrieval/direct-handler outcomes unavailable)")
    print(f"deterministic routes: {dict(sorted(routes.items()))}")


if __name__ == "__main__":
    main()
