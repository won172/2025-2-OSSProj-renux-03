"""랭킹 지표 계산이 실제로 맞는지 손계산과 대조한다.

지표 코드는 조용히 틀리기 쉽다. 특히 nDCG의 IDCG를 **검색된 문서**로 만들면
아무것도 못 찾은 질문이 0/0 → 1.0으로 보여서, 검색이 망가질수록 점수가 좋아진다.
이 파일은 그런 종류의 결함을 회귀로 잡는다.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.evaluate_retrieval import (  # noqa: E402
    aggregate,
    dcg,
    hit_at_k,
    load_qrels,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    score_case,
)


# --------------------------------------------------------------------------- #
# 개별 지표
# --------------------------------------------------------------------------- #
def test_dcg_uses_log2_rank_plus_one():
    # 1위는 log2(2)=1로 나누므로 이득이 그대로, 2위는 log2(3)으로 나뉜다.
    assert dcg([3.0]) == pytest.approx(3.0)
    assert dcg([3.0, 1.0]) == pytest.approx(3.0 + 1.0 / math.log2(3))


def test_reciprocal_rank_finds_first_relevant():
    assert reciprocal_rank([0, 0, 1]) == pytest.approx(1 / 3)
    assert reciprocal_rank([2, 1]) == pytest.approx(1.0)
    assert reciprocal_rank([0, 0, 0]) == 0.0
    assert reciprocal_rank([]) == 0.0


def test_recall_counts_only_within_cutoff():
    ranked = [1, 0, 0, 1, 0]
    assert recall_at_k(ranked, total_relevant=3, k=5) == pytest.approx(2 / 3)
    assert recall_at_k(ranked, total_relevant=3, k=2) == pytest.approx(1 / 3)
    assert recall_at_k(ranked, total_relevant=3, k=1) == pytest.approx(1 / 3)


def test_recall_is_zero_when_nothing_is_relevant():
    """0으로 나누지 않는다. 정답이 없는 질문은 채점 대상에서 빠지지만,
    지표 함수 자체가 터지면 상위 집계가 통째로 죽는다."""
    assert recall_at_k([0, 0], total_relevant=0, k=5) == 0.0


def test_hit_rate_is_binary_within_cutoff():
    assert hit_at_k([0, 0, 1], k=2) == 0.0
    assert hit_at_k([0, 0, 1], k=3) == 1.0


def test_ndcg_matches_hand_computation():
    # ranked=[2,0,1] → gains [3,0,1] → DCG = 3/1 + 0 + 1/log2(4) = 3.5
    # 정답 전체 [2,1] → ideal [3,1] → IDCG = 3 + 1/log2(3)
    ranked_relevance = [2, 0, 1]
    all_relevance = [2, 1]
    expected = 3.5 / (3.0 + 1.0 / math.log2(3))
    assert ndcg_at_k(ranked_relevance, all_relevance, 3) == pytest.approx(expected)


def test_ndcg_is_one_for_a_perfect_ranking():
    assert ndcg_at_k([2, 1], [2, 1], 10) == pytest.approx(1.0)


def test_ndcg_penalises_relevance_ordering():
    """관련도 2를 1보다 아래에 놓으면 점수가 내려가야 한다."""
    good = ndcg_at_k([2, 1], [2, 1], 10)
    bad = ndcg_at_k([1, 2], [2, 1], 10)
    assert bad < good


def test_ndcg_is_zero_when_retrieval_returns_nothing():
    """IDCG를 검색 결과로 만들면 0/0이 1.0으로 보이는 고전적 결함.

    정답은 있는데 하나도 못 찾은 상태다. 반드시 0이어야 한다.
    """
    assert ndcg_at_k([], [2, 1], 10) == 0.0
    assert ndcg_at_k([0, 0, 0], [2, 1], 10) == 0.0


def test_ndcg_is_zero_when_no_relevant_document_exists():
    assert ndcg_at_k([0, 0], [], 10) == 0.0


# --------------------------------------------------------------------------- #
# 케이스 채점
# --------------------------------------------------------------------------- #
def test_score_case_treats_unlabelled_chunks_as_irrelevant():
    """풀에 없는 chunk_id는 관련도 0으로 본다(풀링 평가의 표준 가정)."""
    case = score_case(
        question_id="AC-001",
        dataset="schedule",
        question="개강일이 언제야?",
        ranked_ids=["unknown-a", "gold-1", "unknown-b"],
        relevance_by_id={"gold-1": 2, "gold-2": 1, "noise-1": 0},
    )
    assert case.total_relevant == 2  # gold-1, gold-2 (noise-1은 0이라 제외)
    assert case.first_relevant_rank == 2
    assert case.mrr == pytest.approx(0.5)
    assert case.recall[5] == pytest.approx(0.5)
    assert case.hit[5] == 1.0


def test_score_case_handles_total_miss():
    case = score_case(
        question_id="SC-004",
        dataset="notices",
        question="장학금 신청 기간",
        ranked_ids=["noise-1", "noise-2"],
        relevance_by_id={"gold-1": 2},
    )
    assert case.mrr == 0.0
    assert case.first_relevant_rank is None
    assert case.recall[5] == 0.0
    assert case.hit[5] == 0.0
    assert case.ndcg[5] == 0.0


def test_score_case_handles_empty_retrieval():
    case = score_case(
        question_id="DN-002",
        dataset="meals",
        question="오늘 학식",
        ranked_ids=[],
        relevance_by_id={"gold-1": 2},
    )
    assert case.retrieved == 0
    assert case.mrr == 0.0
    assert case.ndcg[10] == 0.0


def test_recall_at_20_can_exceed_recall_at_5():
    """컷오프가 커지면 Recall은 단조 증가해야 한다."""
    case = score_case(
        question_id="RG-003",
        dataset="rules",
        question="수강신청 취소",
        ranked_ids=[f"noise-{i}" for i in range(7)] + ["gold-1", "gold-2"],
        relevance_by_id={"gold-1": 2, "gold-2": 1},
    )
    assert case.recall[5] == 0.0
    assert case.recall[10] == pytest.approx(1.0)
    assert case.recall[5] <= case.recall[10] <= case.recall[20]


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #
def test_aggregate_is_macro_averaged_over_cases():
    """정답이 많은 문항이 평균을 지배하지 않아야 한다(문항당 동일 가중)."""
    perfect = score_case(
        question_id="A-1",
        dataset="notices",
        question="q1",
        ranked_ids=["g1"],
        relevance_by_id={"g1": 2},
    )
    missed = score_case(
        question_id="A-2",
        dataset="notices",
        question="q2",
        ranked_ids=["noise"],
        relevance_by_id={"g1": 2, "g2": 2, "g3": 2},
    )
    summary = aggregate([perfect, missed])
    assert summary["cases"] == 2
    assert summary["mrr"] == pytest.approx(0.5)
    assert summary["recall@10"] == pytest.approx(0.5)
    assert summary["hit@10"] == pytest.approx(0.5)


def test_aggregate_of_nothing_is_empty():
    assert aggregate([]) == {}


# --------------------------------------------------------------------------- #
# qrels 로딩
# --------------------------------------------------------------------------- #
def test_load_qrels_groups_by_question_and_dataset(tmp_path: Path):
    path = tmp_path / "qrels.csv"
    path.write_text(
        "question_id,question,dataset,chunk_id,relevance,label_source,note\n"
        "AC-001,개강일,schedule,c1,2,log_evidence,\n"
        "AC-001,개강일,schedule,c2,0,pool_judge,\n"
        "AC-001,개강일,notices,c3,1,human,\n",
        encoding="utf-8",
    )
    qrels = load_qrels(path)
    assert qrels[("AC-001", "schedule")] == {"c1": 2, "c2": 0}
    assert qrels[("AC-001", "notices")] == {"c3": 1}


def test_load_qrels_rejects_a_file_missing_required_columns(tmp_path: Path):
    path = tmp_path / "bad.csv"
    path.write_text("question_id,chunk_id\nAC-001,c1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="relevance"):
        load_qrels(path)


def test_load_qrels_reports_a_missing_file_with_the_fix(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="bootstrap_qrels"):
        load_qrels(tmp_path / "nope.csv")


def test_load_qrels_can_restrict_which_label_sources_count(tmp_path: Path):
    """라벨링 방식을 갈아끼울 때 파일을 복제하지 않기 위한 것.

    후보 행 자체는 남아야 한다 — 풀 깊이가 줄면 Recall 분모가 달라져
    두 방식을 비교할 수 없다. 인정하지 않는 출처는 0으로 낮추기만 한다.
    """
    path = tmp_path / "qrels.csv"
    path.write_text(
        "question_id,question,dataset,chunk_id,relevance,label_source,note\n"
        "AC-001,개강일,schedule,c1,2,auto_log,\n"
        "AC-001,개강일,schedule,c2,2,auto_keyword_full,\n"
        "AC-001,개강일,schedule,c3,0,auto_zero,\n",
        encoding="utf-8",
    )
    everything = load_qrels(path)
    assert everything[("AC-001", "schedule")] == {"c1": 2, "c2": 2, "c3": 0}

    log_only = load_qrels(path, label_sources=["auto_log", "human"])
    assert log_only[("AC-001", "schedule")] == {"c1": 2, "c2": 0, "c3": 0}


def test_score_case_reports_how_much_of_the_top_ten_was_judged():
    """풀 밖 문서 비율을 드러내지 않으면 편향된 비교를 알아챌 수 없다.

    Kiwi 실험에서 실제로 일어난 일이다. 첫 측정은 recall@10 −0.097로 "명백히
    나쁨"이었는데, 풀이 이전 토크나이저로 만들어져 있었을 뿐이었다. 양쪽
    합집합으로 풀을 다시 만드니 +0.0005로 뒤집혔다. 그때 이 값이 75.9%였다.
    """
    case = score_case(
        question_id="AC-001",
        dataset="schedule",
        question="개강일",
        ranked_ids=["judged-1", "unjudged-a", "unjudged-b", "judged-2"],
        relevance_by_id={"judged-1": 2, "judged-2": 0},
    )
    assert case.judged_at_10 == pytest.approx(0.5)


def test_fully_pooled_retrieval_reports_complete_coverage():
    case = score_case(
        question_id="AC-001",
        dataset="schedule",
        question="개강일",
        ranked_ids=["a", "b"],
        relevance_by_id={"a": 2, "b": 0},
    )
    assert case.judged_at_10 == pytest.approx(1.0)


def test_aggregate_carries_the_coverage_signal():
    complete = score_case(
        question_id="A-1", dataset="notices", question="q",
        ranked_ids=["a"], relevance_by_id={"a": 2},
    )
    blind = score_case(
        question_id="A-2", dataset="notices", question="q",
        ranked_ids=["x", "y"], relevance_by_id={"a": 2},
    )
    assert aggregate([complete, blind])["judged@10"] == pytest.approx(0.5)
