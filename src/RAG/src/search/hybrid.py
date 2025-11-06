"""Hybrid search utilities (dense + TF-IDF) translated from the notebook."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Tuple

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
    """Fit a TF-IDF vectorizer on the provided corpus and persist it."""
    texts = list(corpus)
    if not texts:
        raise ValueError("Corpus is empty, cannot train TF-IDF vectorizer.")
    vectorizer = TfidfVectorizer(max_features=10000)
    matrix = vectorizer.fit_transform(texts)
    joblib.dump({"vectorizer": vectorizer, "matrix": matrix}, _vectorizer_path(identifier))
    return vectorizer, matrix


def load_tfidf(identifier: str) -> Tuple[TfidfVectorizer, np.ndarray]:
    """Load a previously trained TF-IDF vectorizer and matrix."""
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
) -> pd.DataFrame:
    """Execute the hybrid search strategy from the notebook."""
    if chunks_df.empty:
        return chunks_df.copy()

    chunk_df = chunks_df.reset_index(drop=True).copy()
    chunk_ids = chunk_df["chunk_id"].astype(str).tolist()

    collection = get_collection(collection_name)
    query_embedding = encode_texts([query])
    results = collection.query(query_embeddings=query_embedding, n_results=len(chunk_ids))

    result_ids = (results.get("ids") or [[]])[0]
    result_distances = (results.get("distances") or [[]])[0]
    vec_scores_map = {cid: 1 - dist for cid, dist in zip(result_ids, result_distances)}

    tfidf_scores = cosine_similarity(tfidf_vectorizer.transform([query]), tfidf_matrix).ravel()
    tfidf_scores_map = dict(zip(chunk_ids, tfidf_scores))

    hybrid_scores = []
    for cid in chunk_ids:
        vec_score = vec_scores_map.get(cid, 0.0)
        tfidf_score = tfidf_scores_map.get(cid, 0.0)
        hybrid_scores.append(alpha * vec_score + (1.0 - alpha) * tfidf_score)

    chunk_df["hybrid_score"] = hybrid_scores
    top_indices = np.argsort(chunk_df["hybrid_score"].values)[::-1][:top_k]
    return chunk_df.iloc[top_indices].copy()


def hybrid_search_with_meta(
    collection_name: str,
    chunks_df: pd.DataFrame,
    tfidf_vectorizer: TfidfVectorizer,
    tfidf_matrix,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    alpha: float = HYBRID_ALPHA,
) -> pd.DataFrame:
    """Return notebook-style metadata columns alongside the chunk text."""
    hits = hybrid_search(collection_name, chunks_df, tfidf_vectorizer, tfidf_matrix, query, top_k, alpha)
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
