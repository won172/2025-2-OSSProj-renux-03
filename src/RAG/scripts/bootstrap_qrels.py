"""골든 문항의 정답 후보를 풀링해 라벨링 워크시트(`tests/golden_qrels.csv`)를 만든다.

**왜 풀링인가.** Recall을 재려면 "이 질문의 정답 문서가 무엇인지"를 알아야 하는데,
32,508개 청크를 문항마다 다 볼 수는 없다. TREC이 쓰는 방법은 **여러 검색 설정으로
상위 후보를 모아(pool) 그것만 판정**하고, 풀 밖은 관련 없음으로 간주하는 것이다.
풀이 다양할수록 이 가정이 안전해지므로, 한 설정이 아니라 네 가지로 뽑는다.

  dense    밀집 임베딩만 — 어휘가 달라도 의미가 같은 문서
  sparse   BM25만 — 정확한 어휘·날짜·제도명이 일치하는 문서
  rrf      현행 융합
  weighted α=0.4 가중합 — 융합 방식이 다르면 다른 문서가 올라온다

**로그를 씨앗으로 쓰는 이유.** `rag_query_logs`의 2,503건에 골든 168문항이 **전부**
들어 있고, 그중 154문항은 `rag_retrieval_logs`에 당시 근거로 선택된 문서가 남아 있다.
이미 evidence selector와 grounding 검사를 통과한 문서들이라 정답 후보로서 질이 높다.
다만 **자동 정답으로 승격하지는 않는다** — 답변의 20.6%가 grounding 0점이었으므로
선택된 근거가 곧 정답이라는 보장이 없다. 등급 제안만 하고 판정은 사람이 한다.

**기존 라벨은 절대 덮어쓰지 않는다.** 다시 돌리면 새 후보만 빈 칸으로 추가된다.
검색 설정을 바꿔가며 풀을 넓히는 것이 정상 사용법이고, 그때마다 사람 판정이
날아가면 이 도구는 한 번밖에 못 쓴다.

사용:
    python scripts/bootstrap_qrels.py                 # 풀링 + 워크시트 갱신
    python scripts/bootstrap_qrels.py --depth 15      # 설정당 상위 15개까지
    python scripts/bootstrap_qrels.py --report        # 라벨링 진행률만 확인
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Sequence

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.search.hybrid as hybrid  # noqa: E402
from src.models.embedding import encode_queries  # noqa: E402
from src.pipelines.ingest import DATASET_ARTIFACTS  # noqa: E402
from src.services.retrieval_context import enrich_retrieval_fields  # noqa: E402
from src.vectorstore.chroma_client import get_collection, query_items  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "tests" / "golden_matrix.csv"
QRELS_PATH = ROOT / "tests" / "golden_qrels.csv"
DB_PATH = ROOT / "rag_database.db"

COLUMNS = (
    "question_id",
    "question",
    "domain",
    "case_type",
    "dataset",
    "chunk_id",
    "relevance",
    "label_source",
    "pooled_by",
    "best_rank",
    "title",
    "published_at",
    "snippet",
    "note",
)
SNIPPET_CHARS = 180


# --------------------------------------------------------------------------- #
# 데이터 로딩
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


def load_matrix(path: Path) -> List[dict]:
    frame = pd.read_csv(path, dtype=str).fillna("")
    cases: List[dict] = []
    for _, row in frame.iterrows():
        question_id = str(row.get("id", "")).strip()
        question = str(row.get("question", "")).strip()
        if not question_id or not question:
            continue
        datasets = [
            value.strip()
            for value in str(row.get("expected_datasets", "")).split(";")
            if value.strip() in DATASET_ARTIFACTS
        ]
        cases.append(
            {
                "question_id": question_id,
                "question": question,
                "domain": str(row.get("domain", "")).strip(),
                "case_type": str(row.get("case_type", "")).strip(),
                "datasets": datasets,
                "answerability": str(row.get("answerability", "")).strip(),
                "required_keywords": str(row.get("required_keywords", "")).strip(),
            }
        )
    return cases


# --------------------------------------------------------------------------- #
# 풀 구성원 1 — 과거 근거 로그
# --------------------------------------------------------------------------- #
def load_logged_evidence(db_path: Path) -> Dict[str, Dict[str, dict]]:
    """질문 원문 → {chunk_id: {dataset, rank, grounded}} 로 과거 근거를 읽는다.

    grounding에 통과한 답변의 근거만 등급을 제안한다. 실패한 답변의 근거는
    후보로만 넣는다 — 실제로 그때 근거가 부실했다는 뜻이기 때문이다.
    """
    if not db_path.exists():
        return {}
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT ql.question,
                   rl.dataset,
                   rl.chunk_id,
                   rl.rank,
                   ql.fallback_triggered,
                   ql.grounding_checked,
                   ql.grounding_grounded
            FROM rag_retrieval_logs rl
            JOIN rag_query_logs ql ON ql.id = rl.query_log_id
            WHERE rl.chunk_id IS NOT NULL AND rl.chunk_id <> ''
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        connection.close()

    evidence: Dict[str, Dict[str, dict]] = defaultdict(dict)
    for row in rows:
        question = str(row["question"] or "").strip()
        chunk_id = str(row["chunk_id"]).strip()
        if not question or not chunk_id:
            continue
        # grounding을 켜고 통과했고 폴백도 아니었던 답변의 근거인가.
        trusted = bool(
            not row["fallback_triggered"]
            and row["grounding_checked"]
            and row["grounding_grounded"]
        )
        existing = evidence[question].get(chunk_id)
        rank = int(row["rank"] or 99)
        if existing is None or rank < existing["rank"] or (trusted and not existing["trusted"]):
            evidence[question][chunk_id] = {
                "dataset": str(row["dataset"] or "").strip(),
                "rank": rank,
                "trusted": trusted or bool(existing and existing["trusted"]),
            }
    return dict(evidence)


# --------------------------------------------------------------------------- #
# 풀 구성원 2 — 여러 설정의 검색 결과
# --------------------------------------------------------------------------- #
def _dense_ids(dataset: str, question: str, depth: int) -> List[str]:
    try:
        collection = get_collection(DATASET_ARTIFACTS[dataset].collection)
        result = query_items(
            DATASET_ARTIFACTS[dataset].collection,
            collection=collection,
            query_embeddings=encode_queries([question]),
            n_results=depth,
        )
    except Exception as exc:  # noqa: BLE001 - 풀링은 한 설정이 죽어도 계속돼야 한다
        print(f"    ! dense 검색 실패({dataset}): {exc}")
        return []
    return [str(cid) for cid in (result.get("ids") or [[]])[0]]


def _sparse_ids(dataset: str, question: str, depth: int) -> List[str]:
    frame = _frame(dataset)
    if frame.empty:
        return []
    data = _lexical(dataset)
    chunk_ids = data.get("chunk_ids")
    if not chunk_ids:
        return []
    try:
        scores = hybrid.score_lexical_query(data["vectorizer"], data["matrix"], question)
    except Exception as exc:  # noqa: BLE001
        print(f"    ! sparse 검색 실패({dataset}): {exc}")
        return []
    order = np.argsort(scores)[::-1][:depth]
    return [str(chunk_ids[index]) for index in order if scores[index] > 0]


def _fused_ids(dataset: str, question: str, depth: int, mode: str) -> List[str]:
    frame = _frame(dataset)
    if frame.empty:
        return []
    data = _lexical(dataset)
    previous = hybrid.HYBRID_FUSION_MODE
    hybrid.HYBRID_FUSION_MODE = mode
    try:
        hits = hybrid.hybrid_search(
            DATASET_ARTIFACTS[dataset].collection,
            frame,
            data["vectorizer"],
            data["matrix"],
            question,
            top_k=depth,
            tfidf_chunk_ids=data["chunk_ids"],
        )
    except Exception as exc:  # noqa: BLE001
        print(f"    ! {mode} 검색 실패({dataset}): {exc}")
        return []
    finally:
        hybrid.HYBRID_FUSION_MODE = previous
    return hits["chunk_id"].astype(str).tolist() if not hits.empty else []


def pool_candidates(dataset: str, question: str, depth: int) -> Dict[str, dict]:
    """네 설정의 상위 후보를 합쳐 {chunk_id: {pooled_by, best_rank}} 로 돌려준다."""
    runs = {
        "dense": _dense_ids(dataset, question, depth),
        "sparse": _sparse_ids(dataset, question, depth),
        "rrf": _fused_ids(dataset, question, depth, "rrf"),
        "weighted": _fused_ids(dataset, question, depth, "weighted"),
    }
    pooled: Dict[str, dict] = {}
    for run_name, ids in runs.items():
        for rank, chunk_id in enumerate(ids, start=1):
            entry = pooled.setdefault(chunk_id, {"pooled_by": [], "best_rank": rank})
            entry["pooled_by"].append(run_name)
            entry["best_rank"] = min(entry["best_rank"], rank)
    return pooled


# --------------------------------------------------------------------------- #
# 워크시트 병합
# --------------------------------------------------------------------------- #
def load_existing(path: Path) -> Dict[tuple[str, str, str], dict]:
    if not path.exists():
        return {}
    frame = pd.read_csv(path, dtype=str).fillna("")
    existing: Dict[tuple[str, str, str], dict] = {}
    for _, row in frame.iterrows():
        key = (
            str(row.get("question_id", "")).strip(),
            str(row.get("dataset", "")).strip(),
            str(row.get("chunk_id", "")).strip(),
        )
        existing[key] = {column: str(row.get(column, "")) for column in COLUMNS}
    return existing


_LIVE_IDS: Dict[str, set] = {}


def _live_chunk_ids(dataset: str) -> set:
    """지금 코퍼스에 실제로 있는 chunk_id 집합(데이터셋별 1회 계산)."""
    if dataset not in _LIVE_IDS:
        frame = _frame(dataset)
        _LIVE_IDS[dataset] = (
            set(frame["chunk_id"].astype(str)) if "chunk_id" in frame.columns else set()
        )
    return _LIVE_IDS[dataset]


def _chunk_metadata(dataset: str, chunk_id: str) -> dict:
    frame = _frame(dataset)
    if frame.empty or "chunk_id" not in frame.columns:
        return {}
    match = frame.loc[frame["chunk_id"].astype(str) == chunk_id]
    if match.empty:
        return {}
    row = match.iloc[0]
    text = str(row.get("chunk_text") or "").replace("\n", " ").strip()
    return {
        "title": str(row.get("title") or "")[:120],
        "published_at": str(row.get("published_at") or ""),
        "snippet": text[:SNIPPET_CHARS],
    }


def build_worksheet(
    cases: Sequence[dict],
    logged: Dict[str, Dict[str, dict]],
    existing: Dict[tuple[str, str, str], dict],
    depth: int,
) -> tuple[List[dict], dict]:
    rows: List[dict] = []
    stats = {
        "pooled": 0,
        "kept_labels": 0,
        "new": 0,
        "log_seeded": 0,
        "skipped_cases": 0,
        "stale_log_chunks": 0,
        "carried_over": 0,
    }

    for index, case in enumerate(cases, start=1):
        datasets = case["datasets"]
        if not datasets:
            stats["skipped_cases"] += 1
            continue
        print(f"  [{index:>3}/{len(cases)}] {case['question_id']:<8} {case['question'][:44]}")

        log_hits = logged.get(case["question"], {})
        for dataset in datasets:
            pooled = pool_candidates(dataset, case["question"], depth)

            # 로그 근거를 풀에 합친다. 검색이 지금은 못 찾더라도 후보로 남겨야
            # Recall이 과대평가되지 않는다.
            for chunk_id, info in log_hits.items():
                if info["dataset"] != dataset:
                    continue
                entry = pooled.setdefault(chunk_id, {"pooled_by": [], "best_rank": info["rank"]})
                entry["pooled_by"].append("log")
                entry["trusted"] = info["trusted"]
                stats["log_seeded"] += 1

            # 지난 실행이 모아 둔 후보도 풀에 남긴다. 이걸 빠뜨리면 풀이 **넓어지지
            # 않고 마지막 설정 결과로 교체된다**. 토크나이저를 바꿔 돌린 순간
            # 이전 설정만 찾던 후보 1,681건이 사라졌고, 그 상태로는 두 설정을
            # 같은 분모로 비교할 수 없다(각자 자기 풀에서 채점되니 항상 유리하다).
            for (existing_qid, existing_dataset, chunk_id), previous in existing.items():
                if existing_qid != case["question_id"] or existing_dataset != dataset:
                    continue
                if chunk_id in pooled:
                    continue
                sources = str(previous.get("pooled_by", "")).split("+")
                pooled[chunk_id] = {
                    "pooled_by": [source for source in sources if source] or ["prior"],
                    "best_rank": int(previous.get("best_rank") or 99),
                    "carried_over": True,
                }
                stats["carried_over"] += 1

            alive = _live_chunk_ids(dataset)
            for chunk_id, info in pooled.items():
                if chunk_id not in alive:
                    # 재청킹으로 사라진 chunk_id다. 라벨이 붙어 있어도 버려야 한다 —
                    # 어떤 설정으로도 검색할 수 없는 문서가 정답으로 남으면 Recall
                    # 분모에 유령이 끼어 상한이 내려간다. 실제로 규정을 8,144 →
                    # 5,576청크로 다시 나눈 뒤 rules 정답의 41%가 이 상태였다.
                    stats["stale_log_chunks"] += 1
                    continue

                key = (case["question_id"], dataset, chunk_id)
                previous = existing.get(key)
                if previous is not None and previous.get("relevance", "").strip():
                    rows.append(previous)  # 이미 매긴 판정은 그대로 보존
                    stats["kept_labels"] += 1
                    continue

                metadata = _chunk_metadata(dataset, chunk_id)
                sources = sorted(set(info["pooled_by"]))
                rows.append(
                    {
                        "question_id": case["question_id"],
                        "question": case["question"],
                        "domain": case["domain"],
                        "case_type": case["case_type"],
                        "dataset": dataset,
                        "chunk_id": chunk_id,
                        "relevance": "",
                        "label_source": "log_evidence" if "log" in sources else "pool",
                        "pooled_by": "+".join(sources),
                        "best_rank": str(info["best_rank"]),
                        "title": metadata.get("title", ""),
                        "published_at": metadata.get("published_at", ""),
                        "snippet": metadata.get("snippet", ""),
                        "note": "과거 근거·grounding 통과" if info.get("trusted") else "",
                    }
                )
                stats["new"] += 1
                stats["pooled"] += 1
    return rows, stats


def autolabel(rows: List[dict], cases: Sequence[dict], strategy: str) -> dict:
    """LLM 심판 없이 결정적으로 관련도를 매긴다.

    두 신호가 성격이 정반대라 함께 써야 한다.

    ``log``      과거에 근거로 선택돼 grounding까지 통과한 문서를 정답으로 본다.
                 정밀도는 높지만 **현재 시스템이 뽑은 것**이라, 이것만 쓰면
                 검색을 바꿔 다른 좋은 문서를 찾아낼수록 점수가 떨어진다.
                 baseline이 구조적으로 이기는 지표가 되어 ablation이 무의미해진다.

    ``keywords`` 골든 매트릭스의 `required_keywords`는 사람이 작성한 답변 계약이고
                 현재 검색기와 무관하게 만들어졌다. 그래서 자기편향이 없다.
                 대신 어휘가 겹칠 뿐 답이 아닌 문서도 걸린다(재현율 높고 정밀도 낮음).

    ``log+keywords`` 둘을 합친다. 이미 사람이 판정한 행은 어느 쪽도 건드리지 않는다.
    """
    keywords_by_id = {
        case["question_id"]: [
            keyword.strip()
            for keyword in case.get("required_keywords", "").split(";")
            if keyword.strip()
        ]
        for case in cases
    }
    use_log = "log" in strategy
    use_keywords = "keywords" in strategy
    stats = {"log": 0, "keyword_full": 0, "keyword_partial": 0, "zero": 0, "already_labelled": 0}

    for row in rows:
        if str(row.get("relevance", "")).strip():
            stats["already_labelled"] += 1
            continue

        relevance = 0
        source = "auto_zero"
        if use_log and "log" in str(row.get("pooled_by", "")) and row.get("note"):
            # note가 채워진 로그 후보 = grounding까지 통과한 답변의 근거
            relevance, source = 2, "auto_log"
            stats["log"] += 1
        elif use_keywords:
            wanted = keywords_by_id.get(row["question_id"], [])
            haystack = f"{row.get('title', '')} {row.get('snippet', '')}"
            if wanted:
                found = sum(1 for keyword in wanted if keyword in haystack)
                if found == len(wanted):
                    relevance, source = 2, "auto_keyword_full"
                    stats["keyword_full"] += 1
                elif len(wanted) >= 3 and found * 2 >= len(wanted):
                    # 부분 일치는 키워드가 3개 이상일 때만 쓴다. 골든 문항의
                    # 162/190이 키워드 2개라, "절반 이상"을 그대로 적용하면
                    # 하나만 맞아도 정답이 되어 후보의 39%가 관련 문서로 잡혔다.
                    # 그 라벨로는 어떤 검색 변경도 차이가 나지 않는다.
                    relevance, source = 1, "auto_keyword_partial"
                    stats["keyword_partial"] += 1

        if relevance == 0:
            stats["zero"] += 1
        row["relevance"] = str(relevance)
        row["label_source"] = source
    return stats


def report(path: Path) -> int:
    if not path.exists():
        print(f"워크시트가 없습니다: {path}")
        return 1
    frame = pd.read_csv(path, dtype=str).fillna("")
    labelled = frame[frame["relevance"].str.strip() != ""]
    print(f"\n워크시트 {path}")
    print(f"  후보 총계     {len(frame)}")
    print(f"  판정 완료     {len(labelled)} ({len(labelled) / max(len(frame), 1):.1%})")
    if not labelled.empty:
        print(f"  관련도 분포   {labelled['relevance'].value_counts().to_dict()}")
    graded_questions = labelled.loc[
        pd.to_numeric(labelled["relevance"], errors="coerce").fillna(0) >= 1,
        "question_id",
    ].nunique()
    print(f"  정답이 있는 문항 {graded_questions} / {frame['question_id'].nunique()}")
    unlabelled = frame[frame["relevance"].str.strip() == ""]
    if not unlabelled.empty:
        print(f"\n  판정이 남은 문항 상위:")
        for question_id, count in unlabelled["question_id"].value_counts().head(8).items():
            print(f"    {question_id:<10} {count}건")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--matrix", type=Path, default=MATRIX_PATH)
    parser.add_argument("--out", type=Path, default=QRELS_PATH)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--depth", type=int, default=10, help="설정당 풀에 넣을 상위 후보 수")
    parser.add_argument("--limit", type=int, help="앞에서 N문항만 처리(시험용)")
    parser.add_argument("--report", action="store_true", help="풀링하지 않고 진행률만 출력")
    parser.add_argument(
        "--autolabel",
        choices=("log", "keywords", "log+keywords"),
        help="LLM 심판 없이 결정적으로 관련도를 매긴다(사람이 판정한 행은 보존)",
    )
    args = parser.parse_args()

    if args.report:
        return report(args.out)

    cases = load_matrix(args.matrix)
    if args.limit:
        cases = cases[: args.limit]
    logged = load_logged_evidence(args.db)
    existing = load_existing(args.out)
    print(
        f"골든 {len(cases)}문항 · 과거 근거가 있는 질문 {len(logged)}건 · "
        f"기존 워크시트 {len(existing)}행"
    )

    rows, stats = build_worksheet(cases, logged, existing, args.depth)
    if not rows:
        print("풀링된 후보가 없습니다.")
        return 1

    if args.autolabel:
        label_stats = autolabel(rows, cases, args.autolabel)
        print(
            f"\n자동 라벨링({args.autolabel}): "
            f"로그근거 {label_stats['log']} · 키워드전부 {label_stats['keyword_full']} · "
            f"키워드일부 {label_stats['keyword_partial']} · 무관 {label_stats['zero']} "
            f"(사람 판정 보존 {label_stats['already_labelled']})"
        )

    frame = pd.DataFrame(rows, columns=list(COLUMNS))
    frame.sort_values(
        ["question_id", "dataset", "best_rank"],
        key=lambda column: pd.to_numeric(column, errors="coerce") if column.name == "best_rank" else column,
        inplace=True,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.out, index=False, encoding="utf-8")

    print(f"\n워크시트 {args.out}")
    print(f"  후보 {len(frame)}행 (신규 {stats['new']} · 기존 판정 보존 {stats['kept_labels']})")
    print(f"  로그 근거로 풀에 더해진 후보 {stats['log_seeded']}건")
    if stats["carried_over"]:
        print(
            f"  이전 실행에서 이어받은 후보 {stats['carried_over']}건 "
            "(지금 설정은 못 찾지만 풀에 남긴다)"
        )
    if stats["stale_log_chunks"]:
        print(
            f"  코퍼스에 없어 버린 후보 {stats['stale_log_chunks']}건 "
            "(재청킹으로 chunk_id가 사라진 것 — 라벨이 있어도 버린다)"
        )
    if stats["skipped_cases"]:
        print(f"  expected_datasets가 없어 건너뛴 문항 {stats['skipped_cases']}건")
    print("\n다음 단계: relevance 칸을 0/1/2로 채우세요.")
    print("  2 = 질문에 직접 답한다 · 1 = 부분적으로 뒷받침한다 · 0 = 관련 없음")
    print("  판정을 마친 뒤: python scripts/evaluate_retrieval.py --save <스냅샷.json>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
