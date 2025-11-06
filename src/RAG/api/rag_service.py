"""FastAPI service exposing the campus assistant API."""
from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Tuple

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.config import DEFAULT_TOP_K, HYBRID_ALPHA, VECTORIZER_DIR
from src.pipelines.ingest import (
    DATASET_ARTIFACTS,
    ingest_courses,
    ingest_notices,
    ingest_rules,
    ingest_schedule,
)
from src.search.hybrid import load_tfidf, hybrid_search_with_meta
from src.services.answer import format_citations
from src.services.langchain_chat import generate_langchain_answer
from src.services.notice_classifier import (
    bootstrap_notice_classifier,
    classify_notice_query,
    prioritize_notice_hits,
)
from src.services.router import bootstrap_router, route_query

app = FastAPI(
    title="동똑이",
    description="25-2 오픈소스소프트웨어프로젝트 팀 Renux의 동국대학교 캠퍼스 RAG 어시스턴트 API 서비스입니다.",
)

_DATASET_LOADERS = {
    "notices": ingest_notices,
    "rules": ingest_rules,
    "schedule": ingest_schedule,
    "courses": ingest_courses,
}

@dataclass
class DatasetCache:
    chunks: pd.DataFrame
    vectorizer: object
    matrix: object
    chunk_path: Path
    chunk_mtime: float
    tfidf_mtime: float


_datasets: Dict[str, DatasetCache] = {}


class SourceChunk(BaseModel):
    source: str
    metadata: Dict
    snippet: str


class AskResponse(BaseModel):
    answer: str
    citations: str
    route: List[str]
    sources: List[SourceChunk]


class AskRequest(BaseModel):
    question: str = Field(..., description="사용자 질문")
    # session_id: str | None = Field(None, description="대화 세션 ID (없으면 기본 세션)")




def _ensure_dataset(key: str) -> Tuple[pd.DataFrame, object, object]:
    artifacts = DATASET_ARTIFACTS.get(key)
    if artifacts is None:
        raise KeyError(f"Unsupported dataset '{key}'")
    

    chunk_path = artifacts.chunk_path
    csv_path = artifacts.csv_path
    vectorizer_path = VECTORIZER_DIR / f"{key}_tfidf.pkl"

    if not chunk_path.exists() and csv_path.exists():
        artifacts.chunk_path = csv_path
        chunk_path = csv_path

    chunk_mtime = chunk_path.stat().st_mtime if chunk_path.exists() else -1.0
    vectorizer_mtime = vectorizer_path.stat().st_mtime if vectorizer_path.exists() else -1.0

    cache = _datasets.get(key)
    if cache and cache.chunk_path == chunk_path and cache.chunk_mtime == chunk_mtime and cache.tfidf_mtime == vectorizer_mtime:
        return cache.chunks, cache.vectorizer, cache.matrix

    try:
        if chunk_path.exists() and vectorizer_path.exists():
            if chunk_path.suffix == ".csv":
                chunks_df = pd.read_csv(chunk_path)
            else:
                chunks_df = pd.read_parquet(chunk_path)
            vectorizer, matrix = load_tfidf(key)
        else:
            chunks_df, vectorizer, matrix = _DATASET_LOADERS[key]()
            chunk_path = DATASET_ARTIFACTS[key].chunk_path
            chunk_mtime = chunk_path.stat().st_mtime if chunk_path.exists() else -1.0
            vectorizer_mtime = (VECTORIZER_DIR / f"{key}_tfidf.pkl").stat().st_mtime if (VECTORIZER_DIR / f"{key}_tfidf.pkl").exists() else -1.0
    except FileNotFoundError:
        chunks_df, vectorizer, matrix = _DATASET_LOADERS[key]()
        chunk_path = DATASET_ARTIFACTS[key].chunk_path
        chunk_mtime = chunk_path.stat().st_mtime if chunk_path.exists() else -1.0
        vectorizer_path = VECTORIZER_DIR / f"{key}_tfidf.pkl"
        vectorizer_mtime = vectorizer_path.stat().st_mtime if vectorizer_path.exists() else -1.0

    _datasets[key] = DatasetCache(
        chunks=chunks_df,
        vectorizer=vectorizer,
        matrix=matrix,
        chunk_path=chunk_path,
        chunk_mtime=chunk_mtime,
        tfidf_mtime=vectorizer_mtime,
    )
    return chunks_df, vectorizer, matrix


@app.on_event("startup")
def bootstrap_artifacts() -> None:
    for key in _DATASET_LOADERS:
        try:
            _ensure_dataset(key)
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ Failed to warmup dataset '{key}': {exc}")
    try:
        bootstrap_router()
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ Router bootstrap failed: {exc}")
    try:
        bootstrap_notice_classifier()
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ Notice classifier bootstrap failed: {exc}")



@app.post("/ask", response_model=str)
def ask(req: AskRequest) -> AskResponse:
    query = req.question.strip()
    if not query:
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다.")

    route = route_query(query)
    notice_category = None
    if "notices" in route:
        notice_category = classify_notice_query(query)
    frames: List[pd.DataFrame] = []

    for dataset in route:
        try:
            chunks_df, vectorizer, matrix = _ensure_dataset(dataset)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Dataset '{dataset}' unavailable: {exc}")

        artifacts = DATASET_ARTIFACTS[dataset]
        hits = hybrid_search_with_meta(
            artifacts.collection,
            chunks_df,
            vectorizer,
            matrix,
            query,
            top_k=DEFAULT_TOP_K,
            alpha=HYBRID_ALPHA,
        )
        if not hits.empty:
            hits["dataset"] = dataset
            if dataset == "notices":
                hits = prioritize_notice_hits(hits, notice_category)
            frames.append(hits)

    if not frames:
        return AskResponse(answer="관련 정보를 찾지 못했습니다.", route=route, sources=[])

    merged = pd.concat(frames, ignore_index=True)
    if "hybrid_score" in merged.columns:
        merged.sort_values(by="hybrid_score", ascending=False, inplace=True)
    merged = merged.head(DEFAULT_TOP_K).reset_index(drop=True)

    context_text = "\n\n---\n\n".join(merged["chunk_text"].tolist())
    context_text = context_text[:8000]
    answer = generate_langchain_answer(query, context_text, session_id="123")
    citations = format_citations(merged)
    sources = [
        SourceChunk(
            source=row.get("dataset", ""),
            metadata={col: row.get(col) for col in row.index if col not in {"chunk_text", "dataset", "title", "hybrid_score"}},
            snippet=row.get("chunk_text", "")[:300],
        )
        for _, row in merged.iterrows()
    ]
    print(query)
    print(answer)
    return answer
    # return AskResponse(answer=answer, citations=citations, route=route, sources=sources)


@app.get("/health")
def health() -> dict:
    status = {}
    for key in _DATASET_LOADERS:
        cache = _datasets.get(key)
        status[key] = 0 if cache is None else len(cache.chunks)
    return {"status": "ok", "datasets": status}


__all__ = ["app"]
