"""Compare every Chroma writer's payload with its former inline construction."""
from __future__ import annotations

import pandas as pd
import pytest

from src.pipelines import ingest
from src.services import notices_dense_rebuild, staged_dense_rebuild
from src.services.retrieval_context import enrich_retrieval_fields


def _frame(dataset: str) -> pd.DataFrame:
    common = {
        "chunk_id": f"{dataset}-1",
        "doc_id": f"{dataset}:1",
        "document_key": f"{dataset}:1",
        "source": dataset,
        "url": f"https://www.dongguk.edu/{dataset}/1",
        "extra_none": None,
        "extra_list": ["공식", "자료"],
        # Old direct writers retained these keys and overwrote their values.
        "embedding_input_hash": "stale-hash",
        "embedding_input_field": "chunk_text",
        "retrieval_context": "stale context",
        "retrieval_text": "stale text",
    }
    details = {
        "notices": {
            "chunk_text": "[2026 장학 공지]\n신청 기간을 확인하세요.",
            "title": "2026 장학 공지",
            "category": "장학",
            "department": "장학지원팀",
            "visibility": "department",
            "published_at": "2026-09-01",
            "apply_deadline": "2026-09-30",
        },
        "courses": {
            "chunk_text": "[자료구조]\n2026학년도 2학기 교과목 안내",
            "title": "자료구조",
            "department": "컴퓨터공학과",
            "academic_year": 2026,
            "semester": 2,
        },
        "rules": {
            "chunk_text": "[학칙]\n제1조 목적과 적용 범위",
            "title": "학칙",
            "effective_date": "2026-03-01",
            "article": "제1조",
        },
    }
    base_row = {**common, **details[dataset]}
    rows = [base_row]
    missing_metadata_columns = {
        "notices": ("department", "published_at", "apply_deadline"),
        "courses": ("department", "academic_year", "semester"),
        "rules": ("effective_date", "article", "effective_date"),
    }
    for number, (missing, column) in enumerate(
        zip((float("nan"), pd.NA, None), missing_metadata_columns[dataset]), start=2
    ):
        rows.append({
            **base_row,
            "chunk_id": f"{dataset}-{number}",
            "doc_id": f"{dataset}:{number}",
            "document_key": f"{dataset}:{number}",
            "chunk_text": missing,
            column: missing,
        })
    return pd.DataFrame(rows, dtype=object)


def _old_direct_payload(frame: pd.DataFrame) -> tuple[list[str], list[dict]]:
    # notices_sync._upsert_notice_chunks and rag_service._index_pending_item.
    enriched = enrich_retrieval_fields(frame)
    texts = enriched["retrieval_text"].fillna("").astype(str).tolist()
    metadatas = enriched.drop(columns=["chunk_text", "retrieval_text"], errors="ignore").to_dict(orient="records")
    metadatas = [{key: (value if value is not None else "") for key, value in row.items()} for row in metadatas]
    for metadata, text in zip(metadatas, texts):
        metadata[ingest.EMBEDDING_INPUT_HASH_COLUMN] = ingest._embedding_input_hash(text)
        metadata[ingest.EMBEDDING_INPUT_FIELD_COLUMN] = "retrieval_text"
    return texts, metadatas


def _old_live_payload(frame: pd.DataFrame) -> tuple[list[str], list[dict]]:
    # _persist_chunks_unlocked receives a frame enriched after dropping old stamps.
    texts = frame["retrieval_text"].fillna("").astype(str).tolist()
    metadatas = frame.drop(columns=["chunk_text", "retrieval_text"], errors="ignore").to_dict(orient="records")
    metadatas = [{key: (value if value is not None else "") for key, value in row.items()} for row in metadatas]
    hashes = [ingest._embedding_input_hash(text) for text in texts]
    for metadata, digest in zip(metadatas, hashes):
        metadata[ingest.EMBEDDING_INPUT_HASH_COLUMN] = digest
        metadata[ingest.EMBEDDING_INPUT_FIELD_COLUMN] = "retrieval_text"
    return texts, metadatas


def _old_rebuild_payload(frame: pd.DataFrame, metadata_value) -> tuple[list[str], list[dict]]:
    # Both dense rebuild _batch_payload implementations used this sequence.
    frame = ingest.with_embedding_input(frame)
    documents = frame[ingest.EMBEDDING_INPUT_FIELD].astype(str).tolist()
    metadata_frame = frame.drop(
        columns=["chunk_text", ingest.EMBEDDING_INPUT_FIELD,
                 ingest.EMBEDDING_INPUT_HASH_COLUMN, ingest.EMBEDDING_INPUT_FIELD_COLUMN],
        errors="ignore",
    )
    metadatas = [
        {str(key): metadata_value(value) for key, value in row.items()}
        for row in metadata_frame.to_dict(orient="records")
    ]
    for document, metadata in zip(documents, metadatas):
        metadata[ingest.EMBEDDING_INPUT_FIELD_COLUMN] = ingest.EMBEDDING_INPUT_FIELD
        metadata[ingest.EMBEDDING_INPUT_HASH_COLUMN] = ingest._embedding_input_hash(
            document, field=ingest.EMBEDDING_INPUT_FIELD
        )
    return documents, metadatas


@pytest.mark.parametrize("dataset", ["notices", "courses", "rules"])
@pytest.mark.parametrize("path", ["notice_sync", "admin", "live", "notices_rebuild", "staged_rebuild"])
def test_chroma_embedding_payload_matches_old_inline_logic(path: str, dataset: str) -> None:
    frame = _frame(dataset)
    ids = frame["chunk_id"].astype(str).tolist()
    if path in {"notice_sync", "admin"}:
        expected = _old_direct_payload(frame)
        actual = ingest.chroma_embedding_payload(frame)
    elif path == "live":
        prepared = enrich_retrieval_fields(frame.drop(
            columns=[ingest.EMBEDDING_INPUT_HASH_COLUMN, ingest.EMBEDDING_INPUT_FIELD_COLUMN],
            errors="ignore",
        ))
        # The prepared live frame can contain missing retrieval_text; its old
        # writer used fillna(""), unlike the rebuild writers' astype(str).
        for index, missing in enumerate((float("nan"), pd.NA, None), start=1):
            prepared.at[index, "retrieval_text"] = missing
        prepared["corpus_revision"] = "test-revision"
        expected = _old_live_payload(prepared)
        actual = ingest.chroma_embedding_payload(prepared, preparation="prepared")
        assert expected[0][1:] == ["", "", ""]
    else:
        rebuild = notices_dense_rebuild if path == "notices_rebuild" else staged_dense_rebuild
        expected = _old_rebuild_payload(frame, rebuild._metadata_value)
        actual_ids, documents, metadatas = rebuild._batch_payload(frame)
        assert actual_ids == ids
        actual = documents, metadatas

    assert actual[0] == expected[0]
    assert actual[1] == expected[1]
    assert [list(row.items()) for row in actual[1]] == [list(row.items()) for row in expected[1]]
    assert len(actual[0]) == len(ids)
    assert all(metadata[ingest.EMBEDDING_INPUT_HASH_COLUMN] == ingest._embedding_input_hash(text)
               for text, metadata in zip(*actual))
