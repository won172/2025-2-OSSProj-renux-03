"""공통 증분 동기화 유틸리티.

공지 외 데이터셋(규정/일정/교과)도 신규 데이터프레임을 받아
Chroma와 TF-IDF를 증분 갱신할 수 있도록 한다.
"""
from __future__ import annotations

from typing import Callable, Dict, Tuple

import pandas as pd

from src.models.embedding import encode_texts
from src.pipelines.ingest import (
    DATASET_ARTIFACTS,
    build_notice_chunks,
    build_rule_chunks,
    build_schedule_chunks,
    build_course_chunks,
)
from src.search.hybrid import train_tfidf
from src.vectorstore.chroma_client import add_items


BUILDER_MAP: Dict[str, Callable[[pd.DataFrame], pd.DataFrame]] = {
    "notices": build_notice_chunks,
    "rules": build_rule_chunks,
    "schedule": build_schedule_chunks,
    "courses": build_course_chunks,
}


def _load_existing_chunks(key: str) -> pd.DataFrame:
    artifacts = DATASET_ARTIFACTS[key]
    path = artifacts.chunk_path if artifacts.chunk_path.exists() else artifacts.csv_path
    if not path.exists():
        return pd.DataFrame()
    if path.suffix == ".csv":
        return pd.read_csv(path)
    return pd.read_parquet(path)


def _append_to_chroma(key: str, new_chunks: pd.DataFrame) -> None:
    if new_chunks.empty:
        return
    collection = DATASET_ARTIFACTS[key].collection
    embeddings = encode_texts(new_chunks["chunk_text"].tolist())
    add_items(
        collection,
        ids=new_chunks["chunk_id"],
        documents=new_chunks["chunk_text"],
        metadatas=new_chunks.drop(columns=["chunk_text"]).to_dict(orient="records"),
        embeddings=embeddings,
    )


def sync_dataset(key: str, incoming_df: pd.DataFrame) -> int:
    """새 데이터프레임을 받아 기존 청크와 병합 후 Chroma/TF-IDF를 갱신한다.

    반환값은 신규로 추가된 행(청크) 개수.
    """
    key = key.lower()
    if key not in BUILDER_MAP:
        raise ValueError(f"Unsupported dataset '{key}'")

    builder = BUILDER_MAP[key]
    existing_chunks = _load_existing_chunks(key)

    new_chunks = builder(incoming_df)

    if new_chunks.empty:
        return 0

    if not existing_chunks.empty:
        filtered_new = new_chunks[~new_chunks["chunk_id"].isin(existing_chunks["chunk_id"])].copy()
    else:
        filtered_new = new_chunks.copy()

    if filtered_new.empty:
        return 0

    combined = pd.concat([existing_chunks, filtered_new], ignore_index=True)
    combined.drop_duplicates(subset=["chunk_id"], inplace=True)

    artifacts = DATASET_ARTIFACTS[key]
    artifacts.chunk_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        combined.to_parquet(artifacts.chunk_path, index=False)
    except Exception:
        combined.to_csv(artifacts.csv_path, index=False, encoding="utf-8-sig")
        artifacts.chunk_path = artifacts.csv_path

    _append_to_chroma(key, filtered_new)
    train_tfidf(key, combined["chunk_text"].tolist())
    return len(filtered_new)


__all__ = ["sync_dataset"]
