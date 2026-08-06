"""pkl(rank_bm25)과 FTS5 희소 검색 백엔드를 검색 계층에서 비교한다.

전체 파이프라인(evaluate_rag.py)이 아니라 검색만 재는 이유는, 생성 단계가 두
백엔드에서 동일하기 때문이다. LLM을 태우면 그 비결정성이 검색 차이를 가리고,
76문항 × 2회 × 12초의 비용도 든다. 밀집(Chroma) 검색도 양쪽이 같으므로,
차이는 전부 희소 검색에서 나온다.

측정 항목
  키워드 적중  골든셋 expected_keywords가 검색된 top-k 본문에 있는가.
               답변에 그 단어가 나오려면 근거에 먼저 있어야 하므로 필요조건이다.
  최초 순위    키워드를 모두 담은 첫 문서의 순위(작을수록 좋다).
  top-k 겹침   두 백엔드가 같은 문서를 뽑는 비율. 낮다고 나쁜 게 아니라
               서로 다른 문서를 뽑았다는 뜻이므로, 키워드 적중과 함께 봐야 한다.

사용:
    python scripts/compare_lexical_backends.py [--top-k 10] [--dataset notices]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.search.hybrid as hybrid  # noqa: E402
from src.pipelines.ingest import DATASET_ARTIFACTS  # noqa: E402
from src.services.retrieval_context import enrich_retrieval_fields  # noqa: E402

GOLDEN = Path(__file__).resolve().parents[1] / "tests" / "evaluation_set.csv"
BACKENDS = ("pickle", "fts5")


def _load_dataset(key: str) -> pd.DataFrame:
    path = DATASET_ARTIFACTS[key].chunk_path
    if not path.exists():
        return pd.DataFrame()
    return enrich_retrieval_fields(pd.read_parquet(path))


def _search(key: str, chunks_df: pd.DataFrame, query: str, top_k: int) -> pd.DataFrame:
    """현재 hybrid.LEXICAL_BACKEND 설정으로 검색한다."""
    data = hybrid._load_lexical_artifact(key)
    return hybrid.hybrid_search(
        DATASET_ARTIFACTS[key].collection,
        chunks_df,
        data["vectorizer"],
        data["matrix"],
        query,
        top_k=top_k,
        tfidf_chunk_ids=data["chunk_ids"],
    )


def _keywords(raw: object) -> list[str]:
    if not isinstance(raw, str) or not raw.strip():
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


def _measure(hits: pd.DataFrame, keywords: list[str]) -> tuple[float, int | None]:
    """(키워드 커버리지, 전부 커버되는 순위)를 잰다.

    커버리지는 top-k **전체를 합친** 근거에서 세야 한다. 답변은 여러 청크를
    합쳐 만들어지므로, 키워드가 한 청크 안에 모두 있기를 요구하면 실제
    검색 품질과 무관하게 대부분 미달로 잡힌다(그 지표로는 두 백엔드가 똑같이
    18/70이 나와 나머지 74%의 차이를 보지 못했다).

    두 번째 값은 위에서부터 몇 개를 봐야 모든 키워드가 나오는지다. 작을수록
    좋고, k개를 다 봐도 못 채우면 None이다.
    """
    if not keywords or hits.empty:
        return (0.0, None)
    column = "retrieval_text" if "retrieval_text" in hits.columns else "chunk_text"
    texts = hits[column].fillna("").astype(str).tolist()

    남은 = set(keywords)
    전부_순위: int | None = None
    for rank, text in enumerate(texts, start=1):
        남은 -= {k for k in 남은 if k in text}
        if not 남은:
            전부_순위 = rank
            break
    커버 = (len(keywords) - len(남은)) / len(keywords)
    return (커버, 전부_순위)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--dataset", help="한 데이터셋만 비교")
    args = parser.parse_args()

    golden = pd.read_csv(GOLDEN)
    frames = {
        key: _load_dataset(key)
        for key in DATASET_ARTIFACTS
        if args.dataset in (None, key)
    }
    frames = {k: v for k, v in frames.items() if not v.empty}

    rows: list[dict] = []
    for _, g in golden.iterrows():
        key = str(g.get("expected_dataset", "")).strip()
        if key not in frames:
            continue
        question = str(g["question"])
        keywords = _keywords(g.get("expected_keywords"))

        result: dict = {"question": question, "dataset": key, "keywords": len(keywords)}
        ids: dict[str, list[str]] = {}
        for backend in BACKENDS:
            hybrid.LEXICAL_BACKEND = backend
            hits = _search(key, frames[key], question, args.top_k)
            ids[backend] = hits["chunk_id"].astype(str).tolist() if not hits.empty else []
            커버, 순위 = _measure(hits, keywords)
            result[f"cover_{backend}"] = 커버
            result[f"rank_{backend}"] = 순위
        both = set(ids["pickle"]) & set(ids["fts5"])
        result["overlap"] = len(both) / max(len(ids["pickle"]), 1)
        rows.append(result)

    hybrid.LEXICAL_BACKEND = "pickle"
    df = pd.DataFrame(rows)
    if df.empty:
        print("비교할 골든 항목이 없습니다.")
        return 1

    graded = df[df["keywords"] > 0]
    print(f"\n{'='*72}")
    print(f"골든셋 {len(df)}건 (키워드 채점 대상 {len(graded)}건) · top-{args.top_k}")
    print("=" * 72)

    for backend in BACKENDS:
        cover = graded[f"cover_{backend}"]
        rank = graded[f"rank_{backend}"]
        전부 = rank.notna()
        print(f"  {backend:<7} 키워드 커버리지 평균 {cover.mean():>6.1%}"
              f"   전부 확보 {전부.sum():>3}/{len(graded)} ({전부.mean():>5.1%})"
              f"   그때 평균 순위 {rank[전부].mean() if 전부.any() else float('nan'):>4.1f}")

    print(f"\n  top-{args.top_k} 겹침 중앙값 {df['overlap'].median():.0%} · 평균 {df['overlap'].mean():.0%}")

    차 = graded["cover_fts5"] - graded["cover_pickle"]
    print(f"\n  커버리지 차이(fts5 - pkl): 평균 {차.mean():+.1%}"
          f" · fts5 우세 {int((차 > 0).sum())}건 · pkl 우세 {int((차 < 0).sum())}건"
          f" · 동일 {int((차 == 0).sum())}건")

    악화 = graded[차 < 0].assign(차=차).sort_values("차")
    if len(악화):
        print(f"\n  fts5에서 커버리지가 떨어진 항목 {len(악화)}건")
        for _, r in 악화.iterrows():
            print(f"    {r['cover_pickle']:.0%} → {r['cover_fts5']:.0%}  "
                  f"({r['dataset']}) {r['question'][:50]}")
    개선 = graded[차 > 0].assign(차=차).sort_values("차", ascending=False)
    if len(개선):
        print(f"\n  fts5에서 커버리지가 올라간 항목 {len(개선)}건")
        for _, r in 개선.iterrows():
            print(f"    {r['cover_pickle']:.0%} → {r['cover_fts5']:.0%}  "
                  f"({r['dataset']}) {r['question'][:50]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
