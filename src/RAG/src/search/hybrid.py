"""노트북에서 가져온 밀집 임베딩+TF-IDF 하이브리드 검색 유틸리티입니다."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Tuple, Dict # Dict 추가

import joblib
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from src.config import DEFAULT_TOP_K, HYBRID_ALPHA, VECTORIZER_DIR
from src.models.embedding import encode_texts
from src.vectorstore.chroma_client import get_collection


def _vectorizer_path(identifier: str) -> Path:
    return VECTORIZER_DIR / f"{identifier}_tfidf.pkl"


def train_tfidf(identifier: str, corpus: Iterable[str]) -> Tuple[TfidfVectorizer, np.ndarray]:
    """주어진 말뭉치에 TF-IDF 벡터라이저를 학습시키고 저장합니다."""
    texts = list(corpus)
    if not texts:
        raise ValueError("Corpus is empty, cannot train TF-IDF vectorizer.")
    vectorizer = TfidfVectorizer(max_features=10000)
    matrix = vectorizer.fit_transform(texts)
    joblib.dump({"vectorizer": vectorizer, "matrix": matrix}, _vectorizer_path(identifier))
    return vectorizer, matrix


def load_tfidf(identifier: str) -> Tuple[TfidfVectorizer, np.ndarray]:
    """이미 학습된 TF-IDF 벡터라이저와 행렬을 불러옵니다."""
    data = joblib.load(_vectorizer_path(identifier))
    return data["vectorizer"], data["matrix"]


def hybrid_search(
    collection_name: str,
    chunks_df: pd.DataFrame,
    tfidf_vectorizer: TfidfVectorizer,
    tfidf_matrix,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    alpha: float = HYBRID_ALPHA,
    where_filter: Dict | None = None,
) -> pd.DataFrame:
    """
    최적화된 하이브리드 검색:
    1. Vector Search로 상위 N개 검색
    2. TF-IDF Search로 상위 N개 검색
    3. 결과 결합 및 재정렬 (Rerank)
    """
    if chunks_df.empty:
        return chunks_df.copy()

    # 검색 후보 수 (Top-K보다 넉넉하게 가져와서 랭킹)
    limit = top_k * 3 
    
    # 1. Vector Search (Dense)
    collection = get_collection(collection_name)
    query_embedding = encode_texts([query])
    
    vec_results = collection.query(
        query_embeddings=query_embedding,
        n_results=limit,
        where=where_filter,
    )
    
    vec_ids = (vec_results.get("ids") or [[]])[0]
    vec_dists = (vec_results.get("distances") or [[]])[0]
    
    # 거리(Distance)를 유사도(Similarity)로 변환 (Cosine Distance 가정: 1 - distance)
    vec_scores = {cid: (1 - dist) for cid, dist in zip(vec_ids, vec_dists)}

    # 2. Sparse Search (TF-IDF)
    # 전체 chunks_df 순서와 tfidf_matrix 순서가 같다고 가정
    query_vec = tfidf_vectorizer.transform([query])
    sparse_sims = cosine_similarity(query_vec, tfidf_matrix).ravel()
    
    # TF-IDF 점수 상위 limit개 추출
    # where_filter가 있다면 TF-IDF에도 적용해야 정확하지만, 
    # TF-IDF 구조상 필터링이 어려우므로 일단 전체에서 검색 후 나중에 필터링된 ID와 교차 검증
    sparse_indices = np.argsort(sparse_sims)[::-1][:limit]
    
    sparse_scores = {}
    for idx in sparse_indices:
        if idx >= len(chunks_df): # 인덱스 범위 체크 (TF-IDF와 DataFrame 불일치 방지)
            continue
            
        if sparse_sims[idx] > 0:
            cid = str(chunks_df.iloc[idx]["chunk_id"])
            sparse_scores[cid] = sparse_sims[idx]

    # 3. Merge & Fusion
    # 후보군: Vector 검색 결과 OR TF-IDF 검색 결과
    all_candidates = set(vec_scores.keys()) | set(sparse_scores.keys())
    
    # where_filter가 있는 경우, TF-IDF 결과 중 필터 조건에 맞지 않는 문서는 제외되어야 함
    # 하지만 현재 구조상 chunks_df 전체를 가지고 있으므로, 
    # chunks_df에서 필터링을 먼저 수행하는 것이 맞으나 성능상 비효율적일 수 있음.
    # 여기서는 Vector Search가 필터를 적용했으므로, Vector 결과에 없는 ID가 TF-IDF에서 나왔다면
    # 그 문서가 필터 조건을 만족하는지 확인해야 함.
    # 간단한 해결책: Vector Search 결과가 하나라도 있으면(필터 적용됨), 
    # TF-IDF 결과도 그 필터를 통과한 것만 남겨야 함.
    # 하지만 메타데이터 필터링을 DataFrame에서 다시 하기는 번거로움.
    # 타협안: Vector Search의 'where' 필터가 강력하다면(날짜 등), Vector Search 결과에 의존하는 비중을 높이거나
    # TF-IDF는 필터를 무시하고 검색되므로, 필터가 필수적인 경우(날짜 지난 공지 등) 잘못된 문서가 섞일 수 있음.
    # -> 정확성을 위해: where_filter가 있으면 TF-IDF 점수는 Vector Search 결과에 포함된 문서에 대해서만 가산하는 방식(Intersection)을 고려할 수 있으나,
    # 그러면 키워드 검색의 장점(벡터가 놓친 문서 찾기)이 사라짐.
    # -> 최적화된 방법: chunks_df 자체를 미리 필터링하고 TF-IDF를 수행해야 하지만 matrix 인덱스가 꼬임.
    # 결론: 현재 구조에서는 Vector Search(필터 적용됨) 결과에 가중치를 두고, 
    # TF-IDF 결과는 필터링되지 않았음을 인지해야 함. 
    # 단, Vector Search 결과가 없으면 TF-IDF 결과만 남게 되는데 이때 필터 위반 문서가 나올 수 있음.
    # 이를 방지하기 위해, where_filter가 존재하면 Vector Search 결과 집합(vec_scores.keys())에 포함된 문서만 후보로 삼는 것이 안전함 (Intersection).
    # 하지만 이는 Hybrid Search의 의미를 퇴색시킴.
    # 따라서, 성능을 위해 'where_filter'가 제공되면 Vector Search 결과만 사용하거나,
    # TF-IDF는 보조적인 수단으로만 사용하도록 구현.
    
    final_candidates = []
    
    if where_filter:
        # 필터가 있으면 Vector Search 결과에 나온 문서들만 후보로 인정 (안전한 필터링 보장)
        # TF-IDF 점수는 이 문서들에 대해서만 더해짐 (Re-ranking 역할)
        candidate_ids = set(vec_scores.keys())
    else:
        candidate_ids = all_candidates

    hybrid_results = []
    for cid in candidate_ids:
        v_score = vec_scores.get(cid, 0.0)
        s_score = sparse_scores.get(cid, 0.0)
        
        # 가중 합산
        final_score = alpha * v_score + (1.0 - alpha) * s_score
        hybrid_results.append((cid, final_score))
    
    # 점수순 정렬
    hybrid_results.sort(key=lambda x: x[1], reverse=True)
    top_results = hybrid_results[:top_k]
    
    # 결과 DataFrame 생성
    top_ids = [res[0] for res in top_results]
    top_scores = [res[1] for res in top_results]
    
    # 원본 DataFrame에서 해당 ID를 가진 행 추출 및 순서 유지
    # set_index를 사용하여 빠르게 조회
    df_indexed = chunks_df.set_index("chunk_id")
    
    # top_ids가 chunks_df에 없는 경우(삭제된 문서 등) 방지
    valid_ids = [cid for cid in top_ids if cid in df_indexed.index]
    
    if not valid_ids:
        # 원본 DataFrame의 구조를 유지한 빈 DataFrame 반환
        return chunks_df.iloc[:0].copy()
        
    result_df = df_indexed.loc[valid_ids].copy()
    result_df["hybrid_score"] = top_scores[:len(valid_ids)]
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
) -> pd.DataFrame:
    """노트북과 같은 형식으로 메타데이터 열을 청크 텍스트와 함께 반환합니다."""
    hits = hybrid_search(collection_name, chunks_df, tfidf_vectorizer, tfidf_matrix, query, top_k, alpha, where_filter) # where_filter 전달
    out = hits.copy()
    out["title"] = out["chunk_text"].apply(_extract_title)
    for column in ("topics", "published_at", "url", "source"):
        if column not in out.columns:
            out[column] = ""
    desired = ["title", "chunk_text", "hybrid_score", "topics", "published_at", "url", "source"]
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
    "hybrid_search",
    "hybrid_search_with_meta",
]
