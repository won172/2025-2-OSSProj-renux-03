"""마감/날짜범위 질의에 대한 notices 하이브리드 검색 Recall@K 측정.

측정 목적:
  - '다음 주에 끝나는 장학금', '이번 달 마감 공지' 류 질의에서
    실제 해당 기간에 마감되는 공지가 top_k 내에 포함되는지(Recall@K) 수치화.
  - top_k 상향만으로 recall이 의미 있게 오르는지, 아니면 검색 자체로는 한계인지 분리.

실행:
    cd src/RAG
    python -m pytest tests/test_deadline_recall.py -v -s

또는 스탠드얼론:
    cd src/RAG
    python tests/test_deadline_recall.py
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from typing import List, Optional, Set

import pandas as pd

# 패키지 경로 설정 (pytest / 직접 실행 모두 호환)
_RAG_DIR = Path(__file__).resolve().parents[1]
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from src.config import HYBRID_ALPHA, DEFAULT_TOP_K
from src.search.hybrid import load_tfidf_with_ids, hybrid_search_with_meta

# --------------------------------------------------------------------------- #
# Ground-truth 구성
# --------------------------------------------------------------------------- #
# 오늘 기준: 2026-06-14
_TODAY = date(2026, 6, 14)
_THIS_WEEK_END = date(2026, 6, 20)   # 이번 주 일요일
_NEXT_WEEK_START = date(2026, 6, 21)
_NEXT_WEEK_END = date(2026, 6, 27)
_THIS_MONTH_END = date(2026, 6, 30)

_TITLE_DEADLINE_PAT = re.compile(r"~(\d+)/(\d+)")


def _deadline_from_title(title: str) -> Optional[date]:
    """제목의 (~MM/DD) 패턴에서 마감일 파싱."""
    m = _TITLE_DEADLINE_PAT.search(str(title))
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        try:
            return date(2026, month, day)
        except ValueError:
            return None
    return None


def _build_ground_truth(df: pd.DataFrame) -> dict:
    """공지 청크 DataFrame에서 마감일 카테고리별 ground-truth chunk_id 집합 반환."""
    gt: dict = {
        "this_week": set(),       # 이번 주 마감 (~6/20)
        "next_week": set(),       # 다음 주 마감 (6/21~6/27)
        "this_month": set(),      # 이번 달 마감 (6/14~6/30)
        "scholarship_next_week": set(),  # 다음 주 마감 장학금
        "scholarship_this_month": set(), # 이번 달 마감 장학금
        "imminent": set(),        # 마감 임박 (7일 이내)
    }
    for _, row in df.iterrows():
        cid = str(row["chunk_id"])
        title = str(row["title"])
        deadline = _deadline_from_title(title)
        if deadline is None:
            continue

        is_scholarship = any(
            kw in title for kw in ["장학", "장학금", "Scholarship", "scholarship", "SRD"]
        )
        imminent_end = date(2026, 6, 21)  # 오늘+7일

        if _TODAY <= deadline <= _THIS_WEEK_END:
            gt["this_week"].add(cid)
        if _NEXT_WEEK_START <= deadline <= _NEXT_WEEK_END:
            gt["next_week"].add(cid)
        if _TODAY <= deadline <= _THIS_MONTH_END:
            gt["this_month"].add(cid)
        if _TODAY <= deadline <= imminent_end:
            gt["imminent"].add(cid)
        if _NEXT_WEEK_START <= deadline <= _NEXT_WEEK_END and is_scholarship:
            gt["scholarship_next_week"].add(cid)
        if _TODAY <= deadline <= _THIS_MONTH_END and is_scholarship:
            gt["scholarship_this_month"].add(cid)

    return gt


# --------------------------------------------------------------------------- #
# 평가 질의셋 정의
# --------------------------------------------------------------------------- #
DEADLINE_QUERIES = [
    {
        "query": "다음 주에 끝나는 장학금 있어?",
        "gt_key": "scholarship_next_week",
        "note": "다음 주 마감 장학금 공지",
    },
    {
        "query": "이번 달 마감 공지 알려줘",
        "gt_key": "this_month",
        "note": "이번 달 이내 마감 전체",
    },
    {
        "query": "신청 마감 임박한 장학금",
        "gt_key": "imminent",
        "note": "7일 이내 마감 임박",
    },
    {
        "query": "이번 주까지 신청해야 하는 거",
        "gt_key": "this_week",
        "note": "이번 주 마감",
    },
    {
        "query": "이번 달 장학금 신청 마감",
        "gt_key": "scholarship_this_month",
        "note": "이번 달 마감 장학금",
    },
    {
        "query": "6월 안에 신청 마감되는 장학금",
        "gt_key": "scholarship_this_month",
        "note": "6월 마감 장학금 (명시적 월)",
    },
    {
        "query": "다음주 마감 공지 뭐있어",
        "gt_key": "next_week",
        "note": "다음 주 마감 전체 공지",
    },
    {
        "query": "국가장학금 마감일",
        "gt_key": "scholarship_next_week",
        "note": "국가장학금 키워드 — TF-IDF 유리",
    },
]


# --------------------------------------------------------------------------- #
# 검색 유틸
# --------------------------------------------------------------------------- #

def _recall_at_k(retrieved_ids: List[str], relevant_ids: Set[str]) -> float:
    if not relevant_ids:
        return float("nan")
    hits = sum(1 for cid in retrieved_ids if cid in relevant_ids)
    return hits / len(relevant_ids)


def _run_search(
    df: pd.DataFrame,
    vectorizer,
    matrix,
    tfidf_chunk_ids,
    query: str,
    top_k: int,
) -> List[str]:
    results = hybrid_search_with_meta(
        collection_name="dongguk_notices",
        chunks_df=df,
        tfidf_vectorizer=vectorizer,
        tfidf_matrix=matrix,
        query=query,
        top_k=top_k,
        alpha=HYBRID_ALPHA,
        where_filter=None,
        tfidf_chunk_ids=tfidf_chunk_ids,
    )
    return results["chunk_id"].astype(str).tolist()


# --------------------------------------------------------------------------- #
# 메인 측정 함수 (pytest + 직접 실행 모두 호환)
# --------------------------------------------------------------------------- #

def measure_deadline_recall(verbose: bool = True) -> pd.DataFrame:
    """Recall@K 측정을 실행하고 결과 DataFrame을 반환."""
    # 아티팩트 로드
    df = pd.read_parquet(_RAG_DIR / "artifacts" / "chunks" / "notices.parquet")
    vectorizer, matrix, tfidf_chunk_ids = load_tfidf_with_ids("notices")

    ground_truth = _build_ground_truth(df)

    top_ks = [5, 10, 20, 40]
    rows = []

    for q_info in DEADLINE_QUERIES:
        query = q_info["query"]
        gt_key = q_info["gt_key"]
        relevant = ground_truth[gt_key]

        row = {
            "query": query,
            "gt_key": gt_key,
            "gt_size": len(relevant),
        }
        for k in top_ks:
            retrieved = _run_search(df, vectorizer, matrix, tfidf_chunk_ids, query, k)
            recall = _recall_at_k(retrieved, relevant)
            row[f"recall@{k}"] = recall

            # 점수 분포 (top_5 기준)
            if k == 5:
                results_df = hybrid_search_with_meta(
                    collection_name="dongguk_notices",
                    chunks_df=df,
                    tfidf_vectorizer=vectorizer,
                    tfidf_matrix=matrix,
                    query=query,
                    top_k=k,
                    alpha=HYBRID_ALPHA,
                    where_filter=None,
                    tfidf_chunk_ids=tfidf_chunk_ids,
                )
                if not results_df.empty:
                    row["top1_score"] = round(float(results_df["hybrid_score"].iloc[0]), 4)
                    top_titles = results_df["title"].head(3).tolist()
                    top_deadlines = [_deadline_from_title(t) for t in top_titles]
                    row["top3_deadlines"] = str([str(d) if d else "N/A" for d in top_deadlines])
                else:
                    row["top1_score"] = float("nan")
                    row["top3_deadlines"] = "[]"

        rows.append(row)

    result_df = pd.DataFrame(rows)

    if verbose:
        _print_report(result_df, ground_truth)

    return result_df


def _print_report(df: pd.DataFrame, ground_truth: dict) -> None:
    print("\n" + "=" * 80)
    print("마감/날짜범위 질의 Recall@K 측정 결과")
    print(f"측정 기준일: {_TODAY}  |  HYBRID_ALPHA: {HYBRID_ALPHA}  |  DEFAULT_TOP_K: {DEFAULT_TOP_K}")
    print("=" * 80)

    print("\n[Ground-truth 집합 크기]")
    for key, ids in ground_truth.items():
        print(f"  {key:30s}: {len(ids):3d}개 청크")

    print("\n[Recall@K 결과표]")
    cols = ["query", "gt_key", "gt_size", "recall@5", "recall@10", "recall@20", "recall@40", "top1_score"]
    display = df[[c for c in cols if c in df.columns]].copy()
    for col in ["recall@5", "recall@10", "recall@20", "recall@40"]:
        if col in display.columns:
            display[col] = display[col].apply(lambda v: f"{v:.2f}" if not (v != v) else "N/A")
    print(display.to_string(index=False))

    print("\n[Top-3 검색 결과 마감일 (질의별)]")
    for _, row in df.iterrows():
        print(f"  Q: {row['query']}")
        print(f"     top3_deadlines: {row.get('top3_deadlines', 'N/A')}")

    # 요약 통계
    print("\n[요약]")
    for col in ["recall@5", "recall@10", "recall@20", "recall@40"]:
        if col in df.columns:
            vals = [v for v in df[col] if v == v]  # NaN 제거
            avg = sum(vals) / len(vals) if vals else float("nan")
            print(f"  평균 {col}: {avg:.3f}")

    print("=" * 80)


# --------------------------------------------------------------------------- #
# pytest 진입점
# --------------------------------------------------------------------------- #

def test_deadline_recall_baseline():
    """기본 recall@5 이 0.0 만 나오지 않는지 smoke 체크."""
    result = measure_deadline_recall(verbose=True)
    assert not result.empty, "결과 DataFrame이 비어 있음"
    # 장학금 질의 중 하나라도 recall@40 > 0 이어야 한다(기본 연기 검색 가능성 확인)
    scholarship_rows = result[result["gt_key"].str.contains("scholarship")]
    assert (scholarship_rows["recall@40"] > 0).any(), (
        "장학금 질의에서 recall@40=0: 아티팩트 또는 임베딩 문제 가능성"
    )


if __name__ == "__main__":
    measure_deadline_recall(verbose=True)
