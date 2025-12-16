import functools
import logging
import re
import sys
import uuid
import json
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Tuple

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pandas as pd
from scipy.sparse import vstack
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.config import (
    DEFAULT_TOP_K,
    HYBRID_ALPHA,
    MAX_CONTEXT_LENGTH,
    RECENCY_WEIGHT,
    VECTORIZER_DIR,
)
from src.database import SessionLocal, PendingItem, CustomKnowledge, Chunk, Notice, Schedule, init_db
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
from src.models.embedding import get_embedder, encode_texts
from src.services.router import route_query
from src.utils.date_parser import extract_date_range_from_query
from src.utils.query_expansion import expand_query
from src.utils.preprocess import make_doc_id
from src.vectorstore.chroma_client import upsert_items

app = FastAPI(
    title="동똑이",
    description="25-2 오픈소스소프트웨어프로젝트 팀 Renux의 동국대학교 캠퍼스 RAG 어시스턴트 API 서비스입니다.",
)

@app.get("/notifications")
async def notifications_dummy():
    return []

@app.options("/notifications")
async def notifications_options_dummy():
    return {}

@app.options("/token")
async def token_options_dummy():
    return {}

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


class SubmitRequest(BaseModel):
    source_type: str
    data: str




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


def _add_to_dataset_cache(key: str, doc_id: str, text: str, metadata: Dict) -> None:
    """캐시된 데이터셋에 새 항목을 점진적으로 추가합니다 (전체 리로드 방지)."""
    if key not in _datasets:
        # 캐시에 없으면 로드 (이 시점에 로드하는 것은 어쩔 수 없음, 하지만 이후에는 캐시됨)
        _ensure_dataset(key)
    
    cache = _datasets[key]
    
    # 1. DataFrame에 행 추가
    new_row = metadata.copy()
    new_row["chunk_id"] = doc_id
    new_row["chunk_text"] = text
    # ensure all columns exist
    for col in cache.chunks.columns:
        if col not in new_row:
            new_row[col] = None
            
    # pd.concat is better than append
    new_df = pd.DataFrame([new_row])
    # 기존 컬럼 순서 유지를 위해 reindex
    new_df = new_df.reindex(columns=cache.chunks.columns)
    
    cache.chunks = pd.concat([cache.chunks, new_df], ignore_index=True)
    
    # 2. TF-IDF 매트릭스 업데이트 (기존 어휘 사전 사용)
    # 신규 단어는 반영되지 않지만, 전체 리로드보다 월등히 빠름
    new_vec = cache.vectorizer.transform([text])
    cache.matrix = vstack([cache.matrix, new_vec])
    
    logging.info(f"⚡ Incremental update for '{key}': Added 1 item. New size: {len(cache.chunks)}")


@app.on_event("startup")
def bootstrap_artifacts() -> None:
    """애플리케이션 시작 시 데이터셋과 분류기 등 주요 아티팩트를 미리 로드합니다."""
    logging.basicConfig(level=logging.INFO)
    
    # Ensure DB tables exist
    try:
        init_db()
        logging.info("✅ Database tables initialized.")
    except Exception as e:
        logging.error(f"❌ Failed to initialize database: {e}")
    
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



@app.post("/admin/submit")
async def submit_pending(req: SubmitRequest):
    session = SessionLocal()
    try:
        item = PendingItem(
            source_type=req.source_type,
            data=req.data,
            status="pending"
        )
        session.add(item)
        session.commit()
        return {"status": "ok", "id": item.id}
    finally:
        session.close()


@app.get("/admin/pending")
async def list_pending():
    session = SessionLocal()
    try:
        items = session.query(PendingItem).filter(PendingItem.status == "pending").all()
        return items
    finally:
        session.close()


@app.get("/admin/items")
async def list_all_items():
    session = SessionLocal()
    try:
        items = session.query(PendingItem).order_by(PendingItem.created_at.desc()).all()
        logging.info(f"📋 [Admin] Listed {len(items)} items.")
        return items
    except Exception as e:
        logging.error(f"❌ [Admin] Failed to list items: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()



@app.post("/admin/approve/{item_id}")
async def approve_pending(item_id: int):
    session = SessionLocal()
    try:
        logging.info(f"👉 [Admin] Approving item ID: {item_id}")
        item = session.query(PendingItem).filter(PendingItem.id == item_id).first()
        if not item:
            logging.error(f"❌ [Admin] Item not found: {item_id}")
            return {"status": "error", "message": "Item not found"}

        data = json.loads(item.data)
        
        # 공통 Notice 객체 생성 준비
        notice = None
        
        if item.source_type == "custom_knowledge":
            logging.info(f"📝 [Admin] Processing custom knowledge: {data.get('question')}")
            
            notice = Notice(
                board=data.get("category", "기타"), # e.g. 학과정보
                title=data.get("question"),
                category="FAQ",
                published_date=datetime.now().strftime("%Y-%m-%d"),
                content=data.get("answer"),
                is_manual=1
            )

        elif item.source_type == "event":
            logging.info(f"📅 [Admin] Processing event: {data.get('title')}")
            
            # 내용을 상세하게 구성
            content_parts = []
            if data.get("description"):
                content_parts.append(data.get("description"))
            
            date_str = f"일시: {data.get('start_date')}"
            if data.get("end_date") and data.get("end_date") != data.get("start_date"):
                date_str += f" ~ {data.get('end_date')}"
            content_parts.append(date_str)
            
            if data.get("location"):
                content_parts.append(f"장소: {data.get('location')}")
                
            full_content = "\n\n".join(content_parts)

            notice = Notice(
                board=data.get("department", "학과행사"),
                title=data.get("title"),
                category="행사",
                published_date=data.get("start_date"),
                content=full_content,
                is_manual=1
            )

        elif item.source_type == "announcement":
            logging.info(f"📢 [Admin] Processing announcement: {data.get('title')}")
            
            notice = Notice(
                board=data.get("department", "공지사항"),
                title=data.get("title"),
                category=data.get("category", "일반"),
                published_date=data.get("date"),
                content=data.get("content"),
                is_manual=1
            )
        
        if notice:
            # 1. Save to DB (Notices table)
            session.add(notice)
            session.commit()
            logging.info(f"✅ [Admin] Notice saved to DB. ID: {notice.id}")

            # 2. Create Chunk
            doc_id = make_doc_id(notice.title, notice.board, notice.published_date)

            # Check for collision
            existing_chunk = session.query(Chunk).filter(Chunk.chunk_id == doc_id).first()
            if existing_chunk:
                logging.warning(f"⚠️ [Admin] Chunk ID collision for {doc_id}. Appending random UUID.")
                doc_id = f"{doc_id}_{uuid.uuid4().hex[:8]}"
            
            text_content = notice.content
            prefix_parts = []
            if notice.board:
                prefix_parts.append(f"게시판: {notice.board}")
            if notice.category:
                prefix_parts.append(f"분류: {notice.category}")
            if notice.published_date:
                prefix_parts.append(f"게시일: {notice.published_date}")
            
            if prefix_parts:
                text_content = f"[{', '.join(prefix_parts)}]\n\n{text_content}"

            chunk = Chunk(
                chunk_id=doc_id,
                chunk_text=text_content,
                notice_id=notice.id
            )
            session.add(chunk)
            session.commit()

            # 3. Upsert to Chroma (dongguk_notices)
            target_collection = "dongguk_notices"
            embedding = encode_texts([text_content])
            metadata = {
                "source": "notices",
                "title": notice.title,
                "topics": notice.board,
                "published_at": notice.published_date,
                "category": notice.category
            }
            metadata = {k: (v if v is not None else "") for k, v in metadata.items()}
            
            upsert_items(
                name=target_collection,
                ids=[doc_id],
                documents=[text_content],
                metadatas=[metadata],
                embeddings=embedding
            )
            logging.info(f"✅ [Admin] Upserted to ChromaDB (Notice)")

            # 4. Trigger reload
            try:
                if "notices" in _datasets:
                    del _datasets["notices"]
                _ensure_dataset("notices")
                logging.info(f"✅ [Admin] Reloaded notices dataset.")
            except Exception as e:
                logging.error(f"❌ [Admin] Failed to reload notices: {e}")

            item.status = "approved"
            session.commit()
            return {"status": "approved", "chunk_id": doc_id}

        else:
             item.status = "approved_manually" 
             session.commit()
             return {"status": "approved_manually"}

    except Exception as e:
        session.rollback()
        logging.error(f"🔥 [Admin] Critical Error in approve_pending: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        session.close()


@app.post("/admin/reject/{item_id}")
async def reject_pending(item_id: int):
    session = SessionLocal()
    try:
        item = session.query(PendingItem).filter(PendingItem.id == item_id).first()
        if item:
            item.status = "rejected"
            session.commit()
        return {"status": "rejected"}
    finally:
        session.close()


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
    current_date = datetime.now(KST).strftime('%Y년 %m월 %d일 %H시 %M분 (KST)')
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
