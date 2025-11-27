import functools
import logging
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Tuple

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.config import (
    DEFAULT_TOP_K,
    HYBRID_ALPHA,
    MAX_CONTEXT_LENGTH,
    RECENCY_WEIGHT,
    VECTORIZER_DIR,
)
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
)
from src.models.embedding import get_embedder
from src.services.router import route_query
from src.utils.date_parser import extract_date_range_from_query

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
    # citations: str
    # route: List[str]
    # sources: List[SourceChunk]


class AskRequest(BaseModel):
    question: str = Field(..., description="사용자 질문")
    session_id: str | None = Field(None, description="대화 세션 ID (없으면 기본 세션)")




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
    """애플리케이션 시작 시 데이터셋과 분류기 등 주요 아티팩트를 미리 로드합니다."""
    logging.basicConfig(level=logging.INFO)
    
    for key in _DATASET_LOADERS:
        try:
            _ensure_dataset(key)
            logging.info(f"✅ Dataset '{key}' successfully loaded.")
        except (KeyError, FileNotFoundError, ValueError) as exc:
            logging.error(f"⚠️ Failed to warmup dataset '{key}': {exc}", exc_info=True)
            # 데이터셋 로드 실패는 심각한 문제일 수 있으므로,
            # 필요에 따라 여기서 애플리케이션을 종료시키는 로직을 추가할 수 있습니다.
            # Ex: raise RuntimeError(f"Critical failure loading dataset {key}") from exc

    try:
        bootstrap_notice_classifier()
        logging.info("✅ Notice classifier bootstrap process completed.")
    except Exception as exc:
        # 이 단계의 실패는 치명적이지 않을 수 있으므로 경고만 로깅합니다.
        logging.warning(f"⚠️ Notice classifier bootstrap failed: {exc}", exc_info=True)

    try:
        logging.info("⏳ Warming up embedding model...")
        get_embedder()
        logging.info("✅ Embedding model warmup completed.")
    except Exception as exc:
        logging.warning(f"⚠️ Embedding model warmup failed: {exc}", exc_info=True)



@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest) -> AskResponse:
    query = req.question.strip()
    if not query:
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다.")

    session_id = req.session_id or str(uuid.uuid4())
    logging.info(f"Received question: '{query}' for session: '{session_id}'") 
    route = await route_query(query)
    frames: List[pd.DataFrame] = []

    date_range = await run_in_threadpool(extract_date_range_from_query, query)
    where_filter: Dict | None = None
    if date_range:
        start_date_str = date_range[0].strftime('%Y-%m-%d')
        end_date_str = date_range[1].strftime('%Y-%m-%d')
        
        _filter = {}
        if "notices" in route or "schedule" in route:
            _filter["published_at"] = {"$gte": start_date_str, "$lte": end_date_str}
        elif "rules" in route:
            _filter["updated_at"] = {"$gte": start_date_str, "$lte": end_date_str}
        
        if _filter:
            where_filter = _filter

    for dataset in route:
        try:
            chunks_df, vectorizer, matrix = await run_in_threadpool(_ensure_dataset, dataset)
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=500, detail=f"Dataset '{dataset}' unavailable: {exc}")

        artifacts = DATASET_ARTIFACTS[dataset]
        
        current_where_filter = where_filter if dataset in ["notices", "rules", "schedule"] else None
        
        search_func = functools.partial(
            hybrid_search_with_meta,
            collection_name=artifacts.collection,
            chunks_df=chunks_df,
            tfidf_vectorizer=vectorizer,
            tfidf_matrix=matrix,
            query=query,
            top_k=DEFAULT_TOP_K * 3,
            alpha=HYBRID_ALPHA,
            where_filter=current_where_filter,
        )
        hits = await run_in_threadpool(search_func)

        if not hits.empty:
            hits["dataset"] = dataset
            frames.append(hits)
    
    if not frames:
        # 이 부분은 AskResponse가 아닌 일반 dict를 반환하면 FastAPI에서 자동으로 JSON으로 변환해줍니다.
        # 하지만, 일관성을 위해 AskResponse를 사용하되, 일부 필드는 비워둡니다.
        return AskResponse(answer="관련 정보를 찾지 못했습니다.", citations="", route=route, sources=[])

    merged = pd.concat(frames, ignore_index=True)
    
    if not merged.empty and "hybrid_score" in merged.columns:
        if "published_at" in merged.columns and "updated_at" in merged.columns:
             merged["sort_date"] = pd.to_datetime(merged["published_at"].fillna(merged["updated_at"]), errors='coerce')
        elif "published_at" in merged.columns:
            merged["sort_date"] = pd.to_datetime(merged["published_at"], errors='coerce')
        elif "updated_at" in merged.columns:
            merged["sort_date"] = pd.to_datetime(merged["updated_at"], errors='coerce')
        else:
            merged["sort_date"] = pd.NaT

        merged.dropna(subset=["hybrid_score", "sort_date"], inplace=True)
        if not merged.empty:
            min_hybrid = merged["hybrid_score"].min()
            max_hybrid = merged["hybrid_score"].max()
            if max_hybrid > min_hybrid:
                merged["norm_hybrid"] = (merged["hybrid_score"] - min_hybrid) / (max_hybrid - min_hybrid)
            else:
                merged["norm_hybrid"] = 1.0

            min_date = merged["sort_date"].min().timestamp()
            max_date = merged["sort_date"].max().timestamp()
            if max_date > min_date:
                merged["norm_recency"] = (merged["sort_date"].apply(lambda x: x.timestamp()) - min_date) / (max_date - min_date)
            else:
                merged["norm_recency"] = 1.0
            
            merged["final_score"] = (1 - RECENCY_WEIGHT) * merged["norm_hybrid"] + RECENCY_WEIGHT * merged["norm_recency"]
            merged.sort_values(by="final_score", ascending=False, inplace=True)
        else:
            merged.sort_values(by="hybrid_score", ascending=False, inplace=True)

    merged = merged.head(DEFAULT_TOP_K).reset_index(drop=True)

    context_text = "\n\n---\n\n".join(merged["chunk_text"].tolist())
    context_text = context_text[:MAX_CONTEXT_LENGTH]
    answer = await run_in_threadpool(generate_langchain_answer, query, context_text, session_id=session_id)
    
    citations_raw = await run_in_threadpool(format_citations, merged)
    citations = re.sub(r'<[^>]+>', '', citations_raw)

    sources = [
        SourceChunk(
            source=row.get("dataset", ""),
            metadata={col: row.get(col) for col in row.index if col not in {"chunk_text", "dataset", "title", "hybrid_score", "sort_date", "norm_hybrid", "norm_recency", "final_score"}},
            snippet=row.get("chunk_text", ""),
        )
        for _, row in merged.iterrows()
    ]

    return AskResponse(answer=answer, citations=citations, route=route, sources=sources)


@app.get("/health")
def health() -> dict:
    status = {}
    for key in _DATASET_LOADERS:
        cache = _datasets.get(key)
        status[key] = 0 if cache is None else len(cache.chunks)
    return {"status": "ok", "datasets": status}
