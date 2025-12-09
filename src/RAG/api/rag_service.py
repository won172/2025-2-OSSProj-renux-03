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
    ingest_staff, # 추가
)
from src.search.hybrid import load_tfidf, hybrid_search_with_meta
from src.services.answer import format_citations
from src.services.langchain_chat import generate_langchain_answer
from src.models.embedding import get_embedder
from src.services.router import route_query
from src.utils.date_parser import extract_date_range_from_query
from src.utils.query_expansion import expand_query

app = FastAPI(
    title="동똑이",
    description="25-2 오픈소스소프트웨어프로젝트 팀 Renux의 동국대학교 캠퍼스 RAG 어시스턴트 API 서비스입니다.",
)

_DATASET_LOADERS = {
    "notices": ingest_notices,
    "rules": ingest_rules,
    "schedule": ingest_schedule,
    "courses": ingest_courses,
    "staff": ingest_staff, # 추가
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
    question: str = Field(..., description="사용자 질문", alias="question")
    session_id: str | None = Field(None, description="대화 세션 ID (없으면 기본 세션)", alias="sessionId")
    major: str | None = Field(None, description="사용자 학과") # 새로 추가

    class Config:
        populate_by_name = True





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
        logging.info("⏳ Warming up embedding model...")
        get_embedder()
        logging.info("✅ Embedding model warmup completed.")
    except Exception as exc:
        logging.warning(f"⚠️ Embedding model warmup failed: {exc}", exc_info=True)



@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest) -> AskResponse:
    raw_query = req.question.strip()
    if not raw_query:
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다.")

    # 쿼리 확장 로직 적용
    query = expand_query(raw_query)
    logging.info(f"Original query: '{raw_query}', Expanded query: '{query}'")

    session_id = req.session_id or str(uuid.uuid4())

    # 로그에 처리된 질문과 세션 ID를 출력하여 디버깅을 돕습니다.
    logging.info(f"session: '{session_id}'")

    user_major = req.major
    
    # --- 날짜 및 학과 필터링 로직 ---
    final_where_filter: Dict = {}
    date_range = await run_in_threadpool(extract_date_range_from_query, query)
    
    # 1. 학과 필터링 (ChromaDB where 절 사용)
    if user_major and user_major != "Default": 
        final_where_filter["major"] = {"$eq": user_major}

    # 로깅 추가 (디버깅 용이)
    logging.info(f"Applying ChromaDB filters: {final_where_filter}")
    
    route = await route_query(query)
    frames: List[pd.DataFrame] = []

    # 각 데이터셋별로 필터를 적용
    for dataset in route:
        try:
            chunks_df, vectorizer, matrix = await run_in_threadpool(_ensure_dataset, dataset)
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=500, detail=f"Dataset '{dataset}' unavailable: {exc}")

        artifacts = DATASET_ARTIFACTS[dataset]
        
        # 데이터셋별로 적용될 필터 조정
        current_dataset_filter = final_where_filter.copy()
        
        # 학과 필터: courses만 지원
        if dataset != "courses":
            current_dataset_filter.pop("major", None)
            
        # 필터가 비어있으면 None으로 설정
        final_filter = current_dataset_filter if current_dataset_filter else None
            
        search_func = functools.partial(
            hybrid_search_with_meta,
            collection_name=artifacts.collection,
            chunks_df=chunks_df,
            tfidf_vectorizer=vectorizer,
            tfidf_matrix=matrix,
            query=query,
            top_k=DEFAULT_TOP_K * 3,
            alpha=HYBRID_ALPHA,
            where_filter=final_filter,
        )
        hits = await run_in_threadpool(search_func)
        
        # 2. 날짜 필터링 (Pandas DataFrame 후처리)
        # ChromaDB의 복합 연산자 제한을 피하기 위해 메모리 상에서 필터링
        if date_range and not hits.empty and dataset in ["notices", "schedule", "rules"]:
            start_date_str = date_range[0].strftime('%Y-%m-%d')
            end_date_str = date_range[1].strftime('%Y-%m-%d')
            
            # 날짜 컬럼 확인 (published_at 또는 updated_at)
            # schedule 데이터셋은 'schedule_start' 등을 사용할 수 있으나 
            # 현재 ingest 로직상 'published_at'에 시작일이 매핑되어 있음.
            if "published_at" in hits.columns:
                # 날짜 형식 변환 및 필터링
                hits["_temp_date"] = pd.to_datetime(hits["published_at"], errors='coerce')
                # NaT(날짜 없음)는 필터링 대상에서 제외할지 포함할지 결정해야 함. 
                # 여기서는 날짜 질문이므로 날짜가 있는 것만 남김.
                hits = hits[
                    (hits["_temp_date"] >= start_date_str) & 
                    (hits["_temp_date"] <= end_date_str)
                ]
                hits.drop(columns=["_temp_date"], inplace=True)
                logging.info(f"Date filtered {dataset}: {len(hits)} remaining")

        logging.info(f"Dataset: {dataset}, Filter: {final_filter}, Hits: {len(hits)}")

        if not hits.empty:
            hits["dataset"] = dataset
            frames.append(hits)
    
    if not frames:
        logging.info("No search results found. Falling back to LLM with empty context.")
        merged = pd.DataFrame()
    else:
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

        # sort_date가 NaT여도 데이터를 버리지 않도록 수정
        merged.dropna(subset=["hybrid_score"], inplace=True)
        
        if not merged.empty:
            min_hybrid = merged["hybrid_score"].min()
            max_hybrid = merged["hybrid_score"].max()
            if max_hybrid > min_hybrid:
                merged["norm_hybrid"] = (merged["hybrid_score"] - min_hybrid) / (max_hybrid - min_hybrid)
            else:
                merged["norm_hybrid"] = 1.0

            # 날짜 점수 계산: 날짜가 있는 행만 계산하고 나머지는 0점 처리
            valid_dates = merged["sort_date"].dropna()
            if not valid_dates.empty:
                min_date = valid_dates.min().timestamp()
                max_date = valid_dates.max().timestamp()
                
                # 날짜가 없는 행은 최하점(min_date)으로 채움
                merged["sort_timestamp"] = merged["sort_date"].apply(lambda x: x.timestamp() if pd.notnull(x) else min_date)
                
                if max_date > min_date:
                    merged["norm_recency"] = (merged["sort_timestamp"] - min_date) / (max_date - min_date)
                else:
                    merged["norm_recency"] = 1.0
            else:
                # 날짜 정보가 아예 없는 데이터셋(예: courses)인 경우 최신성 점수 0 또는 1로 통일 (하이브리드 점수만 반영됨)
                merged["norm_recency"] = 0.0
            
            merged["final_score"] = (1 - RECENCY_WEIGHT) * merged["norm_hybrid"] + RECENCY_WEIGHT * merged["norm_recency"]
            merged.sort_values(by="final_score", ascending=False, inplace=True)
        else:
            merged.sort_values(by="hybrid_score", ascending=False, inplace=True)

    merged = merged.head(DEFAULT_TOP_K).reset_index(drop=True)

    context_parts = []
    for idx, row in merged.iterrows():
        part = f"문서 {idx+1} [출처: {row.get('source', '알 수 없음')}]:\n"
        if row.get('title'):
            part += f"제목: {row.get('title')}\n"
        if row.get('published_at'): # 공지사항, 일정 등 날짜 정보가 있는 경우
            part += f"게시일: {row.get('published_at')}\n"
        if row.get('url'): # URL 정보가 있는 경우
            part += f"URL: {row.get('url')}\n"
        part += f"내용:\n{row['chunk_text']}\n"
        context_parts.append(part)
    
    context_text = "\n\n---\n\n".join(context_parts) if context_parts else "검색된 관련 문서가 없습니다. 일반적인 대화로 응답해주세요."
    context_text = context_text[:MAX_CONTEXT_LENGTH] # 최대 길이 제한 유지 
    # LLM에게 현재 날짜를 전달하여 "오늘", "이번 학기" 등의 표현을 해석하도록 돕습니다.
    from datetime import timedelta, timezone
    KST = timezone(timedelta(hours=9))
    current_date = datetime.now(KST).strftime('%Y년 %m월 %d일')
    answer = await generate_langchain_answer(
        question=query, 
        context=context_text, 
        session_id=session_id, 
        current_date=current_date
    )
    
    # 후처리: 볼드체(**) 서식 강제 제거
    answer = answer.replace("**", "")
    
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
