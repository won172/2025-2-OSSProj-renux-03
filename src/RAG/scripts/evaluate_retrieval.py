"""검색 계층의 랭킹 품질을 Recall@K · MRR · nDCG · HitRate로 잰다.

**왜 필요한가.** 지금 있는 평가 지표는 `evaluate_rag.py`의 `route_hit_rate`,
`context_recall_proxy`, `context_precision_proxy`뿐이고 셋 다 **순위를 보지 않는다**.
"기대 키워드가 top-k 어딘가에 있는가"는 1위로 찾았는지 20위로 겨우 찾았는지를
구분하지 못한다. 그런데 생성 단계는 상위 몇 건만 쓰므로, 순위가 곧 답변 품질이다.

실제로 `config.py:83-90`에 기록된 사건이 이 공백의 증거다. 제목 가산을 끄니
"검색 계층 지표"는 61.7% → 63.0%로 **좋아졌는데** 골든 러너 전체는 86.05% →
85.07%로 나빠졌다. 앞 지표가 순위를 보지 않아 정답 문서가 밀려난 것을 놓친 것이다.
그 판단을 지표로 잡아내려면 순위 기반 지표가 있어야 한다.

**왜 검색 계층만 재는가.** 생성 단계를 태우면 LLM 비결정성이 검색 차이를 가리고,
문항당 5.5초와 토큰 비용이 든다(`compare_lexical_backends.py`가 같은 이유로 검색만
잰다). 임베딩·리랭커·메타필터·청킹 실험은 전부 검색 계층에서 결정되므로,
여기서 빠르고 결정적으로 비교한 뒤 통과한 것만 `run_golden_matrix.py`로 넘긴다.

**왜 데이터셋별로 채점하는가.** `_build_balanced_shortlist`의 설계 주석이 밝히듯
"서로 다른 코퍼스의 hybrid 점수는 의도적으로 직접 비교하지 않는다". 점수로 병합한
단일 순위를 만들면 그 규약을 어기고 지표가 코퍼스 편향을 측정하게 된다. 그래서
(질문 × 데이터셋) 단위로 채점하고 macro 평균한다. 융합 이후의 진짜 순위는
`--stage shortlist`가 잰다.

사용:
    # 기준선 기록 (tests/baselines/ 는 커밋한다 — artifacts/ 는 gitignore라 사라진다)
    python scripts/evaluate_retrieval.py --save tests/baselines/retrieval_baseline.json

    # 변경 후 비교
    python scripts/evaluate_retrieval.py --baseline tests/baselines/retrieval_baseline.json

    # 융합 이후 순위(리랭커 실험용)
    python scripts/evaluate_retrieval.py --stage shortlist
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.config as rag_config  # noqa: E402
import src.search.hybrid as hybrid  # noqa: E402
from src.pipelines.ingest import DATASET_ARTIFACTS  # noqa: E402
from src.services.retrieval_context import enrich_retrieval_fields  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
QRELS_PATH = ROOT / "tests" / "golden_qrels.csv"
MATRIX_PATH = ROOT / "tests" / "golden_matrix.csv"

# Recall/HitRate/nDCG를 끊어 볼 지점. 20은 데이터셋별 검색 반환 수
# (RAG_RETRIEVAL_TOP_K_PER_DATASET)와 같아 "검색이 애초에 건져 왔는가"를 잰다.
# 5는 근거로 실제 쓰이는 범위에 가깝다.
CUTOFFS = (5, 10, 20)
RELEVANT_MIN = 1  # relevance >= 1 이면 관련 문서로 센다(1=보조, 2=직접 답변)


# --------------------------------------------------------------------------- #
# 지표
# --------------------------------------------------------------------------- #
def dcg(gains: Sequence[float]) -> float:
    """표준 DCG. 순위 i(1부터)의 이득을 log2(i+1)로 나눈다."""
    return sum(gain / math.log2(index + 1) for index, gain in enumerate(gains, start=1))


def ndcg_at_k(ranked_relevance: Sequence[int], all_relevance: Sequence[int], k: int) -> float:
    """graded nDCG@k. 이득은 2^rel - 1 (관련도 2를 1보다 확실히 우대한다).

    IDCG는 **qrels 전체**의 관련도를 내림차순 정렬해 만든다. 검색된 것만으로
    이상적 순위를 만들면 아무것도 못 찾았을 때 0/0이 되어 1.0으로 보이는
    고전적 실수가 생긴다.
    """
    gains = [(2 ** rel) - 1 for rel in ranked_relevance[:k]]
    ideal = [(2 ** rel) - 1 for rel in sorted(all_relevance, reverse=True)[:k]]
    ideal_dcg = dcg(ideal)
    if ideal_dcg <= 0:
        return 0.0
    return dcg(gains) / ideal_dcg


def reciprocal_rank(ranked_relevance: Sequence[int]) -> float:
    """첫 관련 문서의 역순위. 없으면 0."""
    for index, rel in enumerate(ranked_relevance, start=1):
        if rel >= RELEVANT_MIN:
            return 1.0 / index
    return 0.0


def recall_at_k(ranked_relevance: Sequence[int], total_relevant: int, k: int) -> float:
    if total_relevant <= 0:
        return 0.0
    found = sum(1 for rel in ranked_relevance[:k] if rel >= RELEVANT_MIN)
    return found / total_relevant


def hit_at_k(ranked_relevance: Sequence[int], k: int) -> float:
    return 1.0 if any(rel >= RELEVANT_MIN for rel in ranked_relevance[:k]) else 0.0


@dataclass
class CaseScore:
    """한 (질문 × 데이터셋)의 채점 결과."""

    question_id: str
    dataset: str
    question: str
    total_relevant: int
    retrieved: int
    mrr: float
    first_relevant_rank: int | None
    recall: Dict[int, float] = field(default_factory=dict)
    hit: Dict[int, float] = field(default_factory=dict)
    ndcg: Dict[int, float] = field(default_factory=dict)
    # 상위 10건 중 qrels에 판정이 있는 비율. 낮으면 이 케이스의 점수를 믿을 수 없다.
    judged_at_10: float = 1.0


def score_case(
    *,
    question_id: str,
    dataset: str,
    question: str,
    ranked_ids: Sequence[str],
    relevance_by_id: Dict[str, int],
) -> CaseScore:
    """검색된 chunk_id 순서를 qrels와 대조해 채점한다.

    qrels에 없는 chunk_id는 관련도 0으로 본다(풀링 방식 평가의 표준 가정).
    풀이 얕으면 Recall이 과대평가되므로, 풀 깊이는 bootstrap_qrels.py가 관리한다.
    """
    ranked_relevance = [int(relevance_by_id.get(str(cid), 0)) for cid in ranked_ids]
    all_relevance = [rel for rel in relevance_by_id.values() if rel >= RELEVANT_MIN]
    total_relevant = len(all_relevance)

    first_rank: int | None = None
    for index, rel in enumerate(ranked_relevance, start=1):
        if rel >= RELEVANT_MIN:
            first_rank = index
            break

    # 판정 커버리지. 풀은 특정 검색 설정으로 만들어지므로, 설정을 바꾼 실험은
    # 풀 밖 문서를 뽑고 그건 전부 0점이 된다. 그 비율을 모르면 "새 설정이 나쁘다"와
    # "새 설정이 찾은 걸 아직 판정하지 않았다"를 구별할 수 없다.
    # 실제로 Kiwi 실험의 첫 측정이 recall@10 −0.097로 나왔는데, 풀을 양쪽
    # 합집합으로 다시 만들자 +0.0005로 뒤집혔다. 그때 이 값이 75.9%였다.
    top_ten = [str(cid) for cid in ranked_ids[:10]]
    judged_at_10 = (
        sum(1 for cid in top_ten if cid in relevance_by_id) / len(top_ten)
        if top_ten
        else 1.0
    )

    return CaseScore(
        question_id=question_id,
        dataset=dataset,
        question=question,
        total_relevant=total_relevant,
        retrieved=len(ranked_ids),
        mrr=reciprocal_rank(ranked_relevance),
        first_relevant_rank=first_rank,
        recall={k: recall_at_k(ranked_relevance, total_relevant, k) for k in CUTOFFS},
        hit={k: hit_at_k(ranked_relevance, k) for k in CUTOFFS},
        ndcg={k: ndcg_at_k(ranked_relevance, all_relevance, k) for k in CUTOFFS},
        judged_at_10=judged_at_10,
    )


def aggregate(cases: Sequence[CaseScore]) -> dict:
    """macro 평균. 문항마다 정답 수가 달라도 각 문항이 같은 무게를 갖는다."""
    if not cases:
        return {}
    count = len(cases)
    summary: dict = {"cases": count}
    summary["mrr"] = sum(case.mrr for case in cases) / count
    summary["judged@10"] = sum(case.judged_at_10 for case in cases) / count
    # 후보 수는 비용이다. shortlist를 넓히면 evidence selector 프롬프트가 그만큼
    # 길어지므로, 재현율 이득과 함께 봐야 판단할 수 있다.
    summary["mean_candidates"] = sum(case.retrieved for case in cases) / count
    for k in CUTOFFS:
        summary[f"recall@{k}"] = sum(case.recall[k] for case in cases) / count
        summary[f"hit@{k}"] = sum(case.hit[k] for case in cases) / count
        summary[f"ndcg@{k}"] = sum(case.ndcg[k] for case in cases) / count
    return summary


# --------------------------------------------------------------------------- #
# qrels / 골든 로딩
# --------------------------------------------------------------------------- #
def load_qrels(
    path: Path,
    *,
    label_sources: Sequence[str] | None = None,
) -> Dict[tuple[str, str], Dict[str, int]]:
    """(question_id, dataset) → {chunk_id: relevance} 로 읽는다.

    ``label_sources``를 주면 그 출처의 라벨만 정답으로 인정하고 나머지는 0으로
    낮춘다. 파일을 복제하지 않고 라벨링 방식을 갈아끼우기 위한 것이다 —
    예를 들어 과거 근거만 믿고 싶으면 ``["auto_log", "human"]``을 준다.
    후보 자체는 그대로 남으므로 풀 깊이는 바뀌지 않는다.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"qrels 파일이 없습니다: {path}\n"
            "scripts/bootstrap_qrels.py 로 후보를 풀링한 뒤 라벨링하세요."
        )
    frame = pd.read_csv(path, dtype=str).fillna("")
    required = {"question_id", "dataset", "chunk_id", "relevance"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"qrels에 필요한 컬럼이 없습니다: {sorted(missing)}")

    allowed = set(label_sources) if label_sources else None
    qrels: Dict[tuple[str, str], Dict[str, int]] = defaultdict(dict)
    for _, row in frame.iterrows():
        chunk_id = str(row["chunk_id"]).strip()
        if not chunk_id:
            continue
        try:
            relevance = int(float(row["relevance"]))
        except (TypeError, ValueError):
            continue
        if allowed is not None and str(row.get("label_source", "")).strip() not in allowed:
            relevance = 0
        key = (str(row["question_id"]).strip(), str(row["dataset"]).strip())
        qrels[key][chunk_id] = relevance
    return dict(qrels)


def load_questions(path: Path) -> Dict[str, dict]:
    """골든 매트릭스에서 id → {question, expected_datasets} 를 읽는다."""
    frame = pd.read_csv(path, dtype=str).fillna("")
    questions: Dict[str, dict] = {}
    for _, row in frame.iterrows():
        question_id = str(row.get("id", "")).strip()
        if not question_id:
            continue
        datasets = [
            value.strip()
            for value in str(row.get("expected_datasets", "")).split(";")
            if value.strip()
        ]
        questions[question_id] = {
            "question": str(row.get("question", "")).strip(),
            "domain": str(row.get("domain", "")).strip(),
            "case_type": str(row.get("case_type", "")).strip(),
            "expected_datasets": datasets,
        }
    return questions


# --------------------------------------------------------------------------- #
# 검색 실행
# --------------------------------------------------------------------------- #
_FRAME_CACHE: Dict[str, pd.DataFrame] = {}
_LEXICAL_CACHE: Dict[str, dict] = {}


def _frame(dataset: str) -> pd.DataFrame:
    if dataset not in _FRAME_CACHE:
        path = DATASET_ARTIFACTS[dataset].chunk_path
        frame = pd.read_parquet(path) if path.exists() else pd.DataFrame()
        _FRAME_CACHE[dataset] = enrich_retrieval_fields(frame) if not frame.empty else frame
    return _FRAME_CACHE[dataset]


def _lexical(dataset: str) -> dict:
    if dataset not in _LEXICAL_CACHE:
        _LEXICAL_CACHE[dataset] = hybrid._load_lexical_artifact(dataset)
    return _LEXICAL_CACHE[dataset]


def retrieve_tier(dataset: str, question: str, top_k: int) -> List[str]:
    """검색 계층만 실행해 chunk_id 순위를 돌려준다(융합·리랭킹 이전)."""
    frame = _frame(dataset)
    if frame.empty:
        return []
    data = _lexical(dataset)
    hits = hybrid.hybrid_search(
        DATASET_ARTIFACTS[dataset].collection,
        frame,
        data["vectorizer"],
        data["matrix"],
        question,
        top_k=top_k,
        tfidf_chunk_ids=data["chunk_ids"],
    )
    if hits.empty:
        return []
    return hits["chunk_id"].astype(str).tolist()


def retrieve_shortlist(datasets: Sequence[str], question: str, top_k: int) -> Dict[str, List[str]]:
    """융합·recency·쿼터까지 적용된 최종 후보 순위를 데이터셋별로 돌려준다.

    `api/rag_service`를 지연 임포트한다. 이 모듈은 FastAPI 앱과 DB 세션을 만들어
    무겁고, 기본 경로(검색 계층)에서는 필요 없다.
    """
    from api.rag_service import _build_balanced_shortlist  # noqa: PLC0415

    frames: List[pd.DataFrame] = []
    for dataset in datasets:
        frame = _frame(dataset)
        if frame.empty:
            continue
        data = _lexical(dataset)
        hits = hybrid.hybrid_search_with_meta(
            DATASET_ARTIFACTS[dataset].collection,
            frame,
            data["vectorizer"],
            data["matrix"],
            question,
            top_k=top_k,
            tfidf_chunk_ids=data["chunk_ids"],
        )
        if hits.empty:
            continue
        hits = hits.copy()
        hits["dataset"] = dataset
        frames.append(hits)

    if not frames:
        return {}
    shortlist = _build_balanced_shortlist(frames, query=question)
    if shortlist.empty:
        return {}
    ranked: Dict[str, List[str]] = defaultdict(list)
    for _, row in shortlist.iterrows():
        ranked[str(row.get("dataset"))].append(str(row.get("chunk_id")))
    return dict(ranked)


# --------------------------------------------------------------------------- #
# 실행 · 보고
# --------------------------------------------------------------------------- #
def run(
    *,
    qrels: Dict[tuple[str, str], Dict[str, int]],
    questions: Dict[str, dict],
    stage: str,
    top_k: int,
) -> tuple[List[CaseScore], dict]:
    cases: List[CaseScore] = []
    timings: List[float] = []

    by_question: Dict[str, List[str]] = defaultdict(list)
    for question_id, dataset in qrels:
        by_question[question_id].append(dataset)

    for question_id in sorted(by_question):
        meta = questions.get(question_id)
        if meta is None or not meta["question"]:
            print(f"  ! {question_id}: 골든 매트릭스에 없는 question_id — 건너뜁니다.")
            continue
        datasets = sorted(by_question[question_id])
        question = meta["question"]

        started_at = time.perf_counter()
        if stage == "shortlist":
            ranked_by_dataset = retrieve_shortlist(datasets, question, top_k)
        else:
            ranked_by_dataset = {
                dataset: retrieve_tier(dataset, question, top_k) for dataset in datasets
            }
        timings.append((time.perf_counter() - started_at) * 1000)

        for dataset in datasets:
            relevance_by_id = qrels[(question_id, dataset)]
            if not any(rel >= RELEVANT_MIN for rel in relevance_by_id.values()):
                # 판정된 정답이 하나도 없는 (질문, 데이터셋)은 채점 대상이 아니다.
                # 존재하지 않는 정보(nonexistent_info) 케이스가 여기 해당한다.
                continue
            cases.append(
                score_case(
                    question_id=question_id,
                    dataset=dataset,
                    question=question,
                    ranked_ids=ranked_by_dataset.get(dataset, []),
                    relevance_by_id=relevance_by_id,
                )
            )

    latency = {}
    if timings:
        ordered = sorted(timings)
        latency = {
            "p50_ms": ordered[len(ordered) // 2],
            "p90_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))],
            "mean_ms": sum(ordered) / len(ordered),
        }
    return cases, latency


def build_snapshot(cases: Sequence[CaseScore], latency: dict, config: dict) -> dict:
    by_dataset: Dict[str, List[CaseScore]] = defaultdict(list)
    for case in cases:
        by_dataset[case.dataset].append(case)
    return {
        "config": config,
        "overall": aggregate(cases),
        "by_dataset": {name: aggregate(group) for name, group in sorted(by_dataset.items())},
        "retrieval_latency": latency,
        "cases": [
            {
                "question_id": case.question_id,
                "dataset": case.dataset,
                "mrr": case.mrr,
                "first_relevant_rank": case.first_relevant_rank,
                "total_relevant": case.total_relevant,
                **{f"recall@{k}": case.recall[k] for k in CUTOFFS},
                **{f"ndcg@{k}": case.ndcg[k] for k in CUTOFFS},
            }
            for case in cases
        ],
    }


_METRIC_ORDER = ["mrr"] + [f"{name}@{k}" for k in CUTOFFS for name in ("recall", "hit", "ndcg")]
# 상위 10건의 판정 커버리지가 이 아래로 떨어지면 비교 결과를 신뢰하지 않는다.
JUDGED_COVERAGE_FLOOR = 0.90


def print_report(snapshot: dict, baseline: dict | None) -> None:
    overall = snapshot["overall"]
    if not overall:
        print("채점 대상이 없습니다. qrels에 relevance>=1 인 행이 있는지 확인하세요.")
        return

    base_overall = (baseline or {}).get("overall") or {}
    print()
    print("=" * 74)
    print(f"검색 랭킹 평가 · {overall['cases']}건 (질문×데이터셋) · stage={snapshot['config']['stage']}")
    print("=" * 74)
    header = f"  {'metric':<12} {'value':>8}"
    if base_overall:
        header += f" {'baseline':>10} {'delta':>9}"
    print(header)
    for metric in _METRIC_ORDER:
        if metric not in overall:
            continue
        line = f"  {metric:<12} {overall[metric]:>8.4f}"
        if base_overall and metric in base_overall:
            delta = overall[metric] - base_overall[metric]
            mark = "+" if delta > 0 else ""
            line += f" {base_overall[metric]:>10.4f} {mark}{delta:>8.4f}"
        print(line)

    judged = overall.get("judged@10")
    if judged is not None:
        print(f"\n  판정 커버리지 top-10  {judged:.1%}")
        if judged < JUDGED_COVERAGE_FLOOR:
            print(
                f"  ⚠️  상위 결과의 {1 - judged:.1%}가 qrels에 판정이 없어 전부 0점으로 채점됐다.\n"
                "      풀이 지금 설정으로 만들어지지 않았다는 뜻이고, 이 상태의 비교는\n"
                "      기존 설정에 유리하게 기운다. 먼저 풀을 넓힌 뒤 다시 재라:\n"
                "        python scripts/bootstrap_qrels.py --autolabel log+keywords\n"
                "      (풀을 넓히면 분모가 바뀌므로 기준선도 다시 저장해야 한다)"
            )

    latency = snapshot.get("retrieval_latency") or {}
    if latency:
        print(f"\n  검색 지연  p50 {latency['p50_ms']:.0f}ms · p90 {latency['p90_ms']:.0f}ms")

    print(f"\n  {'dataset':<10} {'cases':>6} {'recall@10':>10} {'mrr':>8} {'ndcg@10':>9}")
    for dataset, metrics in snapshot["by_dataset"].items():
        print(
            f"  {dataset:<10} {metrics['cases']:>6} {metrics['recall@10']:>10.4f} "
            f"{metrics['mrr']:>8.4f} {metrics['ndcg@10']:>9.4f}"
        )

    if baseline:
        _print_regressions(snapshot, baseline)


def _print_regressions(snapshot: dict, baseline: dict) -> None:
    """문항 단위로 개선·악화를 센다. 평균만 보면 상쇄되어 보이지 않는다."""
    base_cases = {
        (case["question_id"], case["dataset"]): case for case in baseline.get("cases", [])
    }
    improved: List[tuple[str, str, float, float]] = []
    regressed: List[tuple[str, str, float, float]] = []
    for case in snapshot.get("cases", []):
        previous = base_cases.get((case["question_id"], case["dataset"]))
        if previous is None:
            continue
        before, after = previous["mrr"], case["mrr"]
        if after > before + 1e-9:
            improved.append((case["question_id"], case["dataset"], before, after))
        elif after < before - 1e-9:
            regressed.append((case["question_id"], case["dataset"], before, after))

    print(f"\n  문항 단위 변화: 개선 {len(improved)} · 악화 {len(regressed)}")
    for question_id, dataset, before, after in sorted(regressed, key=lambda item: item[3] - item[2])[:10]:
        print(f"    악화  {question_id:<8} {dataset:<9} MRR {before:.3f} → {after:.3f}")
    for question_id, dataset, before, after in sorted(improved, key=lambda item: item[2] - item[3])[:5]:
        print(f"    개선  {question_id:<8} {dataset:<9} MRR {before:.3f} → {after:.3f}")


def current_config(stage: str, top_k: int) -> dict:
    """지표와 함께 저장할 설정 지문. 무엇을 바꿔서 숫자가 달라졌는지 남긴다."""
    return {
        "stage": stage,
        "top_k": top_k,
        "embed_model": rag_config.EMBED_MODEL_NAME,
        "lexical_backend": rag_config.LEXICAL_BACKEND,
        "tokenizer": rag_config.TFIDF_TOKENIZER,
        "tokenizer_backend": _tokenizer_backend(),
        "fusion_mode": rag_config.HYBRID_FUSION_MODE,
        "rrf_k": rag_config.HYBRID_RRF_K,
        "title_focus_weight": rag_config.HYBRID_TITLE_FOCUS_WEIGHT,
        "recency_weight": rag_config.RECENCY_WEIGHT,
        "reranker_enabled": rag_config.RERANKER_ENABLED,
        "reranker_model": rag_config.RERANKER_MODEL if rag_config.RERANKER_ENABLED else None,
        "chunk_size": rag_config.CHUNK_SIZE,
        # shortlist 단계에서만 의미가 있지만 항상 기록한다. 이 두 값이 검색이
        # 찾아온 근거 중 무엇이 LLM에 도달하는지를 결정한다.
        "evidence_candidates_per_dataset": rag_config.RAG_EVIDENCE_CANDIDATES_PER_DATASET,
        "evidence_max_candidates": rag_config.RAG_EVIDENCE_MAX_CANDIDATES,
    }


def _tokenizer_backend() -> str:
    """설정된 이름이 아니라 실제로 무엇이 도는지 기록한다.

    `TFIDF_TOKENIZER=korean`이어도 kiwipiepy가 없으면 경량 폴백이 돈다. 실제
    인덱스가 그렇게 만들어져 있었고, 설정만 보면 알 수 없었다.
    """
    if rag_config.TFIDF_TOKENIZER != "korean":
        return rag_config.TFIDF_TOKENIZER
    return "kiwi" if hybrid._load_kiwi() is not None else "light_korean"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--qrels", type=Path, default=QRELS_PATH)
    parser.add_argument("--matrix", type=Path, default=MATRIX_PATH)
    parser.add_argument(
        "--stage",
        choices=("retrieval", "shortlist"),
        default="retrieval",
        help="retrieval=하이브리드 검색만, shortlist=융합·recency·쿼터까지",
    )
    parser.add_argument("--top-k", type=int, default=max(CUTOFFS))
    parser.add_argument("--save", type=Path, help="결과 스냅샷을 JSON으로 저장")
    parser.add_argument("--baseline", type=Path, help="비교할 기준선 스냅샷 JSON")
    parser.add_argument(
        "--label-sources",
        nargs="+",
        metavar="SOURCE",
        help=(
            "이 출처의 라벨만 정답으로 인정한다(나머지는 0). "
            "예: --label-sources auto_log human → 과거 근거만 믿는 모드"
        ),
    )
    args = parser.parse_args()

    qrels = load_qrels(args.qrels, label_sources=args.label_sources)
    questions = load_questions(args.matrix)
    if not qrels:
        print("qrels가 비어 있습니다.")
        return 1

    baseline = None
    if args.baseline:
        if not args.baseline.exists():
            print(f"기준선 파일이 없습니다: {args.baseline}")
            return 1
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))

    config = current_config(args.stage, args.top_k)
    if args.label_sources:
        config["label_sources"] = sorted(args.label_sources)
    print(f"설정: {json.dumps(config, ensure_ascii=False)}")
    if baseline and baseline.get("config") != config:
        print("  ※ 기준선과 설정이 다릅니다(의도한 변경인지 확인하세요):")
        for key, value in config.items():
            previous = baseline.get("config", {}).get(key)
            if previous != value:
                print(f"     {key}: {previous!r} → {value!r}")

    cases, latency = run(qrels=qrels, questions=questions, stage=args.stage, top_k=args.top_k)
    snapshot = build_snapshot(cases, latency, config)
    print_report(snapshot, baseline)

    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n스냅샷 저장: {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
