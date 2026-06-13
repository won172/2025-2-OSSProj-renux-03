"""노트북에서 가져온 밀집 임베딩+TF-IDF 하이브리드 검색 유틸리티입니다."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Dict # Dict 추가

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from src.config import (
    DEFAULT_TOP_K,
    HYBRID_ALPHA,
    TFIDF_REQUIRE_MANIFEST,
    TFIDF_VERIFY_INTEGRITY,
    VECTORIZER_DIR,
)
from src.models.embedding import encode_queries
from src.vectorstore.chroma_client import get_collection

logger = logging.getLogger(__name__)

# TF-IDF pkl 무결성 매니페스트: {파일명: sha256}. 학습 시 갱신하고 로드 전 대조한다.
# 경로는 호출 시점에 VECTORIZER_DIR에서 파생한다(테스트의 VECTORIZER_DIR 패치가 함께 적용되도록).
_MANIFEST_NAME = "manifest.json"


def _vectorizer_path(identifier: str) -> Path:
    return VECTORIZER_DIR / f"{identifier}_tfidf.pkl"


def _manifest_path() -> Path:
    return VECTORIZER_DIR / _MANIFEST_NAME


def _sha256_file(path: Path) -> str:
    """파일의 SHA-256 16진 다이제스트를 청크 단위로 계산한다(대용량 pkl 대비)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_manifest() -> Dict[str, str]:
    manifest_path = _manifest_path()
    if not manifest_path.exists():
        return {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("TF-IDF 매니페스트를 읽지 못했습니다(%s): %s", manifest_path, exc)
        return {}


def _update_manifest(filename: str, digest: str) -> None:
    """학습 직후 매니페스트에 {파일명: sha256}을 기록한다."""
    manifest_path = _manifest_path()
    manifest = _read_manifest()
    manifest[filename] = digest
    tmp_path = manifest_path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)
    tmp_path.replace(manifest_path)


def _verify_artifact_integrity(path: Path) -> None:
    """joblib.load 이전에 매니페스트 해시와 대조한다(불일치 시 fail-closed).

    pkl/joblib 아티팩트는 역직렬화 중 임의 코드를 실행할 수 있으므로, 신뢰된
    매니페스트와 다른 파일은 로드하지 않는다. 검증은 TFIDF_VERIFY_INTEGRITY로 끌 수 있고,
    매니페스트 미등록 항목의 거부 여부는 TFIDF_REQUIRE_MANIFEST로 제어한다.
    """
    if not TFIDF_VERIFY_INTEGRITY:
        return
    manifest = _read_manifest()
    expected = manifest.get(path.name)
    if expected is None:
        msg = (
            f"TF-IDF 아티팩트 '{path.name}'가 무결성 매니페스트에 없습니다. "
            f"scripts/build_indices.py 재생성 또는 매니페스트 갱신이 필요합니다."
        )
        if TFIDF_REQUIRE_MANIFEST:
            raise ValueError(msg + " (TFIDF_REQUIRE_MANIFEST=1 — 로드 거부)")
        logger.warning(msg + " (검증 없이 로드)")
        return
    actual = _sha256_file(path)
    if actual != expected:
        raise ValueError(
            f"TF-IDF 아티팩트 '{path.name}' 무결성 검증 실패: "
            f"매니페스트 해시와 불일치(변조/손상 가능). 로드를 거부합니다."
        )


def train_tfidf(
    identifier: str,
    corpus: Iterable[str],
    chunk_ids: Iterable[str] | None = None,
) -> Tuple[TfidfVectorizer, np.ndarray]:
    """주어진 말뭉치에 TF-IDF 벡터라이저를 학습시키고 저장합니다.

    chunk_ids를 함께 주면 행→chunk_id 매핑이 아티팩트에 저장되어,
    검색 시 chunks_df 행 순서에 의존하지 않고 점수를 매핑할 수 있습니다.
    """
    texts = list(corpus)
    if not texts:
        raise ValueError("Corpus is empty, cannot train TF-IDF vectorizer.")
    ids = [str(cid) for cid in chunk_ids] if chunk_ids is not None else None
    if ids is not None and len(ids) != len(texts):
        raise ValueError(
            f"chunk_ids length ({len(ids)}) does not match corpus length ({len(texts)})."
        )
    vectorizer = TfidfVectorizer(max_features=10000)
    matrix = vectorizer.fit_transform(texts)
    path = _vectorizer_path(identifier)
    joblib.dump(
        {
            "vectorizer": vectorizer,
            "matrix": matrix,
            "chunk_ids": ids,
            "metadata": {
                "dataset": identifier,
                "document_count": len(texts),
                "created_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
                "sklearn_version": sklearn.__version__,
            },
        },
        path,
    )
    # 방금 쓴 아티팩트의 해시를 매니페스트에 기록 → 이후 로드 시 무결성 검증의 신뢰 기준이 된다.
    _update_manifest(path.name, _sha256_file(path))
    return vectorizer, matrix


def _load_tfidf_artifact(identifier: str) -> dict:
    path = _vectorizer_path(identifier)
    _verify_artifact_integrity(path)  # joblib.load(임의 코드 실행 가능) 이전에 fail-closed 검증
    artifact = joblib.load(path)
    if isinstance(artifact, dict) and "vectorizer" in artifact and "matrix" in artifact:
        metadata = artifact.get("metadata")
        if isinstance(metadata, dict):
            return artifact
        return {
            "vectorizer": artifact["vectorizer"],
            "matrix": artifact["matrix"],
            "chunk_ids": artifact.get("chunk_ids"),
            "metadata": {
                "dataset": identifier,
                "document_count": None,
                "created_at": None,
                "sklearn_version": None,
                "is_legacy": True,
            },
        }

    raise ValueError(f"Unexpected TF-IDF artifact format for '{identifier}'.")


def load_tfidf(identifier: str) -> Tuple[TfidfVectorizer, np.ndarray]:
    """이미 학습된 TF-IDF 벡터라이저와 행렬을 불러옵니다."""
    data = _load_tfidf_artifact(identifier)
    return data["vectorizer"], data["matrix"]


def load_tfidf_with_ids(identifier: str) -> Tuple[TfidfVectorizer, np.ndarray, Optional[List[str]]]:
    """TF-IDF 벡터라이저/행렬과 함께 행→chunk_id 매핑을 불러옵니다(구버전 아티팩트면 None)."""
    data = _load_tfidf_artifact(identifier)
    return data["vectorizer"], data["matrix"], data.get("chunk_ids")


def read_tfidf_metadata(identifier: str) -> Dict:
    """TF-IDF 아티팩트 메타데이터를 읽습니다. legacy 포맷이면 legacy 플래그를 반환합니다."""
    data = _load_tfidf_artifact(identifier)
    metadata = data.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _matches_where(row: pd.Series, where_filter: Dict) -> bool:
    """Chroma 스타일 where 필터({"key": {"$eq": v}} / {"$and": [...]})를 DataFrame 행에 평가합니다."""
    for key, condition in where_filter.items():
        if key == "$and":
            if not all(_matches_where(row, sub) for sub in condition):
                return False
            continue
        if key == "$or":
            if not any(_matches_where(row, sub) for sub in condition):
                return False
            continue
        value = row.get(key) if key in row.index else None
        if isinstance(condition, dict):
            for op, expected in condition.items():
                if op == "$eq":
                    if str(value) != str(expected):
                        return False
                elif op == "$ne":
                    if str(value) == str(expected):
                        return False
                elif op == "$in":
                    if str(value) not in {str(e) for e in expected}:
                        return False
                else:
                    # 지원하지 않는 연산자는 보수적으로 불일치 처리
                    return False
        else:
            if str(value) != str(condition):
                return False
    return True


def hybrid_search(
    collection_name: str,
    chunks_df: pd.DataFrame,
    tfidf_vectorizer: TfidfVectorizer,
    tfidf_matrix,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    alpha: float = HYBRID_ALPHA,
    where_filter: Dict | None = None,
    tfidf_chunk_ids: List[str] | None = None,
) -> pd.DataFrame:
    """
    최적화된 하이브리드 검색:
    1. Vector Search로 상위 N개 검색
    2. TF-IDF Search로 상위 N개 검색
    3. 결과 결합 및 재정렬 (Rerank)

    tfidf_chunk_ids: TF-IDF 행렬의 행→chunk_id 매핑(아티팩트에 저장된 것).
    주어지면 chunks_df 행 순서에 의존하지 않고 sparse 점수를 매핑합니다.
    """
    if chunks_df.empty:
        return chunks_df.copy()

    # 검색 후보 수 (Top-K보다 넉넉하게 가져와서 랭킹)
    limit = top_k * 3 
    
    # 1. Vector Search (Dense)
    collection = get_collection(collection_name)
    query_embedding = encode_queries([query])
    
    vec_results = collection.query(
        query_embeddings=query_embedding,
        n_results=limit,
        where=where_filter,
    )
    
    vec_ids = (vec_results.get("ids") or [[]])[0]
    vec_dists = (vec_results.get("distances") or [[]])[0]

    # 거리(Distance)를 유사도(Similarity)로 변환 — 컬렉션의 실제 메트릭에 맞게 처리.
    # cosine/ip: dist = 1 - sim → sim = 1 - dist
    # l2(Chroma는 squared L2 반환): 정규화 임베딩이면 dist = 2 - 2cos → sim = 1 - dist/2
    space = (getattr(collection, "metadata", None) or {}).get("hnsw:space", "l2")
    if space in ("cosine", "ip"):
        _to_sim = lambda d: 1.0 - d
    else:
        _to_sim = lambda d: 1.0 - d / 2.0
    vec_scores = {cid: max(0.0, _to_sim(dist)) for cid, dist in zip(vec_ids, vec_dists)}

    # 2. Sparse Search (TF-IDF)
    # 행→chunk_id 매핑: 아티팩트의 chunk_ids가 있으면 그것을 사용(행 순서 결합 제거),
    # 없으면(구버전 아티팩트) 행 수가 chunks_df와 일치할 때만 행 순서로 매핑.
    matrix_rows = tfidf_matrix.shape[0]
    row_ids: List[str] | None = None
    if tfidf_chunk_ids is not None and len(tfidf_chunk_ids) == matrix_rows:
        row_ids = [str(cid) for cid in tfidf_chunk_ids]
    elif matrix_rows == len(chunks_df):
        row_ids = chunks_df["chunk_id"].astype(str).tolist()
    else:
        logging.warning(
            "TF-IDF matrix rows (%d) do not match chunks_df rows (%d) for collection '%s' "
            "and no chunk_ids mapping is available — skipping sparse scoring (vector-only).",
            matrix_rows,
            len(chunks_df),
            collection_name,
        )

    sparse_scores: Dict[str, float] = {}
    if row_ids is not None:
        query_vec = tfidf_vectorizer.transform([query])
        sparse_sims = cosine_similarity(query_vec, tfidf_matrix).ravel()
        sparse_indices = np.argsort(sparse_sims)[::-1][:limit]
        for idx in sparse_indices:
            if sparse_sims[idx] > 0:
                sparse_scores[str(row_ids[idx])] = sparse_sims[idx]
        if not sparse_scores:
            # OOV 쿼리 등으로 sparse 기여가 0이면 무음으로 vector-only가 되므로 흔적을 남긴다
            logging.info(
                "TF-IDF returned no positive scores for query %r on '%s' (vector-only search).",
                query, collection_name,
            )

    # 3. Merge & Fusion
    # 후보군: Vector 검색 결과 OR TF-IDF 검색 결과.
    # where_filter가 있으면 벡터 검색은 Chroma에서 이미 필터링됨.
    # TF-IDF-only 히트는 chunks_df 메타데이터로 필터를 직접 검증해 통과한 것만 후보로 살린다
    # (이전에는 벡터 결과와의 intersection만 허용해 키워드-only 히트가 전부 버려졌음).
    if where_filter:
        sparse_only = set(sparse_scores.keys()) - set(vec_scores.keys())
        candidate_ids = set(vec_scores.keys())
        if sparse_only:
            id_to_pos = {
                str(cid): pos
                for pos, cid in enumerate(chunks_df["chunk_id"].astype(str).tolist())
            }
            for cid in sparse_only:
                pos = id_to_pos.get(cid)
                if pos is None:
                    continue
                if _matches_where(chunks_df.iloc[pos], where_filter):
                    candidate_ids.add(cid)
    else:
        candidate_ids = set(vec_scores.keys()) | set(sparse_scores.keys())

    hybrid_results = []
    for cid in candidate_ids:
        v_score = vec_scores.get(cid, 0.0)
        s_score = sparse_scores.get(cid, 0.0)
        
        # 가중 합산
        final_score = alpha * v_score + (1.0 - alpha) * s_score
        hybrid_results.append((cid, final_score, v_score, s_score))
    
    # 점수순 정렬
    hybrid_results.sort(key=lambda x: x[1], reverse=True)
    top_results = hybrid_results[:top_k]
    
    # 결과 DataFrame 생성
    top_ids = [res[0] for res in top_results]
    score_by_id = {
        res[0]: {
            "hybrid_score": res[1],
            "vector_score": res[2],
            "sparse_score": res[3],
        }
        for res in top_results
    }
    
    # 원본 DataFrame에서 해당 ID를 가진 행 추출 및 순서 유지
    # set_index를 사용하여 빠르게 조회 (중복 chunk_id는 첫 행만 유지해 .loc 다중행 매칭 방지)
    df_indexed = (
        chunks_df.assign(chunk_id=chunks_df["chunk_id"].astype(str))
        .drop_duplicates(subset="chunk_id", keep="first")
        .set_index("chunk_id")
    )
    
    # top_ids가 chunks_df에 없는 경우(삭제된 문서 등) 방지
    valid_ids = [cid for cid in top_ids if cid in df_indexed.index]
    
    if not valid_ids:
        # 원본 DataFrame의 구조를 유지한 빈 DataFrame 반환
        return chunks_df.iloc[:0].copy()
        
    result_df = df_indexed.loc[valid_ids].copy()
    result_df["hybrid_score"] = [score_by_id[cid]["hybrid_score"] for cid in valid_ids]
    result_df["vector_score"] = [score_by_id[cid]["vector_score"] for cid in valid_ids]
    result_df["sparse_score"] = [score_by_id[cid]["sparse_score"] for cid in valid_ids]
    result_df = result_df.reset_index() # chunk_id를 다시 컬럼으로
    
    return result_df


def hybrid_search_with_meta(
    collection_name: str,
    chunks_df: pd.DataFrame,
    tfidf_vectorizer: TfidfVectorizer,
    tfidf_matrix,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    alpha: float = HYBRID_ALPHA,
    where_filter: Dict | None = None, # where_filter 추가
    tfidf_chunk_ids: List[str] | None = None,
) -> pd.DataFrame:
    """노트북과 같은 형식으로 메타데이터 열을 청크 텍스트와 함께 반환합니다."""
    hits = hybrid_search(collection_name, chunks_df, tfidf_vectorizer, tfidf_matrix, query, top_k, alpha, where_filter, tfidf_chunk_ids) # where_filter 전달
    out = hits.copy()
    out["title"] = out["chunk_text"].apply(_extract_title)
    for column in ("topics", "category", "published_at", "url", "source", "notice_id"):
        if column not in out.columns:
            out[column] = ""
    # major/entry_year/source_type/attachments는 후속 학과 필터·entry_year 가산점·첨부 링크
    # 로직이 사용하므로 존재하면 반드시 함께 반환한다(누락 시 해당 기능이 조용히 비활성).
    desired = [
        "chunk_id", "title", "chunk_text", "hybrid_score", "vector_score", "sparse_score",
        "topics", "category", "published_at", "url", "source", "notice_id",
        "major", "entry_year", "source_type", "attachments",
        "doc_id", "position",  # parent-document 확장(이웃 청크 결합)에 사용
    ]
    existing = [col for col in desired if col in out.columns]
    return out[existing]


def _extract_title(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return ""
    if text.startswith("[") and "]" in text:
        closing = text.index("]")
        return text[1:closing].strip()
    return text.split("\n", 1)[0].strip()[:120]


__all__ = [
    "train_tfidf",
    "load_tfidf",
    "load_tfidf_with_ids",
    "read_tfidf_metadata",
    "hybrid_search",
    "hybrid_search_with_meta",
]
