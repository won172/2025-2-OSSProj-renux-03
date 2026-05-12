import csv
import functools
import io
from importlib.metadata import PackageNotFoundError, version
import logging
import math
import re
import sys
import time
import uuid
import json
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Tuple

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pandas as pd
from scipy.sparse import vstack
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func
from starlette.concurrency import run_in_threadpool
from sklearn import __version__ as sklearn_version

from src.config import (
    DEFAULT_TOP_K,
    HYBRID_ALPHA,
    MAX_CONTEXT_LENGTH,
    MIN_RETRIEVAL_SCORE,
    QUERY_ANALYSIS_MAX_QUERIES,
    RECENCY_DECAY_DAYS_BY_DATASET,
    RECENCY_WEIGHT,
    USE_QUERY_ANALYSIS,
    VECTORIZER_DIR,
)
from src.database import (
    SessionLocal,
    PendingItem,
    CustomKnowledge,
    Chunk,
    DocumentQualityCheck,
    IngestionRun,
    Notice,
    Schedule,
    RagQueryLog,
    RagRetrievalLog,
    SourceDocument,
    init_db,
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
from src.search.hybrid import read_tfidf_metadata
from src.services.answer import format_citations
from src.services.langchain_chat import append_manual_history, generate_langchain_answer, generate_smalltalk_answer
from src.services.query_analysis import QueryAnalysisResult, analyze_query
from src.models.embedding import get_embedder, encode_texts
from src.services.router import route_query
from src.utils.date_parser import QueryDateFilter, extract_date_filter_from_query
from src.utils.query_expansion import expand_query
from src.utils.preprocess import make_doc_id
from src.vectorstore.chroma_client import count_items, upsert_items

app = FastAPI(
    title="동똑이",
    description="25-2 오픈소스소프트웨어프로젝트 팀 Renux의 동국대학교 캠퍼스 RAG 어시스턴트 API 서비스입니다.",
)


def _log_event(level: int, event: str, exc_info: bool = False, **fields) -> None:
    payload = {"event": event, **fields}
    logging.log(level, json.dumps(payload, ensure_ascii=False, default=str), exc_info=exc_info)


def _safe_package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


@app.middleware("http")
async def add_request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    started_at = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        _log_event(
            logging.ERROR,
            "request_failed",
            exc_info=True,
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            duration_ms=duration_ms,
        )
        raise

    duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    _log_event(
        logging.INFO,
        "request_completed",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=exc.status_code,
        headers={"X-Request-ID": request_id},
        content={
            "error": {
                "code": "http_error",
                "message": exc.detail,
                "request_id": request_id,
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    _log_event(logging.ERROR, "unhandled_error", exc_info=True, request_id=request_id, path=request.url.path)
    return JSONResponse(
        status_code=500,
        headers={"X-Request-ID": request_id},
        content={
            "error": {
                "code": "internal_error",
                "message": "RAG service failed to process the request.",
                "request_id": request_id,
            }
        },
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


@dataclass(frozen=True)
class RetrievalPolicy:
    name: str
    min_score: float
    allow_recency_override: bool = False
    prefer_notices_with_dates: bool = False


@dataclass(frozen=True)
class QueryAnalysisMeta:
    result: QueryAnalysisResult | None
    used: bool = False
    failed: bool = False


_datasets: Dict[str, DatasetCache] = {}
FALLBACK_REASON_NO_RESULTS = "no_results"
FALLBACK_REASON_DATE_FILTER_ELIMINATED_ALL = "date_filter_eliminated_all"
FALLBACK_REASON_SCORE_BELOW_THRESHOLD = "score_below_threshold"
FALLBACK_REASON_DATASET_UNAVAILABLE = "dataset_unavailable"
DATASET_REASON_EMPTY_COLLECTION = "empty_collection"
DATASET_REASON_ARTIFACT_MISSING = "artifact_missing"
DATASET_REASON_VECTORIZER_MISSING = "vectorizer_missing"
DATASET_REASON_VERSION_MISMATCH = "version_mismatch"
NOTICE_RECENCY_TERMS = ("장학", "공지", "모집", "발표")
RECENT_QUERY_TERMS = ("오늘", "최근", "최신", "방금", "올라온", "새로")
NOTICE_FOCUS_TERMS = ("장학", "학사", "입학", "유학생", "수강", "휴학", "복학", "등록", "졸업")
NOTICE_BOARD_ALIASES = {
    "일반공지": "일반공지",
    "일반 공지": "일반공지",
    "장학공지": "장학공지",
    "장학 공지": "장학공지",
    "학사공지": "학사공지",
    "학사 공지": "학사공지",
    "유학생공지": "유학생공지",
    "유학생 공지": "유학생공지",
    "행사공지": "행사공지",
    "행사 공지": "행사공지",
    "국제교류공지": "국제교류공지",
    "국제교류 공지": "국제교류공지",
    "국제공지": "국제공지",
    "국제 공지": "국제공지",
    "학술공지": "학술공지",
    "학술 공지": "학술공지",
    "입학공지": "입학공지",
    "입학 공지": "입학공지",
    "안전공지": "안전공지",
    "안전 공지": "안전공지",
}
SCHOOL_INFO_TERMS = (
    "동국",
    "학교",
    "학사",
    "공지",
    "장학",
    "모집",
    "발표",
    "전화번호",
    "연락처",
    "사무실",
    "내선",
    "교과",
    "전공",
    "수업",
    "강의",
    "개강",
    "종강",
    "시험",
    "수강",
    "휴학",
    "복학",
    "성적",
    "졸업",
    "입학",
    "등록",
    "일정",
    "교수",
    "행정실",
    "장학금",
    "공지사항",
    "학칙",
    "규정",
    "교과과정",
    "과목",
)
SMALL_TALK_TERMS = (
    "안녕",
    "하이",
    "반가워",
    "ㅎㅇ",
    "고마워",
    "감사",
    "땡큐",
    "이름이뭐야",
    "누구야",
    "정체가뭐야",
    "잘가",
    "잘자",
    "바이",
    "bye",
    "오늘기분어때",
    "기분어때",
    "잘지내",
    "요즘어때",
    "상태어때",
    "심심해",
    "농담해줘",
    "웃긴얘기해줘",
    "멋지다",
    "귀엽다",
    "좋아해",
)
SMALL_TALK_BLOCK_TERMS = (
    "상담",
    "우울",
    "불안",
    "진로",
    "진학",
    "투자",
    "재정",
    "법률",
    "소송",
    "의학",
    "병원",
    "약",
    "치료",
    "건강",
)


class SourceChunk(BaseModel):
    source: str
    metadata: Dict
    snippet: str
    chunk_id: str | None = None
    title: str | None = None
    url: str | None = None
    published_at: str | None = None
    vector_score: float | None = None
    sparse_score: float | None = None
    hybrid_score: float | None = None
    recency_score: float | None = None
    final_score: float | None = None
    sort_date: str | None = None


class AskResponse(BaseModel):
    answer: str
    citations: str
    route: List[str]
    sources: List[SourceChunk]
    fallback_triggered: bool = False
    fallback_reason: str | None = None


class AskRequest(BaseModel):
    question: str = Field(..., description="사용자 질문", alias="question")
    session_id: str | None = Field(None, description="대화 세션 ID (없으면 기본 세션)", alias="sessionId")
    major: str | None = Field(None, description="사용자 학과") # 새로 추가


def _dataset_status_message(reason: str, **kwargs) -> str:
    if reason == DATASET_REASON_EMPTY_COLLECTION:
        return "Chroma collection is empty while chunk cache is loaded."
    if reason == DATASET_REASON_ARTIFACT_MISSING:
        return "Chunk artifact is missing."
    if reason == DATASET_REASON_VECTORIZER_MISSING:
        return "TF-IDF vectorizer artifact is missing."
    if reason == DATASET_REASON_VERSION_MISMATCH:
        return (
            "TF-IDF artifact version mismatch: "
            f"{kwargs.get('artifact_version')} != {kwargs.get('runtime_version')}"
        )
    return "Dataset status is degraded."

    class Config:
        populate_by_name = True


class SubmitRequest(BaseModel):
    source_type: str
    data: str


def _clean_response_value(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _clean_response_str(value) -> str | None:
    value = _clean_response_value(value)
    return None if value is None else str(value)


def _clean_response_float(value) -> float | None:
    value = _clean_response_value(value)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_retrieval_fallback_answer(
    route: List[str] = None,
    reason: str | None = None,
    *,
    date_filter_relaxed: bool = False,
    policy_name: str | None = None,
    clarification_reason: str | None = None,
) -> str:
    base_msg = (
        "제공된 동국대학교 자료에서 질문과 충분히 관련 있는 정보를 찾지 못했습니다.\n\n"
        "정확하지 않은 정보를 추측해서 답변하는 대신, 다음과 같은 방법을 권장합니다:\n"
    )

    if reason == FALLBACK_REASON_DATE_FILTER_ELIMINATED_ALL:
        if date_filter_relaxed and policy_name == "recent_notices":
            base_msg += "- **최신 범위 재확인**: 오늘 기준으로 찾지 못해 최근 며칠 범위까지 넓혀 다시 검색했지만 확인된 공지를 찾지 못했습니다.\n"
        else:
            base_msg += "- **날짜 범위 재확인**: 요청하신 날짜 범위 안에서는 확인된 공지를 찾지 못했습니다. 날짜를 넓혀서 다시 질문해 보세요.\n"
    elif reason == FALLBACK_REASON_DATASET_UNAVAILABLE:
        base_msg += "- **잠시 후 재시도**: 일부 학교 자료 인덱스를 지금 조회하지 못했습니다. 잠시 후 다시 질문해 주세요.\n"
    elif reason == FALLBACK_REASON_SCORE_BELOW_THRESHOLD:
        if policy_name == "recent_notices":
            base_msg += "- **최신 공지 키워드 보강**: 최근 공지 후보는 있었지만 충분히 일치하는 근거로 보기 어려웠습니다. 장학명이나 정확한 공지 제목을 포함해 다시 질문해 주세요.\n"
        else:
            base_msg += "- **키워드 보강**: 관련 문서는 있었지만 충분히 일치하는 근거로 보기 어려웠습니다. 장학명이나 공지 제목을 포함해 다시 질문해 주세요.\n"

    if route and "staff" in route:
        base_msg += "- **부서 연락처 확인**: 질문하신 내용과 관련된 부서의 연락처를 찾으시려면 '어느 부서 전화번호 알려줘'와 같이 다시 질문해 보세요.\n"
    elif route and "notices" in route:
        base_msg += "- **공지사항 검색**: 학교 홈페이지의 공지사항 게시판에서 키워드로 직접 검색해 보시는 것이 가장 정확합니다.\n"

    base_msg += (
        "- **질문 구체화**: 학과명, 날짜, 정확한 공지 제목 등을 포함해 주시면 더 나은 결과를 얻을 수 있습니다.\n"
        "- **공식 채널 이용**: 긴급한 사안은 해당 학과 사무실이나 행정 부서에 직접 유선으로 문의하시기 바랍니다."
    )
    if clarification_reason:
        base_msg += f"\n- **추가 정보 요청**: {clarification_reason}"
    return base_msg


def _get_current_kst_string() -> str:
    from datetime import timedelta, timezone

    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).strftime("%Y년 %m월 %d일 %H시 %M분 (KST)")


def _is_small_talk_query(raw_query: str) -> bool:
    normalized = re.sub(r"\s+", "", raw_query.lower())
    if not normalized:
        return False
    if any(term in normalized for term in SCHOOL_INFO_TERMS):
        return False
    if any(term in normalized for term in SMALL_TALK_BLOCK_TERMS):
        return False
    return any(term in normalized for term in SMALL_TALK_TERMS)


def _calculate_recency_score(sort_date, dataset: str, now: pd.Timestamp) -> float:
    decay_days = RECENCY_DECAY_DAYS_BY_DATASET.get(dataset)
    if not decay_days or pd.isna(sort_date):
        return 0.0

    age_days = max((now - sort_date).total_seconds() / 86400, 0.0)
    return math.exp(-age_days / decay_days)


def _is_recent_notice_query(query: str, route: List[str]) -> bool:
    if "notices" not in route:
        return False
    if not any(term in query for term in NOTICE_RECENCY_TERMS):
        return False
    return any(term in query for term in RECENT_QUERY_TERMS)


def _is_staff_lookup_query(query: str, route: List[str]) -> bool:
    if "staff" not in route:
        return False
    return any(term in query for term in ("전화번호", "연락처", "사무실", "내선", "번호"))


def _extract_notice_board_filter(query: str, route: List[str]) -> str | None:
    if "notices" not in route:
        return None

    for alias, normalized in NOTICE_BOARD_ALIASES.items():
        if alias in query:
            return normalized
    return None


def _analysis_to_meta(result: QueryAnalysisResult | None, *, failed: bool = False) -> QueryAnalysisMeta:
    if result is None:
        return QueryAnalysisMeta(result=None, used=False, failed=failed)
    return QueryAnalysisMeta(result=result, used=True, failed=False)


def _build_retrieval_queries(raw_query: str, expanded_query: str, analysis: QueryAnalysisMeta) -> List[str]:
    queries: List[str] = []
    candidates = [raw_query]
    if analysis.result is not None:
        candidates.append(analysis.result.normalized_question)
        candidates.extend(analysis.result.search_queries[:QUERY_ANALYSIS_MAX_QUERIES])
    candidates.append(expanded_query)

    for candidate in candidates:
        cleaned = candidate.strip()
        if cleaned and cleaned not in queries:
            queries.append(cleaned)
    return queries


def _merge_routes(analysis: QueryAnalysisMeta, routed: List[str]) -> List[str]:
    merged: List[str] = []
    if analysis.result is not None and analysis.result.intent in {"notices", "rules", "schedule", "staff", "courses"}:
        merged.append(analysis.result.intent)
    for route_name in routed:
        if route_name not in merged:
            merged.append(route_name)
    return merged or ["notices"]


def _resolve_retrieval_policy(query: str, route: List[str]) -> RetrievalPolicy:
    recent_notice_query = _is_recent_notice_query(query, route)
    if recent_notice_query:
        return RetrievalPolicy(
            name="recent_notices",
            min_score=max(MIN_RETRIEVAL_SCORE - 0.04, 0.08),
            allow_recency_override=True,
            prefer_notices_with_dates=True,
        )
    if _is_staff_lookup_query(query, route):
        return RetrievalPolicy(name="staff_lookup", min_score=MIN_RETRIEVAL_SCORE + 0.02)
    if "courses" in route and len(route) == 1:
        return RetrievalPolicy(name="courses", min_score=MIN_RETRIEVAL_SCORE)
    if any(dataset in route for dataset in ("rules", "schedule")) and "notices" not in route:
        return RetrievalPolicy(name="rules_schedule", min_score=MIN_RETRIEVAL_SCORE)
    if "notices" in route:
        return RetrievalPolicy(name="general_notices", min_score=MIN_RETRIEVAL_SCORE)
    return RetrievalPolicy(name="default", min_score=MIN_RETRIEVAL_SCORE)


def _extract_notice_focus_terms(query: str) -> List[str]:
    return [term for term in NOTICE_FOCUS_TERMS if term in query]


def _row_matches_notice_focus_terms(row: pd.Series, focus_terms: List[str]) -> bool:
    if not focus_terms:
        return True

    haystack = " ".join(
        filter(
            None,
            [
                _clean_response_str(row.get("title")) or "",
                _clean_response_str(row.get("topics")) or "",
                _clean_response_str(row.get("snippet")) or "",
                _clean_response_str(row.get("chunk_text")) or "",
            ],
        )
    )
    return any(term in haystack for term in focus_terms)


def _has_notice_topic_alignment(merged: pd.DataFrame, query: str) -> bool:
    if merged.empty:
        return False
    focus_terms = _extract_notice_focus_terms(query)
    if not focus_terms:
        return True

    candidates = merged[merged.get("dataset") == "notices"].head(5)
    if candidates.empty:
        return False

    for _, row in candidates.iterrows():
        if _row_matches_notice_focus_terms(row, focus_terms):
            return True
    return False


def _apply_date_filter(hits: pd.DataFrame, dataset: str, date_filter: QueryDateFilter | None) -> tuple[pd.DataFrame, bool]:
    if date_filter is None or hits.empty or dataset not in ["notices", "schedule", "rules"]:
        return hits, False
    if "published_at" not in hits.columns:
        return hits, False

    filtered = hits.copy()
    filtered["_temp_date"] = pd.to_datetime(filtered["published_at"], errors="coerce")
    mask = (
        (filtered["_temp_date"] >= pd.Timestamp(date_filter.start))
        & (filtered["_temp_date"] <= pd.Timestamp(date_filter.end))
    )
    filtered = filtered[mask].copy()
    filtered.drop(columns=["_temp_date"], inplace=True, errors="ignore")
    was_eliminated = len(hits) > 0 and filtered.empty
    return filtered, was_eliminated


async def _retrieve_frames(
    *,
    route: List[str],
    query: str,
    final_where_filter: Dict,
    notice_board_filter: str | None,
    date_filter: QueryDateFilter | None,
    request_id: str,
) -> tuple[List[pd.DataFrame], bool, List[str]]:
    frames: List[pd.DataFrame] = []
    date_filter_eliminated_any = False
    unavailable_datasets: List[str] = []

    for dataset in route:
        try:
            chunks_df, vectorizer, matrix = await run_in_threadpool(_ensure_dataset, dataset)
        except (KeyError, FileNotFoundError, ValueError) as exc:
            unavailable_datasets.append(dataset)
            _log_event(
                logging.ERROR,
                "retrieval_dataset_unavailable",
                request_id=request_id,
                dataset=dataset,
                error=str(exc),
            )
            continue

        artifacts = DATASET_ARTIFACTS[dataset]
        current_dataset_filter = final_where_filter.copy()
        if dataset != "courses":
            current_dataset_filter.pop("major", None)
        if dataset == "notices" and notice_board_filter:
            current_dataset_filter["topics"] = {"$eq": notice_board_filter}

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
        hits, eliminated = _apply_date_filter(hits, dataset, date_filter)
        date_filter_eliminated_any = date_filter_eliminated_any or eliminated

        _log_event(
            logging.INFO,
            "retrieval_dataset_completed",
            request_id=request_id,
            dataset=dataset,
            filter=final_filter,
            date_filter=None if date_filter is None else date_filter.label,
            hits=len(hits),
        )

        if not hits.empty:
            hits["dataset"] = dataset
            frames.append(hits)

    return frames, date_filter_eliminated_any, unavailable_datasets


async def _retrieve_frames_for_queries(
    *,
    route: List[str],
    queries: List[str],
    final_where_filter: Dict,
    notice_board_filter: str | None,
    date_filter: QueryDateFilter | None,
    request_id: str,
) -> tuple[List[pd.DataFrame], bool, List[str]]:
    all_frames: List[pd.DataFrame] = []
    date_filter_eliminated_any = False
    unavailable_datasets: List[str] = []

    for query_candidate in queries:
        frames, eliminated, unavailable = await _retrieve_frames(
            route=route,
            query=query_candidate,
            final_where_filter=final_where_filter,
            notice_board_filter=notice_board_filter,
            date_filter=date_filter,
            request_id=request_id,
        )
        for frame in frames:
            if frame.empty:
                continue
            tagged = frame.copy()
            tagged["matched_query"] = query_candidate
            all_frames.append(tagged)
        date_filter_eliminated_any = date_filter_eliminated_any or eliminated
        unavailable_datasets.extend(unavailable)

    return all_frames, date_filter_eliminated_any, list(dict.fromkeys(unavailable_datasets))


def _coalesce_series(series: pd.Series):
    for value in series:
        cleaned = _clean_response_value(value)
        if cleaned is not None:
            return cleaned
    return None


def _merge_query_hits(frames: List[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()

    merged = pd.concat(frames, ignore_index=True)
    if merged.empty or "chunk_id" not in merged.columns:
        return merged

    aggregated_rows = []
    for chunk_id, group in merged.groupby("chunk_id", sort=False):
        row = group.iloc[0].copy()
        matched_queries = []
        for value in group.get("matched_query", pd.Series(dtype=str)).tolist():
            if isinstance(value, str) and value and value not in matched_queries:
                matched_queries.append(value)

        for column in group.columns:
            if column in {"chunk_id", "matched_query"}:
                continue
            if column in {"hybrid_score", "vector_score", "sparse_score"}:
                row[column] = pd.to_numeric(group[column], errors="coerce").max()
            else:
                row[column] = _coalesce_series(group[column])

        row["matched_queries"] = matched_queries
        row["matched_query_count"] = len(matched_queries)
        aggregated_rows.append(row)

    return pd.DataFrame(aggregated_rows)


def _collect_matched_queries(merged: pd.DataFrame) -> List[str]:
    if merged.empty or "matched_queries" not in merged.columns:
        return []

    collected: List[str] = []
    for queries in merged["matched_queries"].tolist():
        if not isinstance(queries, list):
            continue
        for query in queries:
            if isinstance(query, str) and query and query not in collected:
                collected.append(query)
    return collected


def _prepare_merged_results(
    merged: pd.DataFrame,
    recent_notice_query: bool,
    policy: RetrievalPolicy,
    query: str,
) -> pd.DataFrame:
    if merged.empty or "hybrid_score" not in merged.columns:
        return merged

    if "published_at" in merged.columns and "updated_at" in merged.columns:
        merged["sort_date"] = pd.to_datetime(merged["published_at"].fillna(merged["updated_at"]), errors="coerce")
    elif "published_at" in merged.columns:
        merged["sort_date"] = pd.to_datetime(merged["published_at"], errors="coerce")
    elif "updated_at" in merged.columns:
        merged["sort_date"] = pd.to_datetime(merged["updated_at"], errors="coerce")
    else:
        merged["sort_date"] = pd.NaT

    merged.dropna(subset=["hybrid_score"], inplace=True)
    if merged.empty:
        return merged

    min_hybrid = merged["hybrid_score"].min()
    max_hybrid = merged["hybrid_score"].max()
    if max_hybrid > min_hybrid:
        merged["norm_hybrid"] = (merged["hybrid_score"] - min_hybrid) / (max_hybrid - min_hybrid)
    else:
        merged["norm_hybrid"] = 1.0

    now = pd.Timestamp.now(tz="Asia/Seoul").tz_localize(None)
    merged["norm_recency"] = merged.apply(
        lambda row: _calculate_recency_score(row.get("sort_date"), row.get("dataset", ""), now),
        axis=1,
    )
    merged["sort_timestamp"] = merged["sort_date"].apply(
        lambda value: value.timestamp() if pd.notna(value) else float("-inf")
    )
    merged["final_score"] = (1 - RECENCY_WEIGHT) * merged["norm_hybrid"] + RECENCY_WEIGHT * merged["norm_recency"]
    if "matched_query_count" not in merged.columns:
        merged["matched_query_count"] = 1
    merged["query_match_bonus"] = (merged["matched_query_count"].clip(lower=1) - 1) * 0.03
    merged["final_score"] = merged["final_score"] + merged["query_match_bonus"]

    if recent_notice_query and policy.prefer_notices_with_dates:
        focus_terms = _extract_notice_focus_terms(query)
        if focus_terms:
            merged["notice_topic_match"] = merged.apply(
                lambda row: int(
                    row.get("dataset") == "notices"
                    and _row_matches_notice_focus_terms(row, focus_terms)
                ),
                axis=1,
            )
        else:
            merged["notice_topic_match"] = 0
        merged["recent_notice_priority"] = (
            (merged["dataset"] == "notices") & merged["sort_date"].notna()
        ).astype(int)
        merged.sort_values(
            by=["notice_topic_match", "matched_query_count", "recent_notice_priority", "sort_timestamp", "final_score", "hybrid_score"],
            ascending=[False, False, False, False, False, False],
            inplace=True,
        )
    else:
        merged.sort_values(by=["matched_query_count", "final_score", "hybrid_score"], ascending=[False, False, False], inplace=True)

    return merged


def _get_latest_document_published_at(cache: DatasetCache | None) -> str | None:
    if cache is None or cache.chunks.empty or "published_at" not in cache.chunks.columns:
        return None

    dates = pd.to_datetime(cache.chunks["published_at"], errors="coerce").dropna()
    if dates.empty:
        return None
    return dates.max().date().isoformat()


def _save_rag_evaluation_log(
    request_id: str,
    session_id: str,
    question: str,
    expanded_question: str,
    route: List[str],
    answer: str,
    fallback_triggered: bool,
    fallback_reason: str | None,
    date_filter_applied: bool,
    date_filter_relaxed: bool,
    analysis_intent: str | None,
    analysis_entities_json: str | None,
    analysis_time_focus: str | None,
    analysis_search_queries_json: str | None,
    analysis_needs_clarification: bool,
    analysis_clarification_reason: str | None,
    analysis_used: bool,
    analysis_failed: bool,
    matched_queries_json: str | None,
    top_hybrid_score: float | None,
    sources: List[SourceChunk],
) -> None:
    session = SessionLocal()
    try:
        query_log = RagQueryLog(
            request_id=request_id,
            session_id=session_id,
            question=question,
            expanded_question=expanded_question,
            route=json.dumps(route, ensure_ascii=False),
            answer=answer,
            fallback_triggered=fallback_triggered,
            fallback_reason=fallback_reason,
            date_filter_applied=date_filter_applied,
            date_filter_relaxed=date_filter_relaxed,
            analysis_intent=analysis_intent,
            analysis_entities_json=analysis_entities_json,
            analysis_time_focus=analysis_time_focus,
            analysis_search_queries_json=analysis_search_queries_json,
            analysis_needs_clarification=analysis_needs_clarification,
            analysis_clarification_reason=analysis_clarification_reason,
            analysis_used=analysis_used,
            analysis_failed=analysis_failed,
            matched_queries_json=matched_queries_json,
            top_hybrid_score=top_hybrid_score,
            source_count=len(sources),
        )
        session.add(query_log)
        session.flush()

        for rank, source in enumerate(sources, start=1):
            session.add(
                RagRetrievalLog(
                    query_log_id=query_log.id,
                    rank=rank,
                    dataset=source.source,
                    chunk_id=source.chunk_id,
                    title=source.title,
                    url=source.url,
                    published_at=source.published_at,
                    vector_score=source.vector_score,
                    sparse_score=source.sparse_score,
                    hybrid_score=source.hybrid_score,
                    recency_score=source.recency_score,
                    final_score=source.final_score,
                    sort_date=source.sort_date or source.published_at,
                    snippet=source.snippet[:2000],
                )
            )

        session.commit()
    except Exception:
        session.rollback()
        _log_event(logging.ERROR, "rag_evaluation_log_failed", exc_info=True, request_id=request_id)
    finally:
        session.close()


def _format_mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat()


def _build_notices_ingestion_status(session) -> dict:
    latest_run = (
        session.query(IngestionRun)
        .filter(IngestionRun.dataset == "notices")
        .order_by(IngestionRun.started_at.desc())
        .first()
    )
    latest_collection = (
        session.query(SourceDocument)
        .filter(SourceDocument.dataset == "notices")
        .order_by(SourceDocument.collected_at.desc())
        .first()
    )
    quality_rows = (
        session.query(DocumentQualityCheck.severity, func.count(DocumentQualityCheck.id))
        .join(
            SourceDocument,
            DocumentQualityCheck.document_key == SourceDocument.document_key,
        )
        .filter(SourceDocument.dataset == "notices")
        .group_by(DocumentQualityCheck.severity)
        .all()
    )
    latest_quality_checks = (
        session.query(DocumentQualityCheck)
        .join(
            SourceDocument,
            DocumentQualityCheck.document_key == SourceDocument.document_key,
        )
        .filter(SourceDocument.dataset == "notices")
        .order_by(DocumentQualityCheck.created_at.desc())
        .limit(5)
        .all()
    )
    parse_failed_count = (
        session.query(SourceDocument)
        .filter(
            SourceDocument.dataset == "notices",
            SourceDocument.status == "parse_failed",
        )
        .count()
    )
    raw_count = (
        session.query(SourceDocument)
        .filter(SourceDocument.dataset == "notices", SourceDocument.raw_path.isnot(None))
        .count()
    )
    normalized_count = (
        session.query(SourceDocument)
        .filter(SourceDocument.dataset == "notices", SourceDocument.normalized_path.isnot(None))
        .count()
    )
    indexed_count = (
        session.query(SourceDocument)
        .filter(
            SourceDocument.dataset == "notices",
            SourceDocument.last_indexed_at.isnot(None),
            SourceDocument.status.in_(["active", "updated", "hidden"]),
        )
        .count()
    )

    return {
        "last_collection_at": None if latest_collection is None else latest_collection.collected_at.isoformat(),
        "last_successful_ingestion_at": (
            None
            if latest_run is None or latest_run.status not in {"success", "partial_success"}
            else latest_run.finished_at.isoformat() if latest_run.finished_at else None
        ),
        "ingestion_summary": {
            "status": None if latest_run is None else latest_run.status,
            "documents_seen": 0 if latest_run is None else latest_run.documents_seen,
            "documents_new": 0 if latest_run is None else latest_run.documents_new,
            "documents_updated": 0 if latest_run is None else latest_run.documents_updated,
            "documents_deleted": 0 if latest_run is None else latest_run.documents_deleted,
            "documents_failed": 0 if latest_run is None else latest_run.documents_failed,
        },
        "stage_summary": {
            "raw_documents": raw_count,
            "normalized_documents": normalized_count,
            "indexed_documents": indexed_count,
        },
        "quality_summary": {
            "parse_failed": parse_failed_count,
            "severities": {severity: count for severity, count in quality_rows},
            "recent_checks": [
                {
                    "document_key": row.document_key,
                    "check_type": row.check_type,
                    "severity": row.severity,
                    "message": row.message,
                    "created_at": row.created_at.isoformat(),
                }
                for row in latest_quality_checks
            ],
        },
    }


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
            tfidf_metadata = read_tfidf_metadata(key)
            artifact_version = tfidf_metadata.get("sklearn_version")
            if artifact_version and artifact_version != sklearn_version:
                _log_event(
                    logging.WARNING,
                    "tfidf_version_mismatch",
                    dataset=key,
                    artifact_version=artifact_version,
                    runtime_version=sklearn_version,
                )
            elif tfidf_metadata.get("is_legacy"):
                _log_event(logging.INFO, "tfidf_legacy_artifact_loaded", dataset=key)
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

    _log_event(
        logging.INFO,
        "runtime_versions",
        torch_version=_safe_package_version("torch"),
        transformers_version=_safe_package_version("transformers"),
        sentence_transformers_version=_safe_package_version("sentence-transformers"),
        sklearn_version=_safe_package_version("scikit-learn"),
    )
    
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


@app.get("/admin/rag-logs/export")
async def export_rag_logs(limit: int = 1000):
    safe_limit = min(max(limit, 1), 10000)
    session = SessionLocal()
    output = io.StringIO()
    output.write("\ufeff")

    fieldnames = [
        "query_log_id",
        "created_at",
        "request_id",
        "session_id",
        "question",
        "expanded_question",
        "route",
        "answer",
        "fallback_triggered",
        "fallback_reason",
        "date_filter_applied",
        "date_filter_relaxed",
        "top_hybrid_score",
        "source_count",
        "rank",
        "dataset",
        "chunk_id",
        "title",
        "url",
        "published_at",
        "vector_score",
        "sparse_score",
        "hybrid_score",
        "recency_score",
        "final_score",
        "sort_date",
        "snippet",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    try:
        query_logs = (
            session.query(RagQueryLog)
            .order_by(RagQueryLog.created_at.desc())
            .limit(safe_limit)
            .all()
        )

        for query_log in query_logs:
            base_row = {
                "query_log_id": query_log.id,
                "created_at": query_log.created_at,
                "request_id": query_log.request_id,
                "session_id": query_log.session_id,
                "question": query_log.question,
                "expanded_question": query_log.expanded_question,
                "route": query_log.route,
                "answer": query_log.answer,
                "fallback_triggered": query_log.fallback_triggered,
                "fallback_reason": query_log.fallback_reason,
                "date_filter_applied": query_log.date_filter_applied,
                "date_filter_relaxed": query_log.date_filter_relaxed,
                "top_hybrid_score": query_log.top_hybrid_score,
                "source_count": query_log.source_count,
            }
            retrievals = sorted(query_log.retrievals, key=lambda item: item.rank or 0)
            if not retrievals:
                writer.writerow(base_row)
                continue

            for retrieval in retrievals:
                writer.writerow(
                    {
                        **base_row,
                        "rank": retrieval.rank,
                        "dataset": retrieval.dataset,
                        "chunk_id": retrieval.chunk_id,
                        "title": retrieval.title,
                        "url": retrieval.url,
                        "published_at": retrieval.published_at,
                        "vector_score": retrieval.vector_score,
                        "sparse_score": retrieval.sparse_score,
                        "hybrid_score": retrieval.hybrid_score,
                        "recency_score": retrieval.recency_score,
                        "final_score": retrieval.final_score,
                        "sort_date": retrieval.sort_date,
                        "snippet": retrieval.snippet,
                    }
                )
    finally:
        session.close()

    filename = f"rag_evaluation_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/admin/rag/logs")
async def get_rag_logs(limit: int = 100):
    safe_limit = min(max(limit, 1), 1000)
    session = SessionLocal()
    try:
        logs = (
            session.query(RagQueryLog)
            .order_by(RagQueryLog.created_at.desc())
            .limit(safe_limit)
            .all()
        )
        result = [
            {
                "id": log.id,
                "question": log.question,
                "answer": log.answer,
                "fallback_triggered": log.fallback_triggered,
                "fallback_reason": log.fallback_reason,
                "created_at": log.created_at.isoformat() if log.created_at else None,
                "route": log.route,
                "source_count": log.source_count,
            }
            for log in logs
        ]
        logging.info(f"📋 [Admin] Returning {len(logs)} RAG logs.")
        return result
    except Exception as e:
        logging.error(f"❌ [Admin] Failed to fetch RAG logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.get("/admin/rag/status")
async def rag_admin_status():
    generated_at = datetime.now().isoformat()
    session = SessionLocal()
    try:
        datasets = []
        has_degraded_dataset = False

        for key, artifacts in DATASET_ARTIFACTS.items():
            chunk_path = artifacts.chunk_path
            if not chunk_path.exists() and artifacts.csv_path.exists():
                chunk_path = artifacts.csv_path

            vectorizer_path = VECTORIZER_DIR / f"{key}_tfidf.pkl"
            cache = _datasets.get(key)
            dataset_status = "ok"
            chroma_count = None
            error_message = None
            degraded_reason = None
            tfidf_metadata = {}

            try:
                chroma_count = count_items(artifacts.collection)
            except Exception as exc:
                dataset_status = "degraded"
                error_message = str(exc)

            if vectorizer_path.exists():
                try:
                    tfidf_metadata = read_tfidf_metadata(key)
                    artifact_version = tfidf_metadata.get("sklearn_version")
                    if artifact_version and artifact_version != sklearn_version:
                        dataset_status = "degraded"
                        degraded_reason = degraded_reason or DATASET_REASON_VERSION_MISMATCH
                        error_message = error_message or _dataset_status_message(
                            DATASET_REASON_VERSION_MISMATCH,
                            artifact_version=artifact_version,
                            runtime_version=sklearn_version,
                        )
                except Exception as exc:
                    dataset_status = "degraded"
                    error_message = error_message or f"Failed to read TF-IDF metadata: {exc}"

            chunk_artifact_exists = chunk_path.exists()
            vectorizer_exists = vectorizer_path.exists()
            if not chunk_artifact_exists:
                dataset_status = "degraded"
                degraded_reason = degraded_reason or DATASET_REASON_ARTIFACT_MISSING
                error_message = error_message or _dataset_status_message(DATASET_REASON_ARTIFACT_MISSING)
            if not vectorizer_exists:
                dataset_status = "degraded"
                degraded_reason = degraded_reason or DATASET_REASON_VECTORIZER_MISSING
                error_message = error_message or _dataset_status_message(DATASET_REASON_VECTORIZER_MISSING)
            if chroma_count == 0 and cache is not None and len(cache.chunks) > 0:
                dataset_status = "degraded"
                degraded_reason = DATASET_REASON_EMPTY_COLLECTION
                error_message = _dataset_status_message(DATASET_REASON_EMPTY_COLLECTION)

            if dataset_status != "ok":
                has_degraded_dataset = True
                _log_event(
                    logging.WARNING,
                    "dataset_status_degraded",
                    dataset=key,
                    reason=degraded_reason,
                    error=error_message,
                )

            datasets.append(
                {
                    "key": key,
                    "collection": artifacts.collection,
                    "chroma_count": chroma_count,
                    "cached_chunk_count": 0 if cache is None else len(cache.chunks),
                    "chunk_artifact_exists": chunk_artifact_exists,
                    "chunk_artifact_mtime": _format_mtime(chunk_path),
                    "latest_document_published_at": _get_latest_document_published_at(cache),
                    "vectorizer_exists": vectorizer_exists,
                    "vectorizer_mtime": _format_mtime(vectorizer_path),
                    "last_successful_indexed_at": tfidf_metadata.get("created_at") or _format_mtime(vectorizer_path),
                    "vectorizer_sklearn_version": tfidf_metadata.get("sklearn_version"),
                    "status": dataset_status,
                    "error": error_message,
                }
            )

        pending_items = {
            "pending": session.query(PendingItem).filter(PendingItem.status == "pending").count(),
            "approved": session.query(PendingItem).filter(PendingItem.status.in_(["approved", "approved_manually"])).count(),
            "rejected": session.query(PendingItem).filter(PendingItem.status == "rejected").count(),
        }

        latest_query = session.query(RagQueryLog).order_by(RagQueryLog.created_at.desc()).first()
        fallback_reason_counts = {
            (reason or "unknown"): count
            for reason, count in (
                session.query(RagQueryLog.fallback_reason, func.count(RagQueryLog.id))
                .filter(RagQueryLog.fallback_triggered.is_(True))
                .group_by(RagQueryLog.fallback_reason)
                .all()
            )
        }
        rag_logs = {
            "total_queries": session.query(RagQueryLog).count(),
            "fallback_count": session.query(RagQueryLog).filter(RagQueryLog.fallback_triggered.is_(True)).count(),
            "latest_query_at": None if latest_query is None else latest_query.created_at.isoformat(),
            "fallback_reasons": fallback_reason_counts,
        }
        notices_ingestion = _build_notices_ingestion_status(session)

        return {
            "status": "degraded" if has_degraded_dataset else "ok",
            "generated_at": generated_at,
            "datasets": datasets,
            "pending_items": pending_items,
            "rag_logs": rag_logs,
            "notices_ingestion": notices_ingestion,
        }
    except Exception as exc:
        _log_event(logging.ERROR, "rag_admin_status_failed", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "generated_at": generated_at,
                "datasets": [],
                "pending_items": {"pending": 0, "approved": 0, "rejected": 0},
                "rag_logs": {"total_queries": 0, "fallback_count": 0, "latest_query_at": None},
                "notices_ingestion": {
                    "last_collection_at": None,
                    "last_successful_ingestion_at": None,
                    "ingestion_summary": {
                        "status": None,
                        "documents_seen": 0,
                        "documents_new": 0,
                        "documents_updated": 0,
                        "documents_deleted": 0,
                        "documents_failed": 0,
                    },
                    "stage_summary": {
                        "raw_documents": 0,
                        "normalized_documents": 0,
                        "indexed_documents": 0,
                    },
                    "quality_summary": {"parse_failed": 0, "severities": {}, "recent_checks": []},
                },
                "error": str(exc),
            },
        )
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

            # 3.5. Append to CSV (Persistent Storage)
            try:
                artifacts = DATASET_ARTIFACTS["notices"]
                csv_path = artifacts.csv_path
                
                # notices.csv schema: chunk_id,doc_id,chunk_text,position,token_len,title,topics,published_at,url,attachments,source,notice_id,rule_id,schedule_id,course_id,staff_id
                new_row = {
                    "chunk_id": doc_id,
                    "doc_id": doc_id,
                    "chunk_text": text_content,
                    "position": 0,
                    "token_len": len(text_content), 
                    "title": notice.title,
                    "topics": notice.board,
                    "published_at": notice.published_date,
                    "url": "",
                    "attachments": "[]",
                    "source": "notices",
                    "notice_id": notice.id,
                    "rule_id": "",
                    "schedule_id": "",
                    "course_id": "",
                    "staff_id": ""
                }
                
                if csv_path.exists():
                    new_df = pd.DataFrame([new_row])
                    new_df.to_csv(csv_path, mode='a', header=False, index=False, encoding='utf-8-sig')
                    logging.info(f"✅ [Admin] Appended to notices.csv")
                else:
                    logging.warning(f"⚠️ [Admin] notices.csv not found. Skipping CSV append.")

            except Exception as e:
                logging.error(f"❌ [Admin] Failed to append to CSV: {e}")

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
async def ask(req: AskRequest, request: Request) -> AskResponse:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    raw_query = req.question.strip()
    if not raw_query:
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다.")

    session_id = req.session_id or str(uuid.uuid4())

    if _is_small_talk_query(raw_query):
        current_date = _get_current_kst_string()
        try:
            small_talk_answer = await generate_smalltalk_answer(
                question=raw_query,
                session_id=session_id,
                current_date=current_date,
            )
        except Exception:
            _log_event(logging.ERROR, "small_talk_generation_failed", exc_info=True, request_id=request_id)
            small_talk_answer = "안녕하세요. 가벼운 대화는 짧게 도와드릴 수 있고, 학교 정보가 필요하면 바로 질문해 주세요."
            await run_in_threadpool(append_manual_history, session_id, raw_query, small_talk_answer)
        _log_event(
            logging.INFO,
            "small_talk_answered",
            request_id=request_id,
            session_id=session_id,
            raw_query=raw_query,
        )
        await run_in_threadpool(
            _save_rag_evaluation_log,
            request_id,
            session_id,
            raw_query,
            raw_query,
            ["smalltalk"],
            small_talk_answer,
            False,
            None,
            False,
            False,
            None,
            None,
            None,
            None,
            False,
            None,
            False,
            False,
            None,
            None,
            [],
        )
        return AskResponse(
            answer=small_talk_answer,
            citations="",
            route=["smalltalk"],
            sources=[],
            fallback_triggered=False,
            fallback_reason=None,
        )

    analysis_meta = QueryAnalysisMeta(result=None, used=False, failed=False)
    if USE_QUERY_ANALYSIS:
        analysis_result = await analyze_query(raw_query)
        analysis_meta = _analysis_to_meta(analysis_result, failed=analysis_result is None)

    expanded_query = expand_query(raw_query)
    retrieval_queries = _build_retrieval_queries(raw_query, expanded_query, analysis_meta)
    semantic_query = analysis_meta.result.normalized_question if analysis_meta.result is not None else expanded_query
    _log_event(
        logging.INFO,
        "ask_started",
        request_id=request_id,
        raw_query=raw_query,
        expanded_query=expanded_query,
        retrieval_queries=retrieval_queries,
        analysis_intent=None if analysis_meta.result is None else analysis_meta.result.intent,
    )

    # 로그에 처리된 질문과 세션 ID를 출력하여 디버깅을 돕습니다.
    _log_event(logging.INFO, "ask_session", request_id=request_id, session_id=session_id)

    user_major = req.major
    final_where_filter: Dict = {}
    if user_major and user_major != "Default": 
        final_where_filter["major"] = {"$eq": user_major}

    _log_event(logging.INFO, "ask_filters", request_id=request_id, filters=final_where_filter)
    routed = await route_query(
        analysis_meta.result.normalized_question
        if analysis_meta.result is not None
        else expanded_query
    )
    route = _merge_routes(analysis_meta, routed)
    date_filter = await run_in_threadpool(
        extract_date_filter_from_query,
        semantic_query,
    )
    date_filter_applied = date_filter is not None
    date_filter_relaxed = False
    recent_notice_query = _is_recent_notice_query(semantic_query, route)
    notice_board_filter = _extract_notice_board_filter(semantic_query, route)
    retrieval_policy = _resolve_retrieval_policy(semantic_query, route)

    frames, date_filter_eliminated_any, unavailable_datasets = await _retrieve_frames_for_queries(
        route=route,
        queries=retrieval_queries,
        final_where_filter=final_where_filter,
        notice_board_filter=notice_board_filter,
        date_filter=date_filter,
        request_id=request_id,
    )

    if not frames and date_filter is not None and date_filter.relaxed_start and date_filter.relaxed_end:
        date_filter_relaxed = True
        relaxed_filter = QueryDateFilter(
            start=date_filter.relaxed_start,
            end=date_filter.relaxed_end,
            label=f"{date_filter.label}_relaxed",
            is_relative=date_filter.is_relative,
        )
        relaxed_frames, _, relaxed_unavailable = await _retrieve_frames_for_queries(
            route=route,
            queries=retrieval_queries,
            final_where_filter=final_where_filter,
            notice_board_filter=notice_board_filter,
            date_filter=relaxed_filter,
            request_id=request_id,
        )
        if relaxed_frames:
            frames = relaxed_frames
        unavailable_datasets = list(dict.fromkeys(unavailable_datasets + relaxed_unavailable))

    if not frames:
        _log_event(logging.INFO, "retrieval_no_results", request_id=request_id, route=route)
        merged = pd.DataFrame()
    else:
        merged = _merge_query_hits(frames)

    merged = _prepare_merged_results(merged, recent_notice_query, retrieval_policy, semantic_query)
    merged = merged.head(DEFAULT_TOP_K).reset_index(drop=True)
    matched_queries = _collect_matched_queries(merged)

    top_hybrid_score = None
    if not merged.empty and "hybrid_score" in merged.columns:
        top_hybrid_score = _clean_response_float(merged["hybrid_score"].max())

    notice_topic_aligned = _has_notice_topic_alignment(merged, semantic_query)
    allow_recent_notice_answer = (
        retrieval_policy.allow_recency_override
        and recent_notice_query
        and not merged.empty
        and notice_topic_aligned
    )
    effective_min_score = retrieval_policy.min_score
    if recent_notice_query and retrieval_policy.allow_recency_override and not notice_topic_aligned:
        effective_min_score = max(MIN_RETRIEVAL_SCORE, retrieval_policy.min_score)
    fallback_reason = None
    if merged.empty:
        if unavailable_datasets and len(unavailable_datasets) == len(route):
            fallback_reason = FALLBACK_REASON_DATASET_UNAVAILABLE
        elif date_filter_eliminated_any:
            fallback_reason = FALLBACK_REASON_DATE_FILTER_ELIMINATED_ALL
        else:
            fallback_reason = FALLBACK_REASON_NO_RESULTS
    elif (top_hybrid_score is None or top_hybrid_score < effective_min_score) and not allow_recent_notice_answer:
        fallback_reason = FALLBACK_REASON_SCORE_BELOW_THRESHOLD

    if fallback_reason is not None:
        fallback_answer = _build_retrieval_fallback_answer(
            route=route,
            reason=fallback_reason,
            date_filter_relaxed=date_filter_relaxed,
            policy_name=retrieval_policy.name,
            clarification_reason=(
                analysis_meta.result.clarification_reason
                if analysis_meta.result is not None and analysis_meta.result.needs_clarification
                else None
            ),
        )
        _log_event(
            logging.INFO,
            "retrieval_fallback_triggered",
            request_id=request_id,
            route=route,
            top_hybrid_score=top_hybrid_score,
            threshold=effective_min_score,
            retry=date_filter_relaxed,
            fallback_reason=fallback_reason,
            policy_name=retrieval_policy.name,
            effective_min_score=effective_min_score,
            recent_notice_query=recent_notice_query,
            notice_topic_aligned=notice_topic_aligned,
            date_filter_label=None if date_filter is None else date_filter.label,
            analysis_used=analysis_meta.used,
            analysis_failed=analysis_meta.failed,
            notice_board_filter=notice_board_filter,
        )
        await run_in_threadpool(
            _save_rag_evaluation_log,
            request_id,
            session_id,
            raw_query,
            expanded_query,
            route,
            fallback_answer,
            True,
            fallback_reason,
            date_filter_applied,
            date_filter_relaxed,
            None if analysis_meta.result is None else analysis_meta.result.intent,
            None if analysis_meta.result is None else json.dumps(analysis_meta.result.entities, ensure_ascii=False),
            None if analysis_meta.result is None else analysis_meta.result.time_focus,
            None if analysis_meta.result is None else json.dumps(analysis_meta.result.search_queries, ensure_ascii=False),
            False if analysis_meta.result is None else analysis_meta.result.needs_clarification,
            None if analysis_meta.result is None else analysis_meta.result.clarification_reason,
            analysis_meta.used,
            analysis_meta.failed,
            json.dumps(matched_queries, ensure_ascii=False),
            top_hybrid_score,
            [],
        )
        await run_in_threadpool(append_manual_history, session_id, raw_query, fallback_answer)
        return AskResponse(
            answer=fallback_answer,
            citations="",
            route=route,
            sources=[],
            fallback_triggered=True,
            fallback_reason=fallback_reason,
        )

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
        
        # --- NEW ATTACHMENT PROCESSING ---
        attachments_str = row.get('attachments')
        if attachments_str:
            try:
                # attachments_str이 비어있지 않은 경우에만 json.loads 시도
                if attachments_str.strip(): # 비어있는 문자열 체크
                    attachments = json.loads(attachments_str)
                else:
                    attachments = [] # 비어있는 경우 빈 리스트로 처리

                if isinstance(attachments, list):
                    pdf_links = []
                    for att in attachments:
                        if isinstance(att, dict) and 'name' in att and 'url' in att:
                            file_name = att['name']
                            file_url = att['url']
                            # Check if it's a PDF or a file link
                            # For now, include all attachments as clickable links, not just PDFs
                            pdf_links.append(f"- [{file_name}]({file_url})")
                    if pdf_links:
                        part += "\n첨부파일:\n" + "\n".join(pdf_links) + "\n"
            except json.JSONDecodeError:
                logging.warning(f"Failed to decode attachments JSON: {attachments_str}")
        # --- END NEW ATTACHMENT PROCESSING ---

        context_parts.append(part)
    
    context_text = "\n\n---\n\n".join(context_parts) if context_parts else "검색된 관련 문서가 없습니다. 일반적인 대화로 응답해주세요."
    context_text = context_text[:MAX_CONTEXT_LENGTH] # 최대 길이 제한 유지 
    # LLM에게 현재 날짜를 전달하여 "오늘", "이번 학기" 등의 표현을 해석하도록 돕습니다.
    current_date = _get_current_kst_string()

    try:
        answer = await generate_langchain_answer(
            question=semantic_query,
            context=context_text,
            session_id=session_id,
            current_date=current_date
        )
    except Exception as e:
        _log_event(logging.ERROR, "llm_generation_failed", exc_info=True, request_id=request_id)
        answer = "죄송합니다. 답변을 생성하는 도중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."

    # 후처리: 과도한 볼드체 제거 대신 가독성 유지 (필요 시 최소화)
    # answer = answer.replace("**", "")

    citations_raw = await run_in_threadpool(format_citations, merged)
    citations = re.sub(r'<[^>]+>', '', citations_raw)

    score_columns = {"vector_score", "sparse_score", "hybrid_score", "norm_recency", "final_score"}
    internal_columns = {"chunk_text", "dataset", "sort_date", "sort_timestamp", "norm_hybrid", "recent_notice_priority", "matched_query_count", "query_match_bonus"} | score_columns
    sources = [
        SourceChunk(
            source=_clean_response_str(row.get("dataset")) or "",
            metadata={col: _clean_response_value(row.get(col)) for col in row.index if col not in internal_columns},
            snippet=_clean_response_str(row.get("chunk_text")) or "",
            chunk_id=_clean_response_str(row.get("chunk_id")),
            title=_clean_response_str(row.get("title")),
            url=_clean_response_str(row.get("url")),
            published_at=_clean_response_str(row.get("published_at")),
            vector_score=_clean_response_float(row.get("vector_score")),
            sparse_score=_clean_response_float(row.get("sparse_score")),
            hybrid_score=_clean_response_float(row.get("hybrid_score")),
            recency_score=_clean_response_float(row.get("norm_recency")),
            final_score=_clean_response_float(row.get("final_score")),
            sort_date=_clean_response_str(row.get("sort_date")),
        )
        for _, row in merged.iterrows()
    ]

    await run_in_threadpool(
        _save_rag_evaluation_log,
        request_id,
        session_id,
        raw_query,
        expanded_query,
        route,
        answer,
        False,
        None,
        date_filter_applied,
        date_filter_relaxed,
        None if analysis_meta.result is None else analysis_meta.result.intent,
        None if analysis_meta.result is None else json.dumps(analysis_meta.result.entities, ensure_ascii=False),
        None if analysis_meta.result is None else analysis_meta.result.time_focus,
        None if analysis_meta.result is None else json.dumps(analysis_meta.result.search_queries, ensure_ascii=False),
        False if analysis_meta.result is None else analysis_meta.result.needs_clarification,
        None if analysis_meta.result is None else analysis_meta.result.clarification_reason,
        analysis_meta.used,
        analysis_meta.failed,
        json.dumps(matched_queries, ensure_ascii=False),
        top_hybrid_score,
        sources,
    )

    _log_event(
        logging.INFO,
        "ask_completed",
        request_id=request_id,
        route=route,
        source_count=len(sources),
        top_hybrid_score=top_hybrid_score,
        policy_name=retrieval_policy.name,
        effective_min_score=effective_min_score,
        recent_notice_query=recent_notice_query,
        notice_topic_aligned=notice_topic_aligned,
        date_filter_label=None if date_filter is None else date_filter.label,
        analysis_used=analysis_meta.used,
        analysis_failed=analysis_meta.failed,
        notice_board_filter=notice_board_filter,
    )

    return AskResponse(
        answer=answer,
        citations=citations,
        route=route,
        sources=sources,
        fallback_triggered=False,
        fallback_reason=None,
    )


@app.post("/admin/reindex/{target}")
async def reindex_dataset(target: str):
    if target not in _DATASET_LOADERS and target != "all":
        raise HTTPException(status_code=400, detail=f"Invalid target: {target}")

    try:
        from src.pipelines.ingest import reindex_from_db

        target_param = None if target == "all" else target
        # run_in_threadpool because reindexing can be slow and blocking
        results = await run_in_threadpool(reindex_from_db, target_param)

        # Clear cache to force reload
        if target == "all":
            _datasets.clear()
        elif target in _datasets:
            del _datasets[target]

        return {
            "status": "ok",
            "message": f"Reindexing for '{target}' completed.",
            "details": {k: len(v[0]) for k, v in results.items()}
        }
    except Exception as e:
        _log_event(logging.ERROR, "reindex_failed", exc_info=True, target=target)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health() -> dict:
    status = {}
    for key in _DATASET_LOADERS:
        cache = _datasets.get(key)
        status[key] = 0 if cache is None else len(cache.chunks)
    return {"status": "ok", "datasets": status}
