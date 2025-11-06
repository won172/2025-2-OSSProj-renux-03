"""Utilities to merge newly crawled notices and update downstream artifacts."""
from __future__ import annotations

from typing import Tuple, Any

import json

import pandas as pd

from src.config import DATA_SOURCES
from src.models.embedding import encode_texts
from src.pipelines.ingest import DATASET_ARTIFACTS, build_notice_chunks
from src.search.hybrid import train_tfidf
from src.vectorstore.chroma_client import add_items

NOTICE_CSV_PATH = DATA_SOURCES["notices"]


def load_existing_notices() -> pd.DataFrame:
    if NOTICE_CSV_PATH.exists():
        return pd.read_csv(NOTICE_CSV_PATH)
    columns = ["게시판", "제목", "카테고리", "게시일", "상단고정", "상세URL", "본문", "첨부파일"]
    return pd.DataFrame(columns=columns)


def merge_notices(existing: pd.DataFrame, incoming: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if incoming.empty:
        return existing, incoming

    combined = pd.concat([existing, incoming], ignore_index=True)
    combined.drop_duplicates(subset=["상세URL"], inplace=True, keep="first")
    combined["게시일"] = pd.to_datetime(combined["게시일"], errors="coerce")
    combined.sort_values(by=["게시일", "제목"], ascending=[False, True], inplace=True)
    combined["게시일"] = combined["게시일"].dt.strftime("%Y-%m-%d")
    combined["게시일"] = combined["게시일"].fillna("")
    combined.reset_index(drop=True, inplace=True)

    if existing.empty:
        new_rows = combined
    else:
        new_mask = ~combined["상세URL"].isin(existing["상세URL"])
        new_rows = combined[new_mask].copy()
    return combined, new_rows


def save_notices(df: pd.DataFrame) -> None:
    df.to_csv(NOTICE_CSV_PATH, index=False, encoding="utf-8-sig")


def append_chunks(new_rows: pd.DataFrame) -> pd.DataFrame:
    """Build chunks for the provided rows and return them."""
    if new_rows.empty:
        return pd.DataFrame()
    if "첨부파일" in new_rows.columns:
        new_rows["첨부파일"] = new_rows["첨부파일"].apply(_serialize_metadata)
    return build_notice_chunks(new_rows.copy())


def _serialize_metadata(value: Any) -> str:
    if isinstance(value, (list, dict)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return str(value)
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value)


def update_chroma(new_chunks: pd.DataFrame) -> None:
    if new_chunks.empty:
        return
    embeddings = encode_texts(new_chunks["chunk_text"].tolist())
    add_items(
        DATASET_ARTIFACTS["notices"].collection,
        ids=new_chunks["chunk_id"],
        documents=new_chunks["chunk_text"],
        metadatas=new_chunks.drop(columns=["chunk_text"]).to_dict(orient="records"),
        embeddings=embeddings,
    )


def update_chunk_artifact(new_chunks: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    artifacts = DATASET_ARTIFACTS["notices"]
    chunk_path = artifacts.chunk_path
    csv_path = artifacts.csv_path

    if not chunk_path.exists() and csv_path.exists():
        artifacts.chunk_path = csv_path
        chunk_path = csv_path

    if chunk_path.exists():
        if chunk_path.suffix == ".csv":
            existing_chunks = pd.read_csv(chunk_path)
        else:
            existing_chunks = pd.read_parquet(chunk_path)
    else:
        existing_chunks = pd.DataFrame()

    if new_chunks.empty:
        return existing_chunks, pd.DataFrame()

    if not existing_chunks.empty:
        filtered_new = new_chunks[~new_chunks["chunk_id"].isin(existing_chunks["chunk_id"])].copy()
    else:
        filtered_new = new_chunks.copy()

    if existing_chunks.empty:
        combined = filtered_new.copy()
    else:
        combined = pd.concat([existing_chunks, filtered_new], ignore_index=True)

    combined.drop_duplicates(subset=["chunk_id"], inplace=True)

    try:
        if chunk_path.suffix == ".csv":
            raise ImportError  # force fallback
        combined.to_parquet(chunk_path, index=False)
    except (ImportError, ModuleNotFoundError, ValueError, OSError):
        fallback_path = chunk_path.with_suffix(".csv")
        combined.to_csv(fallback_path, index=False, encoding="utf-8-sig")
        artifacts.chunk_path = fallback_path
        chunk_path = fallback_path

    return combined, filtered_new

def retrain_tfidf(chunks_df: pd.DataFrame) -> None:
    if chunks_df.empty:
        return
    train_tfidf("notices", chunks_df["chunk_text"].tolist())


def sync_notices(incoming: pd.DataFrame) -> int:
    """Merge *incoming* notices, update CSV/Chroma/TF-IDF, and return # of new rows."""
    existing = load_existing_notices()
    merged, new_rows = merge_notices(existing, incoming)
    if new_rows.empty:
        save_notices(merged)  # ensure ordering/encoding stays consistent
        return 0

    save_notices(merged)
    new_chunks = append_chunks(new_rows)
    all_chunks, filtered_new = update_chunk_artifact(new_chunks)
    update_chroma(filtered_new)
    retrain_tfidf(all_chunks)
    return len(new_rows)


__all__ = ["sync_notices", "merge_notices", "load_existing_notices"]
