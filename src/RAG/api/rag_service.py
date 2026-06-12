import csv
import functools
import io
from importlib.metadata import PackageNotFoundError, version
import logging
import math
import re
import sys
import threading
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
    RAG_DECOMPOSE_ENABLED,
    RAG_MAX_SUBQUERIES,
    RECENCY_DECAY_DAYS_BY_DATASET,
    PARENT_CONTEXT_ENABLED,
    RECENCY_WEIGHT,
    RERANKER_CANDIDATES,
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
    build_notice_chunks,
    ingest_courses,
    ingest_notices,
    ingest_rules,
    ingest_schedule,
    ingest_staff, # 추가
)
from src.search.hybrid import load_tfidf_with_ids, hybrid_search_with_meta
from src.search.hybrid import read_tfidf_metadata
from src.services.answer import format_citations
from src.services.langchain_chat import (
    append_manual_history,
    generate_langchain_answer,
    generate_langchain_answer_stream,
    get_recent_history_text,
)
from src.services.query_analysis import QueryAnalysisResult, analyze_query
from src.models.embedding import get_embedder, encode_texts
from src.services.router import route_query
from src.utils.date_parser import QueryDateFilter, extract_date_filter_from_query
from src.utils.query_expansion import expand_query
from src.utils.dept_college import college_grad_queries, personalized_grad_queries, user_scope_label
from src.utils.preprocess import make_doc_id
from src.vectorstore.chroma_client import count_items, upsert_items, delete_items

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
    tfidf_chunk_ids: list | None = None


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
# admin 리로드(del/재로드)와 검색 스레드의 _ensure_dataset 경합 방지용 락
_datasets_lock = threading.Lock()
FALLBACK_REASON_NO_RESULTS = "no_results"
FALLBACK_REASON_DATE_FILTER_ELIMINATED_ALL = "date_filter_eliminated_all"
FALLBACK_REASON_DATASET_UNAVAILABLE = "dataset_unavailable"
FALLBACK_REASON_SCORE_BELOW_THRESHOLD = "score_below_threshold"
# 학과 필터를 적용하지 않는 sentinel 값들(백엔드는 보통 null을 보내지만 방어적으로 처리).
_NO_MAJOR_SENTINELS = {"Default", "Unknown"}
DATASET_REASON_EMPTY_COLLECTION = "empty_collection"
DATASET_REASON_ARTIFACT_MISSING = "artifact_missing"
DATASET_REASON_VECTORIZER_MISSING = "vectorizer_missing"
DATASET_REASON_VERSION_MISMATCH = "version_mismatch"
NOTICE_RECENCY_TERMS = ("장학", "공지", "모집", "발표")
RECENT_QUERY_TERMS = ("오늘", "최근", "최신", "방금", "올라온", "새로")
NOTICE_FOCUS_TERMS = ("장학", "학사", "입학", "유학생", "수강", "휴학", "복학", "등록", "졸업")
ENTRY_YEAR_GUIDE_SOURCE_TYPE = "entry_year_guide_pdf"
ENTRY_YEAR_GUIDE_TERMS = (
    "학번",
    "신입생",
    "졸업",
    "졸업기준",
    "이수",
    "이수기준",
    "교양",
    "복수전공",
    "다전공",
    "전과",
    "수강신청",
    "재수강",
    "학점포기",
)
COURSE_GRADE_TERMS = ("1학년", "2학년", "3학년", "4학년", "1학기", "2학기")
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

    class Config:
        populate_by_name = True


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


def _has_school_info_terms(raw_query: str) -> bool:
    normalized = re.sub(r"\s+", "", raw_query.lower())
    return any(term in normalized for term in SCHOOL_INFO_TERMS)


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


def _extract_entry_year_from_query(query: str) -> int | None:
    explicit_match = re.search(r"\b(20\d{2})\s*(?:학번|신입생)?", query)
    if explicit_match:
        year = int(explicit_match.group(1))
        if 2000 <= year <= 2099:
            return year

    short_match = re.search(r"\b(\d{2})\s*학번", query)
    if short_match:
        year = 2000 + int(short_match.group(1))
        if 2000 <= year <= 2099:
            return year
    return None


def _has_entry_year_guide_intent(query: str) -> bool:
    return any(term in query for term in ENTRY_YEAR_GUIDE_TERMS)


def _should_append_rules_route(query: str, route: List[str]) -> bool:
    if "rules" in route:
        return False
    if "courses" in route and _has_entry_year_guide_intent(query):
        return True
    return False


def _is_entry_year_guide_row(row: pd.Series) -> bool:
    return str(row.get("source_type", "")).strip() == ENTRY_YEAR_GUIDE_SOURCE_TYPE


def _latest_entry_year_in_frame(merged: pd.DataFrame) -> int | None:
    if merged.empty or "entry_year" not in merged.columns:
        return None
    years = pd.to_numeric(merged["entry_year"], errors="coerce").dropna()
    if years.empty:
        return None
    return int(years.max())


def _build_guide_context_prefix(merged: pd.DataFrame, route: List[str], entry_year: int | None) -> str:
    if merged.empty or "source_type" not in merged.columns:
        return ""

    guide_mask = merged["source_type"].astype(str).eq(ENTRY_YEAR_GUIDE_SOURCE_TYPE)
    if not guide_mask.any():
        return ""

    latest_year = _latest_entry_year_in_frame(merged[guide_mask])
    effective_year = entry_year or latest_year

    if "courses" in route:
        year_label = f"{effective_year}학번 기준" if effective_year is not None else "최신 기준"
        return (
            f"안내 메모: 학번별 PDF 자료가 함께 검색되었습니다. 이 자료는 {year_label} 이수기준·졸업기준 안내용입니다. "
            "세부 과목표나 학년별 개설과목 목록처럼 단정하지 말고, 과목표가 확인되지 않으면 그 점을 명시하세요.\n\n"
        )
    return ""


def _build_guide_answer_prefix(merged: pd.DataFrame, route: List[str], entry_year: int | None) -> str:
    if merged.empty or "source_type" not in merged.columns:
        return ""

    guide_mask = merged["source_type"].astype(str).eq(ENTRY_YEAR_GUIDE_SOURCE_TYPE)
    if not guide_mask.any():
        return ""

    latest_year = _latest_entry_year_in_frame(merged[guide_mask])
    effective_year = entry_year or latest_year
    if effective_year is None:
        return ""

    if "courses" in route:
        has_course_chunks = "dataset" in merged.columns and (merged["dataset"].astype(str) == "courses").any()
        if not has_course_chunks:
            return (
                f"세부 과목표는 제공된 자료에서 충분히 확인되지 않아, 아래는 {effective_year}학년도 신입생 기준의 "
                "이수기준·졸업기준을 바탕으로 안내합니다.\n\n"
            )
        return f"참고: 일부 안내는 {effective_year}학년도 신입생 기준입니다.\n\n"

    if entry_year is None:
        return f"참고: 아래 내용은 최신 기준인 {effective_year}학년도 신입생 기준을 우선 반영했습니다.\n\n"
    return ""


def _analysis_to_meta(result: QueryAnalysisResult | None, *, failed: bool = False) -> QueryAnalysisMeta:
    if result is None:
        return QueryAnalysisMeta(result=None, used=False, failed=failed)
    return QueryAnalysisMeta(result=result, used=True, failed=False)


def _is_compound_analysis(analysis: QueryAnalysisMeta) -> bool:
    """질의 분해가 활성화되어 있고 분석이 복합 질문으로 판단했는지."""
    return bool(
        RAG_DECOMPOSE_ENABLED
        and analysis.result is not None
        and analysis.result.is_compound
        and analysis.result.sub_queries
    )


def _user_profile_prefix(user_major: str | None) -> str:
    """로그인 사용자의 소속 학과를 컨텍스트 상단에 명시해, 학과를 안 밝힌 질문도
    본인 소속 기준으로 답하도록 유도한다(개인 맞춤형). 학과 미상이면 빈 문자열."""
    major = user_major if user_major and user_major not in _NO_MAJOR_SENTINELS else None
    if not major:
        return ""
    label = user_scope_label(major)
    return (
        f"[질문자 정보] 소속 학과: {label}. "
        "질문에 학과·단과대를 따로 밝히지 않았다면 이 소속을 기준으로 답하세요. "
        "단, 질문에 다른 학과·단과대가 명시되어 있으면 그쪽 기준을 따르세요.\n\n"
    )


def _build_retrieval_queries(
    raw_query: str, expanded_query: str, analysis: QueryAnalysisMeta, user_major: str | None = None
) -> List[str]:
    queries: List[str] = []

    # 1. 원문은 항상 포함
    queries.append(raw_query.strip())

    # 2. 분석 결과가 있으면 분석된 쿼리 추가 (원문과 다를 경우만)
    if analysis.result is not None:
        norm = analysis.result.normalized_question.strip()
        if norm and norm not in queries:
            queries.append(norm)

        for sq in analysis.result.search_queries[:QUERY_ANALYSIS_MAX_QUERIES]:
            cleaned = sq.strip()
            if cleaned and cleaned not in queries:
                queries.append(cleaned)

    # 2-1. 복합 질문이면 측면별 분해 서브쿼리를 추가한다. 각 서브쿼리는 route의 데이터셋들과
    #      교차 검색되며(merge가 점수로 정리), 단순 질문은 이 경로를 타지 않아 영향이 없다.
    compound = _is_compound_analysis(analysis)
    if compound:
        for sub in analysis.result.sub_queries:
            cleaned = sub.query.strip()
            if cleaned and cleaned not in queries:
                queries.append(cleaned)

    # 3. 확장 쿼리는 분석 결과가 없을 때나 쿼리가 너무 적을 때만 보조적으로 추가한다.
    if len(queries) < 3:
        expanded = expanded_query.strip()
        if expanded and expanded not in queries:
            queries.append(expanded)

    # 단순 질문은 종전대로 최대 3개, 복합 질문은 분해 서브쿼리를 담을 수 있게 상한을 넓힌다.
    cap = (2 + RAG_MAX_SUBQUERIES) if compound else 3
    result = queries[:cap]

    # 학과명으로 졸업 요건을 물으면 소속 단과대 기준 검색어를 보강한다(예: 통계학과 → 이과대학).
    # 졸업기준 자료가 단과대 단위라 학과명만으로는 매칭이 약하기 때문. cap과 무관하게 항상 포함.
    extra_queries = list(college_grad_queries(raw_query))
    # 로그인 사용자가 학과를 안 밝히고 졸업요건을 물어도 본인 학과 기준 자료가 잡히도록 보강.
    valid_major = user_major if user_major and user_major not in _NO_MAJOR_SENTINELS else None
    extra_queries.extend(personalized_grad_queries(raw_query, valid_major))
    for cq in extra_queries:
        if cq not in result:
            result.append(cq)
    return result


def _merge_routes(analysis: QueryAnalysisMeta, routed: List[str]) -> List[str]:
    merged: List[str] = []
    if analysis.result is not None and analysis.result.intent in {"notices", "rules", "schedule", "staff", "courses"}:
        merged.append(analysis.result.intent)
    for route_name in routed:
        if route_name not in merged:
            merged.append(route_name)
    # 복합 질문이면 분해 서브쿼리가 가리키는 데이터셋을 합집합으로 더해, 요건·과목·일정·연락처가
    # 한 답변에 융합되도록 한다(예: 졸업 준비 → rules+courses+schedule+staff).
    if _is_compound_analysis(analysis):
        for dataset in analysis.result.decomposed_datasets:
            if dataset not in merged:
                merged.append(dataset)
    return merged or ["notices"]


def _resolve_retrieval_policy(query: str, route: List[str]) -> RetrievalPolicy:
    staff_lookup_min_score = min(
        MIN_RETRIEVAL_SCORE,
        max(MIN_RETRIEVAL_SCORE - 0.03, 0.08),
    )
    courses_min_score = min(
        MIN_RETRIEVAL_SCORE,
        max(MIN_RETRIEVAL_SCORE - 0.07, 0.05),
    )
    recent_notice_query = _is_recent_notice_query(query, route)
    if recent_notice_query:
        return RetrievalPolicy(
            name="recent_notices",
            min_score=max(MIN_RETRIEVAL_SCORE - 0.04, 0.08),
            allow_recency_override=True,
            prefer_notices_with_dates=True,
        )
    if _is_staff_lookup_query(query, route):
        return RetrievalPolicy(name="staff_lookup", min_score=staff_lookup_min_score)
    if "courses" in route and "notices" not in route and set(route).issubset({"courses", "rules"}):
        return RetrievalPolicy(name="courses", min_score=courses_min_score)
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


def _expand_chunk_with_neighbors(row: pd.Series) -> str:
    """검색된 청크에 같은 문서의 앞뒤 이웃 청크를 결합해 반환합니다 (parent-document 확장).

    검색은 작은 청크(정밀)로 하되 생성 근거는 더 넓게 제공해, 절차/기간 안내가
    청크 경계에서 잘려 LLM이 불완전한 근거로 답하는 문제를 줄인다.
    캐시된 chunks_df를 사용하므로 추가 I/O 비용이 없다. 실패 시 원본 청크 그대로.
    """
    chunk_text = str(row.get("chunk_text", ""))
    if not PARENT_CONTEXT_ENABLED:
        return chunk_text
    dataset = row.get("dataset")
    doc_id = row.get("doc_id")
    position = row.get("position")
    if not dataset or doc_id is None or position is None or pd.isna(position):
        return chunk_text

    cache = _datasets.get(str(dataset))
    if cache is None or cache.chunks.empty:
        return chunk_text
    df = cache.chunks
    if "doc_id" not in df.columns or "position" not in df.columns:
        return chunk_text

    try:
        pos = int(float(position))
        siblings = df[df["doc_id"].astype(str) == str(doc_id)]
        if len(siblings) <= 1:
            return chunk_text
        positions = siblings["position"].astype(float).astype(int)
        window = siblings[positions.isin([pos - 1, pos, pos + 1])].copy()
        window["_pos"] = window["position"].astype(float).astype(int)
        window.sort_values("_pos", inplace=True)

        parts: List[str] = []
        for _, sib in window.iterrows():
            text = str(sib.get("chunk_text", "")).strip()
            # 이웃 청크의 "[제목]" prefix는 중복이므로 제거(본문만 이어 붙임)
            if sib["_pos"] != pos and text.startswith("[") and "]\n\n" in text:
                text = text.split("]\n\n", 1)[1].strip()
            if text:
                parts.append(text)
        return "\n".join(parts) if parts else chunk_text
    except Exception:  # noqa: BLE001 — 확장 실패는 원본 청크로 무해하게 폴백
        return chunk_text


def _build_context_text(context_parts: List[str], limit: int, prefix: str = "") -> str:
    """문서 경계를 존중하며 컨텍스트를 limit 이내로 구성합니다.

    기존의 단순 슬라이싱(`text[:limit]`)은 마지막 문서를 중간에서 잘라
    LLM에 불완전한 근거를 제공했음. 한도를 넘기는 문서는 통째로 제외하되,
    첫 문서만은 (그것만으로 한도를 넘더라도) 포함 후 절단한다.
    """
    sep = "\n\n---\n\n"
    included: List[str] = []
    used = len(prefix)
    for part in context_parts:
        extra = len(part) + (len(sep) if included else 0)
        if included and used + extra > limit:
            break
        included.append(part)
        used += extra
    return (prefix + sep.join(included))[:limit]


async def _retrieve_frames(
    *,
    route: List[str],
    query: str,
    final_where_filter: Dict,
    notice_board_filter: str | None,
    date_filter: QueryDateFilter | None,
    entry_year: int | None,
    request_id: str,
) -> tuple[List[pd.DataFrame], bool, List[str]]:
    frames: List[pd.DataFrame] = []
    date_filter_eliminated_any = False
    unavailable_datasets: List[str] = []

    for dataset in route:
        try:
            chunks_df, vectorizer, matrix, tfidf_chunk_ids = await run_in_threadpool(_ensure_dataset, dataset)
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
            top_k=DEFAULT_TOP_K * 2,
            alpha=HYBRID_ALPHA,
            where_filter=final_filter,
            tfidf_chunk_ids=tfidf_chunk_ids,
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
    entry_year: int | None,
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
            entry_year=entry_year,
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
    entry_year: int | None = None,
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
    if "source_type" in merged.columns:
        guide_mask = merged["source_type"].astype(str).eq(ENTRY_YEAR_GUIDE_SOURCE_TYPE)
        if guide_mask.any() and _has_entry_year_guide_intent(query):
            if entry_year is not None and "entry_year" in merged.columns:
                matched_year_mask = guide_mask & merged["entry_year"].astype(str).eq(str(entry_year))
                merged.loc[matched_year_mask, "final_score"] = merged.loc[matched_year_mask, "final_score"] + 0.12
            elif "entry_year" in merged.columns:
                latest_year = _latest_entry_year_in_frame(merged[guide_mask])
                if latest_year is not None:
                    latest_mask = guide_mask & merged["entry_year"].astype(str).eq(str(latest_year))
                    merged.loc[latest_mask, "final_score"] = merged.loc[latest_mask, "final_score"] + 0.06

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
        # 날짜 우선 정렬(recent_notice) 분기는 의도된 시간순 배치라 리랭커를 적용하지 않는다.
        merged = _apply_cross_encoder_rerank(merged, query)

    return merged


def _apply_cross_encoder_rerank(merged: pd.DataFrame, query: str) -> pd.DataFrame:
    """상위 후보를 cross-encoder로 정밀 재정렬합니다(RERANKER_ENABLED=1일 때만).

    hybrid_score는 변경하지 않으므로 폴백 임계(MIN_RETRIEVAL_SCORE) 판정에는 영향 없음.
    recency 가중은 유지: rerank 점수와 norm_recency를 기존 비율로 재혼합한다.
    """
    from src.services.reranker import is_reranker_enabled, rerank_scores

    if not is_reranker_enabled() or merged.empty or len(merged) < 2:
        return merged

    head_n = min(RERANKER_CANDIDATES, len(merged))
    top = merged.head(head_n).copy()
    scores = rerank_scores(query, top["chunk_text"].astype(str).tolist())
    if scores is None or len(scores) != len(top):
        return merged

    top["rerank_raw"] = scores
    lo, hi = min(scores), max(scores)
    top["rerank_norm"] = (top["rerank_raw"] - lo) / (hi - lo) if hi > lo else 1.0
    recency = top["norm_recency"] if "norm_recency" in top.columns else 0.0
    top["final_score"] = (1 - RECENCY_WEIGHT) * top["rerank_norm"] + RECENCY_WEIGHT * recency
    top.sort_values(by=["final_score", "hybrid_score"], ascending=[False, False], inplace=True)

    rest = merged.iloc[head_n:]
    return pd.concat([top, rest], ignore_index=True)


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


def _ensure_dataset(key: str) -> Tuple[pd.DataFrame, object, object, list | None]:
    with _datasets_lock:
        return _ensure_dataset_locked(key)


def _ensure_dataset_locked(key: str) -> Tuple[pd.DataFrame, object, object, list | None]:
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
        return cache.chunks, cache.vectorizer, cache.matrix, cache.tfidf_chunk_ids

    tfidf_chunk_ids: list | None = None
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
            vectorizer, matrix, tfidf_chunk_ids = load_tfidf_with_ids(key)
        else:
            chunks_df, vectorizer, matrix = _DATASET_LOADERS[key]()
            # 방금 학습된 TF-IDF는 chunks_df 순서와 동일
            tfidf_chunk_ids = chunks_df["chunk_id"].astype(str).tolist() if not chunks_df.empty else None
            chunk_path = DATASET_ARTIFACTS[key].chunk_path
            chunk_mtime = chunk_path.stat().st_mtime if chunk_path.exists() else -1.0
            vectorizer_mtime = (VECTORIZER_DIR / f"{key}_tfidf.pkl").stat().st_mtime if (VECTORIZER_DIR / f"{key}_tfidf.pkl").exists() else -1.0
    except FileNotFoundError:
        chunks_df, vectorizer, matrix = _DATASET_LOADERS[key]()
        tfidf_chunk_ids = chunks_df["chunk_id"].astype(str).tolist() if not chunks_df.empty else None
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
        tfidf_chunk_ids=tfidf_chunk_ids,
    )
    return chunks_df, vectorizer, matrix, tfidf_chunk_ids


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
    if cache.tfidf_chunk_ids is not None:
        cache.tfidf_chunk_ids.append(str(doc_id))
    
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



# 제출 가능한 source_type과 각 타입의 필수(비공백) 필드
_SUBMIT_REQUIRED_FIELDS: Dict[str, List[str]] = {
    "custom_knowledge": ["question", "answer"],
    "event": ["title", "start_date"],
    "announcement": ["title", "content"],
}


def _extract_submitter_department(source_type: str, data: dict) -> str:
    """제출 payload에서 학과명을 일관되게 추출한다.

    지식(custom_knowledge)은 `category`, 행사/공지는 `department`에 학과명이 담겨 온다
    (프론트 DepartmentAdminPage 및 C# A9 확인). 키가 비어 있으면 다른 키로 폴백한다.
    """
    if source_type == "custom_knowledge":
        candidates = [data.get("category"), data.get("department")]
    else:
        candidates = [data.get("department"), data.get("category")]
    for value in candidates:
        if value and str(value).strip():
            return str(value).strip()
    return ""


@app.post("/admin/submit")
async def submit_pending(req: SubmitRequest):
    # K8: source_type 화이트리스트 + data JSON/필수 필드 검증 (fail-fast)
    source_type = (req.source_type or "").strip()
    if source_type not in _SUBMIT_REQUIRED_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 source_type입니다: '{source_type}'",
        )

    try:
        parsed = json.loads(req.data)
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"data가 유효한 JSON이 아닙니다: {exc}")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="data는 JSON 객체여야 합니다.")

    missing = [
        field
        for field in _SUBMIT_REQUIRED_FIELDS[source_type]
        if not str(parsed.get(field, "")).strip()
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"필수 항목이 비어 있습니다: {', '.join(missing)}",
        )

    session = SessionLocal()
    try:
        item = PendingItem(
            source_type=source_type,
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



def _build_notice_from_pending(source_type: str, data: dict) -> Notice | None:
    """제출 payload를 크롤 공지와 동일한 한글 컬럼 의미의 Notice로 변환한다.

    board는 source_type별 고정값으로 통일하고, 학과명은 별도로 보존하지 않고
    content/title에 이미 포함되도록 한다(K5). detail_url은 호출자가 doc_id 확정 후 채운다(K7).
    """
    department = _extract_submitter_department(source_type, data)

    if source_type == "custom_knowledge":
        content = data.get("answer") or ""
        if department:
            content = f"{content}\n\n주관: {department}".strip()
        return Notice(
            board="학과지식",
            title=data.get("question"),
            category="FAQ",
            published_date=datetime.now().strftime("%Y-%m-%d"),
            content=content,
            is_manual=1,
        )

    if source_type == "event":
        content_parts = []
        if data.get("description"):
            content_parts.append(data.get("description"))
        date_str = f"일시: {data.get('start_date')}"
        if data.get("end_date") and data.get("end_date") != data.get("start_date"):
            date_str += f" ~ {data.get('end_date')}"
        content_parts.append(date_str)
        if data.get("location"):
            content_parts.append(f"장소: {data.get('location')}")
        if department:
            content_parts.append(f"주관: {department}")
        return Notice(
            board="학과행사",
            title=data.get("title"),
            category="행사",
            published_date=data.get("start_date"),
            content="\n\n".join(content_parts),
            is_manual=1,
        )

    if source_type == "announcement":
        content = data.get("content") or ""
        if department:
            content = f"{content}\n\n주관: {department}".strip()
        return Notice(
            board="학과공지",
            title=data.get("title"),
            category=data.get("category") or "일반",
            published_date=data.get("date"),
            content=content,
            is_manual=1,
        )

    return None


def _notice_to_ingest_frame(notice: Notice) -> pd.DataFrame:
    """단일 Notice를 ingest의 build_notice_chunks가 기대하는 한글 컬럼 프레임으로 만든다.

    이렇게 해야 크롤 공지와 동일한 doc_id/chunk_id/prefix/clean 규칙을 그대로 탄다(K1/K6).
    """
    return pd.DataFrame([
        {
            "게시판": notice.board or "",
            "제목": notice.title or "",
            "카테고리": notice.category or "",
            "게시일": notice.published_date or "",
            "상단고정": notice.is_fixed or "",
            "상세URL": notice.detail_url or "",
            "본문": notice.content or "",
            "첨부파일": notice.attachments or "[]",
            "db_id": notice.id,
        }
    ])


@app.post("/admin/approve/{item_id}")
async def approve_pending(item_id: int):
    session = SessionLocal()
    chroma_committed_ids: List[str] = []
    target_collection = DATASET_ARTIFACTS["notices"].collection
    try:
        logging.info(f"👉 [Admin] Approving item ID: {item_id}")
        item = session.query(PendingItem).filter(PendingItem.id == item_id).first()
        if not item:
            logging.error(f"❌ [Admin] Item not found: {item_id}")
            raise HTTPException(status_code=404, detail="Item not found")

        if item.status in ("approved", "approved_manually"):
            return {"status": item.status, "message": "이미 승인된 항목입니다."}

        data = json.loads(item.data)
        notice = _build_notice_from_pending(item.source_type, data)

        if notice is None:
            item.status = "approved_manually"
            session.commit()
            return {"status": "approved_manually"}

        # 1. Notice 저장 (id 확보) — 색인 부작용 성공 후 commit하기 위해 flush만 먼저
        session.add(notice)
        session.flush()  # notice.id 확보, 아직 commit 아님
        # K7: 수동 공지에 합성 고유 url 부여 (UNIQUE NULL/"" 충돌 방지 + url 필드 일관 채움)
        notice.detail_url = f"manual://notice/{item.source_type}/{notice.id}"
        session.flush()

        # 2. ingest 공식 경로로 청크 생성 (크롤 공지와 동일 규칙) — K1/K6
        chunks_df = build_notice_chunks(_notice_to_ingest_frame(notice))
        if chunks_df.empty:
            raise HTTPException(status_code=400, detail="청크를 생성할 수 없습니다(본문이 비어 있음).")

        chunk_ids = chunks_df["chunk_id"].astype(str).tolist()
        texts = chunks_df["chunk_text"].astype(str).tolist()

        # 3. 색인 부작용을 DB commit 전에 먼저 수행 (실패 시 롤백 가능) — K3
        embeddings = encode_texts(texts)
        metadatas = chunks_df.drop(columns=["chunk_text"]).to_dict(orient="records")
        metadatas = [{k: (v if v is not None else "") for k, v in m.items()} for m in metadatas]
        upsert_items(
            name=target_collection,
            ids=chunk_ids,
            documents=texts,
            metadatas=metadatas,
            embeddings=embeddings,
        )
        chroma_committed_ids = chunk_ids
        logging.info(f"✅ [Admin] Upserted {len(chunk_ids)} chunk(s) to ChromaDB")

        # 4. DB Chunk 적재 (동일 chunk_id 사용)
        for cid, text in zip(chunk_ids, texts):
            session.add(Chunk(chunk_id=cid, chunk_text=text, notice_id=notice.id))

        # 5. 모든 색인 성공 → 한 번에 commit (Notice + Chunk + status)
        item.status = "approved"
        session.commit()
        logging.info(f"✅ [Admin] Notice {notice.id} approved & committed.")

        # 6. 캐시 리로드 (best-effort, 실패해도 승인은 유효 — DB/Chroma엔 이미 반영됨)
        try:
            with _datasets_lock:
                if "notices" in _datasets:
                    del _datasets["notices"]
                _ensure_dataset_locked("notices")
        except Exception as e:
            logging.error(f"❌ [Admin] Failed to reload notices cache: {e}")

        return {"status": "approved", "chunk_ids": chunk_ids}

    except HTTPException:
        session.rollback()
        # 이미 Chroma에 넣었다면 되돌린다 (부분 적용 방지) — K3
        if chroma_committed_ids:
            try:
                delete_items(target_collection, chroma_committed_ids)
            except Exception:
                logging.error("⚠️ [Admin] Failed to rollback Chroma upsert after error.", exc_info=True)
        raise
    except Exception as e:
        session.rollback()
        if chroma_committed_ids:
            try:
                delete_items(target_collection, chroma_committed_ids)
            except Exception:
                logging.error("⚠️ [Admin] Failed to rollback Chroma upsert after error.", exc_info=True)
        logging.error(f"🔥 [Admin] Critical Error in approve_pending: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.post("/admin/reject/{item_id}")
async def reject_pending(item_id: int):
    session = SessionLocal()
    target_collection = DATASET_ARTIFACTS["notices"].collection
    try:
        item = session.query(PendingItem).filter(PendingItem.id == item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        was_approved = item.status == "approved"
        reload_needed = False

        # K4: 이미 승인·색인된 항목을 반려하면 색인을 되돌린다.
        if was_approved:
            data = json.loads(item.data)
            notice_obj = _build_notice_from_pending(item.source_type, data)
            # 승인 시 생성된 Notice를 title+source 기준으로 찾는다(수동 공지만 대상).
            removed_chunk_ids: List[str] = []
            if notice_obj is not None and notice_obj.title:
                matched_notices = (
                    session.query(Notice)
                    .filter(
                        Notice.is_manual == 1,
                        Notice.title == notice_obj.title,
                        Notice.board == notice_obj.board,
                    )
                    .all()
                )
                for n in matched_notices:
                    chunks = session.query(Chunk).filter(Chunk.notice_id == n.id).all()
                    removed_chunk_ids.extend([c.chunk_id for c in chunks if c.chunk_id])
                    for c in chunks:
                        session.delete(c)
                    session.delete(n)

            if removed_chunk_ids:
                try:
                    delete_items(target_collection, removed_chunk_ids)
                    reload_needed = True
                except Exception:
                    logging.error("⚠️ [Admin] Failed to delete chunks from Chroma on reject.", exc_info=True)

        item.status = "rejected"
        session.commit()

        if reload_needed:
            try:
                with _datasets_lock:
                    if "notices" in _datasets:
                        del _datasets["notices"]
                    _ensure_dataset_locked("notices")
            except Exception as e:
                logging.error(f"❌ [Admin] Failed to reload notices cache on reject: {e}")

        return {"status": "rejected"}
    except HTTPException:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        logging.error(f"🔥 [Admin] Error in reject_pending: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.post("/ask/stream")
async def ask_stream(req: AskRequest, request: Request):
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    raw_query = req.question.strip()
    if not raw_query:
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다.")

    session_id = req.session_id or str(uuid.uuid4())

    async def stream_generator():
        analysis_meta = QueryAnalysisMeta(result=None, used=False, failed=False)
        if USE_QUERY_ANALYSIS:
            # 후속 질문("그럼 신청 기간은?")의 대명사/생략을 해소하기 위해
            # 최근 대화 이력을 함께 전달해 독립형 질문으로 재작성하게 한다.
            history_text = await run_in_threadpool(get_recent_history_text, session_id)
            analysis_result = await analyze_query(raw_query, history_text)
            analysis_meta = _analysis_to_meta(analysis_result, failed=analysis_result is None)

        # 1. 일반 대화 처리 (검색 불필요한 경우)
        if (
            analysis_meta.result is not None
            and analysis_meta.result.intent == "unknown"
            and not _has_school_info_terms(raw_query)
        ):
            current_date = _get_current_kst_string()
            context_text = "일반 대화입니다. 학교 자료 검색이 필요한 질문이 아니면 자연스럽고 짧게 답하세요."
            
            # 메타데이터 전송 (소스 없음)
            yield f"data: {json.dumps({'type': 'metadata', 'sources': [], 'citations': '', 'route': ['unknown'], 'fallback_triggered': False}, ensure_ascii=False)}\n\n"
            
            full_answer = []
            async for chunk in generate_langchain_answer_stream(
                question=raw_query,
                context=context_text,
                session_id=session_id,
                current_date=current_date
            ):
                full_answer.append(chunk)
                yield f"data: {json.dumps({'type': 'text', 'content': chunk}, ensure_ascii=False)}\n\n"
            
            # 로깅은 스트림 종료 후 수행 (별도 태스크로 처리하거나 여기서 대략 수행)
            await run_in_threadpool(
                _save_rag_evaluation_log,
                request_id, session_id, raw_query, raw_query, ["unknown"], "".join(full_answer),
                False, None, False, False, analysis_meta.result.intent,
                json.dumps(analysis_meta.result.entities, ensure_ascii=False),
                analysis_meta.result.time_focus,
                json.dumps(analysis_meta.result.search_queries, ensure_ascii=False),
                analysis_meta.result.needs_clarification, analysis_meta.result.clarification_reason,
                analysis_meta.used, analysis_meta.failed, None, None, []
            )
            return

        # 2. RAG 검색 프로세스
        query_for_retrieval = raw_query
        expanded_query = expand_query(query_for_retrieval)
        retrieval_queries = _build_retrieval_queries(query_for_retrieval, expanded_query, analysis_meta, req.major)
        if raw_query not in retrieval_queries:
            retrieval_queries.insert(0, raw_query)
        semantic_query = analysis_meta.result.normalized_question if analysis_meta.result is not None else expanded_query

        user_major = req.major
        final_where_filter = {}
        # 백엔드는 학과 미지정 시 null을 보낸다("Unknown"/"Default"는 보내지 않지만 방어적으로 함께 제외).
        if user_major and user_major not in _NO_MAJOR_SENTINELS:
            final_where_filter["major"] = {"$eq": user_major}

        routed = await route_query(semantic_query)
        route = _merge_routes(analysis_meta, routed)
        if _should_append_rules_route(semantic_query, route):
            route.append("rules")
        
        entry_year = _extract_entry_year_from_query(semantic_query) or _extract_entry_year_from_query(raw_query)
        date_filter = await run_in_threadpool(extract_date_filter_from_query, semantic_query)
        date_filter_applied = date_filter is not None
        date_filter_relaxed = False
        recent_notice_query = _is_recent_notice_query(semantic_query, route)
        notice_board_filter = _extract_notice_board_filter(semantic_query, route)
        retrieval_policy = _resolve_retrieval_policy(semantic_query, route)

        frames, date_filter_eliminated_any, unavailable_datasets = await _retrieve_frames_for_queries(
            route=route, queries=retrieval_queries, final_where_filter=final_where_filter,
            notice_board_filter=notice_board_filter, date_filter=date_filter, entry_year=entry_year, request_id=request_id
        )

        if not frames and date_filter is not None and date_filter.relaxed_start and date_filter.relaxed_end:
            date_filter_relaxed = True
            relaxed_filter = QueryDateFilter(
                start=date_filter.relaxed_start, end=date_filter.relaxed_end,
                label=f"{date_filter.label}_relaxed", is_relative=date_filter.is_relative
            )
            relaxed_frames, _, relaxed_unavailable = await _retrieve_frames_for_queries(
                route=route, queries=retrieval_queries, final_where_filter=final_where_filter,
                notice_board_filter=notice_board_filter, date_filter=relaxed_filter, entry_year=entry_year, request_id=request_id
            )
            if relaxed_frames:
                frames = relaxed_frames
            unavailable_datasets = list(dict.fromkeys(unavailable_datasets + relaxed_unavailable))

        if not frames:
            merged = pd.DataFrame()
        else:
            merged = _merge_query_hits(frames)

        merged = _prepare_merged_results(merged, recent_notice_query, retrieval_policy, semantic_query, entry_year=entry_year)
        merged = merged.head(DEFAULT_TOP_K).reset_index(drop=True)

        # 3. Fallback 체크
        top_hybrid_score = None
        if not merged.empty and "hybrid_score" in merged.columns:
            top_hybrid_score = _clean_response_float(merged["hybrid_score"].max())

        topic_aligned = _has_notice_topic_alignment(merged, semantic_query)
        min_score = retrieval_policy.min_score
        if recent_notice_query and retrieval_policy.allow_recency_override and not topic_aligned:
            min_score = max(MIN_RETRIEVAL_SCORE, retrieval_policy.min_score)
        allow_recent_answer = (
            retrieval_policy.allow_recency_override
            and recent_notice_query
            and not merged.empty
            and topic_aligned
        )

        fallback_reason = None
        if merged.empty:
            if unavailable_datasets and len(unavailable_datasets) == len(route):
                fallback_reason = FALLBACK_REASON_DATASET_UNAVAILABLE
            elif date_filter_eliminated_any:
                fallback_reason = FALLBACK_REASON_DATE_FILTER_ELIMINATED_ALL
            else:
                fallback_reason = FALLBACK_REASON_NO_RESULTS
        # 결과가 있어도 최고 점수가 임계 미만이면 환각 방지를 위해 폴백 처리한다(비스트리밍 경로와 동일).
        elif top_hybrid_score is not None and not allow_recent_answer and top_hybrid_score < min_score:
            fallback_reason = FALLBACK_REASON_SCORE_BELOW_THRESHOLD

        if fallback_reason is not None:
            fallback_answer = _build_retrieval_fallback_answer(
                route=route, reason=fallback_reason, date_filter_relaxed=date_filter_relaxed,
                policy_name=retrieval_policy.name, clarification_reason=(
                    analysis_meta.result.clarification_reason if analysis_meta.result and analysis_meta.result.needs_clarification else None
                )
            )
            yield f"data: {json.dumps({'type': 'metadata', 'sources': [], 'citations': '', 'route': route, 'fallback_triggered': True, 'fallback_reason': fallback_reason}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'text', 'content': fallback_answer}, ensure_ascii=False)}\n\n"
            
            await run_in_threadpool(
                _save_rag_evaluation_log,
                request_id, session_id, raw_query, expanded_query, route, fallback_answer,
                True, fallback_reason, date_filter_applied, date_filter_relaxed,
                None if analysis_meta.result is None else analysis_meta.result.intent,
                None if analysis_meta.result is None else json.dumps(analysis_meta.result.entities, ensure_ascii=False),
                None if analysis_meta.result is None else analysis_meta.result.time_focus,
                None if analysis_meta.result is None else json.dumps(analysis_meta.result.search_queries, ensure_ascii=False),
                False if analysis_meta.result is None else analysis_meta.result.needs_clarification,
                None if analysis_meta.result is None else analysis_meta.result.clarification_reason,
                analysis_meta.used, analysis_meta.failed, None, top_hybrid_score, []
            )
            await run_in_threadpool(append_manual_history, session_id, raw_query, fallback_answer)
            return

        # 4. 컨텍스트 구성 및 스트리밍 시작
        context_parts = []
        for idx, row in merged.iterrows():
            part = f"문서 {idx+1} [출처: {row.get('source', '알 수 없음')}]:\n"
            if row.get('title'): part += f"제목: {row.get('title')}\n"
            if row.get('published_at'): part += f"게시일: {row.get('published_at')}\n"
            if row.get('url'): part += f"URL: {row.get('url')}\n"
            part += f"내용:\n{_expand_chunk_with_neighbors(row)}\n"
            
            # attachments는 결측 시 NaN(float)일 수 있으므로 문자열인 경우에만 파싱한다.
            attachments_str = row.get('attachments')
            if isinstance(attachments_str, str) and attachments_str.strip():
                try:
                    attachments = json.loads(attachments_str)
                    if isinstance(attachments, list):
                        links = [f"- [{a['name']}]({a['url']})" for a in attachments if isinstance(a, dict) and 'name' in a and 'url' in a]
                        if links: part += "\n첨부파일:\n" + "\n".join(links) + "\n"
                except (json.JSONDecodeError, TypeError) as exc:
                    _log_event(logging.WARNING, "attachment_parse_failed", error=str(exc))
            context_parts.append(part)

        guide_prefix = _build_guide_context_prefix(merged, route, entry_year)
        # 로그인 사용자 소속 학과를 컨텍스트 상단에 명시(개인 맞춤형) + 학번 가이드 프리픽스
        context_prefix = _user_profile_prefix(req.major) + guide_prefix
        # 문서 경계를 존중하며 MAX_CONTEXT_LENGTH 이내로 구성(마지막 문서 중간 절단 방지)
        context_text = _build_context_text(context_parts, MAX_CONTEXT_LENGTH, prefix=context_prefix)
        current_date = _get_current_kst_string()

        # 소스 데이터 정리
        score_cols = {"vector_score", "sparse_score", "hybrid_score", "norm_recency", "final_score"}
        internal_cols = {"chunk_text", "dataset", "sort_date", "sort_timestamp", "norm_hybrid", "recent_notice_priority", "matched_query_count", "query_match_bonus"} | score_cols
        sources = [
            SourceChunk(
                source=_clean_response_str(row.get("dataset")) or "",
                metadata={col: _clean_response_value(row.get(col)) for col in row.index if col not in internal_cols},
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
            ).dict()
            for _, row in merged.iterrows()
        ]
        
        citations_raw = await run_in_threadpool(format_citations, merged)
        citations = re.sub(r'<[^>]+>', '', citations_raw)

        # 메타데이터 먼저 전송
        yield f"data: {json.dumps({'type': 'metadata', 'sources': sources, 'citations': citations, 'route': route, 'fallback_triggered': False}, ensure_ascii=False)}\n\n"

        # 답변 스트리밍 시작
        full_answer = []
        guide_ans_prefix = _build_guide_answer_prefix(merged, route, entry_year)
        if guide_ans_prefix:
            full_answer.append(guide_ans_prefix)
            yield f"data: {json.dumps({'type': 'text', 'content': guide_ans_prefix}, ensure_ascii=False)}\n\n"

        async for chunk in generate_langchain_answer_stream(
            question=semantic_query,
            context=context_text,
            session_id=session_id,
            current_date=current_date
        ):
            full_answer.append(chunk)
            yield f"data: {json.dumps({'type': 'text', 'content': chunk}, ensure_ascii=False)}\n\n"

        # 최종 로깅
        final_answer = "".join(full_answer)
        await run_in_threadpool(
            _save_rag_evaluation_log,
            request_id, session_id, raw_query, expanded_query, route, final_answer,
            False, None, date_filter_applied, date_filter_relaxed,
            None if analysis_meta.result is None else analysis_meta.result.intent,
            None if analysis_meta.result is None else json.dumps(analysis_meta.result.entities, ensure_ascii=False),
            None if analysis_meta.result is None else analysis_meta.result.time_focus,
            None if analysis_meta.result is None else json.dumps(analysis_meta.result.search_queries, ensure_ascii=False),
            False if analysis_meta.result is None else analysis_meta.result.needs_clarification,
            None if analysis_meta.result is None else analysis_meta.result.clarification_reason,
            analysis_meta.used, analysis_meta.failed, 
            json.dumps(_collect_matched_queries(merged), ensure_ascii=False),
            top_hybrid_score, 
            [SourceChunk(**s) for s in sources]
        )

    return StreamingResponse(stream_generator(), media_type="text/event-stream")

@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest, request: Request) -> AskResponse:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    raw_query = req.question.strip()
    if not raw_query:
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다.")

    session_id = req.session_id or str(uuid.uuid4())

    analysis_meta = QueryAnalysisMeta(result=None, used=False, failed=False)
    if USE_QUERY_ANALYSIS:
        # 후속 질문의 대명사/생략 해소를 위해 최근 대화 이력을 함께 전달(스트리밍 경로와 동일)
        history_text = await run_in_threadpool(get_recent_history_text, session_id)
        analysis_result = await analyze_query(raw_query, history_text)
        analysis_meta = _analysis_to_meta(analysis_result, failed=analysis_result is None)

    if (
        analysis_meta.result is not None
        and analysis_meta.result.intent == "unknown"
        and not _has_school_info_terms(raw_query)
    ):
        current_date = _get_current_kst_string()
        context_text = "일반 대화입니다. 학교 자료 검색이 필요한 질문이 아니면 자연스럽고 짧게 답하세요."
        try:
            answer = await generate_langchain_answer(
                question=raw_query,
                context=context_text,
                session_id=session_id,
                current_date=current_date,
            )
        except Exception:
            _log_event(logging.ERROR, "llm_generation_failed", exc_info=True, request_id=request_id)
            answer = "저는 동똑이에요. 지금 응답이 잠깐 매끄럽지 않았지만, 계속 편하게 물어보셔도 됩니다."

        await run_in_threadpool(
            _save_rag_evaluation_log,
            request_id,
            session_id,
            raw_query,
            raw_query,
            ["unknown"],
            answer,
            False,
            None,
            False,
            False,
            analysis_meta.result.intent,
            json.dumps(analysis_meta.result.entities, ensure_ascii=False),
            analysis_meta.result.time_focus,
            json.dumps(analysis_meta.result.search_queries, ensure_ascii=False),
            analysis_meta.result.needs_clarification,
            analysis_meta.result.clarification_reason,
            analysis_meta.used,
            analysis_meta.failed,
            None,
            None,
            [],
        )
        return AskResponse(
            answer=answer,
            citations="",
            route=["unknown"],
            sources=[],
            fallback_triggered=False,
            fallback_reason=None,
        )

    query_for_retrieval = raw_query
    expanded_query = expand_query(query_for_retrieval)
    retrieval_queries = _build_retrieval_queries(query_for_retrieval, expanded_query, analysis_meta, req.major)
    if raw_query not in retrieval_queries:
        retrieval_queries.insert(0, raw_query)
    semantic_query = analysis_meta.result.normalized_question if analysis_meta.result is not None else expanded_query
    _log_event(
        logging.INFO,
        "ask_started",
        request_id=request_id,
        raw_query=raw_query,
        query_for_retrieval=query_for_retrieval,
        expanded_query=expanded_query,
        retrieval_queries=retrieval_queries,
        analysis_intent=None if analysis_meta.result is None else analysis_meta.result.intent,
    )

    # 로그에 처리된 질문과 세션 ID를 출력하여 디버깅을 돕습니다.
    _log_event(logging.INFO, "ask_session", request_id=request_id, session_id=session_id)

    user_major = req.major
    final_where_filter: Dict = {}
    # 백엔드는 학과 미지정 시 null을 보낸다("Unknown"/"Default"는 보내지 않지만 방어적으로 함께 제외).
    if user_major and user_major not in _NO_MAJOR_SENTINELS:
        final_where_filter["major"] = {"$eq": user_major}

    _log_event(logging.INFO, "ask_filters", request_id=request_id, filters=final_where_filter)
    routed = await route_query(
        analysis_meta.result.normalized_question
        if analysis_meta.result is not None
        else expanded_query
    )
    route = _merge_routes(analysis_meta, routed)
    if _should_append_rules_route(semantic_query, route):
        route.append("rules")
    entry_year = _extract_entry_year_from_query(semantic_query) or _extract_entry_year_from_query(raw_query)
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
        entry_year=entry_year,
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
            entry_year=entry_year,
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

    merged = _prepare_merged_results(merged, recent_notice_query, retrieval_policy, semantic_query, entry_year=entry_year)
    merged = merged.head(DEFAULT_TOP_K).reset_index(drop=True)

    def _evaluate_fallback(current_merged: pd.DataFrame) -> tuple[float | None, bool, float, str | None]:
        top_score = None
        if not current_merged.empty and "hybrid_score" in current_merged.columns:
            top_score = _clean_response_float(current_merged["hybrid_score"].max())

        topic_aligned = _has_notice_topic_alignment(current_merged, semantic_query)
        allow_recent_answer = (
            retrieval_policy.allow_recency_override
            and recent_notice_query
            and not current_merged.empty
            and topic_aligned
        )
        min_score = retrieval_policy.min_score
        if recent_notice_query and retrieval_policy.allow_recency_override and not topic_aligned:
            min_score = max(MIN_RETRIEVAL_SCORE, retrieval_policy.min_score)

        reason = None
        if current_merged.empty:
            if unavailable_datasets and len(unavailable_datasets) == len(route):
                reason = FALLBACK_REASON_DATASET_UNAVAILABLE
            elif date_filter_eliminated_any:
                reason = FALLBACK_REASON_DATE_FILTER_ELIMINATED_ALL
            else:
                reason = FALLBACK_REASON_NO_RESULTS
        # 결과가 있어도 최고 점수가 임계 미만이면 환각 방지를 위해 폴백 처리한다.
        # 단, 최신 공지 질의에서 recency override가 허용되고 주제가 일치하면 낮은 점수도 통과시킨다.
        elif top_score is not None and not allow_recent_answer and top_score < min_score:
            reason = FALLBACK_REASON_SCORE_BELOW_THRESHOLD
        return top_score, topic_aligned, min_score, reason

    top_hybrid_score, notice_topic_aligned, effective_min_score, fallback_reason = _evaluate_fallback(merged)

    if fallback_reason is not None and "courses" in route and "rules" not in route:
        guide_frames, _, guide_unavailable = await _retrieve_frames_for_queries(
            route=["rules"],
            queries=retrieval_queries,
            final_where_filter=final_where_filter,
            notice_board_filter=None,
            date_filter=None,
            entry_year=entry_year,
            request_id=request_id,
        )
        unavailable_datasets = list(dict.fromkeys(unavailable_datasets + guide_unavailable))
        if guide_frames:
            frames.extend(guide_frames)
            route.append("rules")
            merged = _merge_query_hits(frames)
            merged = _prepare_merged_results(merged, recent_notice_query, retrieval_policy, semantic_query, entry_year=entry_year)
            merged = merged.head(DEFAULT_TOP_K).reset_index(drop=True)
            top_hybrid_score, notice_topic_aligned, effective_min_score, fallback_reason = _evaluate_fallback(merged)

    matched_queries = _collect_matched_queries(merged)

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
        part += f"내용:\n{_expand_chunk_with_neighbors(row)}\n"
        
        # --- NEW ATTACHMENT PROCESSING ---
        # attachments는 결측 시 NaN(float)일 수 있으므로 문자열인 경우에만 파싱한다.
        attachments_str = row.get('attachments')
        if isinstance(attachments_str, str) and attachments_str.strip():
            try:
                attachments = json.loads(attachments_str)

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
    
    # 로그인 사용자 소속 학과를 컨텍스트 상단에 명시(개인 맞춤형) + 학번 가이드 프리픽스
    guide_context_prefix = _user_profile_prefix(req.major) + _build_guide_context_prefix(merged, route, entry_year)
    if context_parts:
        # 문서 경계를 존중하며 MAX_CONTEXT_LENGTH 이내로 구성(마지막 문서 중간 절단 방지)
        context_text = _build_context_text(context_parts, MAX_CONTEXT_LENGTH, prefix=guide_context_prefix)
    else:
        context_text = (guide_context_prefix + "검색된 관련 문서가 없습니다. 일반적인 대화로 응답해주세요.")[:MAX_CONTEXT_LENGTH]
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

    guide_answer_prefix = _build_guide_answer_prefix(merged, route, entry_year)
    if guide_answer_prefix and not answer.startswith(guide_answer_prefix):
        answer = guide_answer_prefix + answer

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
            with _datasets_lock:
                _datasets.clear()
        elif target in _datasets:
            with _datasets_lock:
                _datasets.pop(target, None)

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
