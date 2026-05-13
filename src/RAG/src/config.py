"""프로젝트 전반에서 사용하는 설정과 상수 모음입니다."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

# 주요 파일 시스템 경로를 정의한다.
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ARTIFACT_DIR = BASE_DIR / "artifacts"
CHROMA_DIR = ARTIFACT_DIR / "db_chroma"
MODEL_DIR = ARTIFACT_DIR / "models"
VECTORIZER_DIR = ARTIFACT_DIR / "vectorizers"
CHUNKS_DIR = ARTIFACT_DIR / "chunks"
RAW_DIR = ARTIFACT_DIR / "raw"
NORMALIZED_DIR = ARTIFACT_DIR / "normalized"

# 모듈이 임포트될 때 필요한 디렉터리를 미리 만든다.
for _path in (DATA_DIR, ARTIFACT_DIR, CHROMA_DIR, MODEL_DIR, VECTORIZER_DIR, CHUNKS_DIR, RAW_DIR, NORMALIZED_DIR):
    _path.mkdir(parents=True, exist_ok=True)

# 임베딩 관련 설정.
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL", "nlpai-lab/KURE-v1")
EMBED_DEVICE = os.getenv("EMBED_DEVICE", "cpu")
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "8"))

# 청크 분할과 검색 기본값.
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "300")) # 청크 크기 기본값
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "80")) # 청크 겹침 기본값
HYBRID_ALPHA = float(os.getenv("HYBRID_ALPHA", "0.4")) # 하이브리드 검색 가중치 기본값
DEFAULT_TOP_K = int(os.getenv("DEFAULT_TOP_K", "5")) # 검색 결과 개수 기본값
RECENCY_WEIGHT = float(os.getenv("RECENCY_WEIGHT", "0.4")) # Re-ranking 가중치 추가
MIN_RETRIEVAL_SCORE = float(os.getenv("MIN_RETRIEVAL_SCORE", "0.12")) # 검색 실패 판단 최소 하이브리드 점수
RECENCY_DECAY_DAYS_BY_DATASET = {
    "notices": float(os.getenv("RECENCY_DECAY_DAYS_NOTICES", "90")),
    "schedule": float(os.getenv("RECENCY_DECAY_DAYS_SCHEDULE", "180")),
    "rules": float(os.getenv("RECENCY_DECAY_DAYS_RULES", "1095")),
}

# 컨텍스트 관련 설정
MAX_CONTEXT_LENGTH = int(os.getenv("MAX_CONTEXT_LENGTH", "4000"))

# LLM 라우터가 각 데이터셋의 역할을 이해하는 데 사용하는 설명
LLM_ROUTER_DESCRIPTIONS = {
    "notices": "학교 생활 전반에 걸친 공지사항, 모집, 발표, 장학금, 등록금, 입시, 휴학, 복학 관련 안내입니다.",
    "rules": "학사 운영, 졸업, 성적, 징계 등 학교의 공식적인 학칙, 규정, 시행세칙에 대한 정보입니다.",
    "schedule": "수강신청, 개강, 종강, 방학, 시험 등 주요 학사일정에 대한 정보입니다.",
    "courses": "개설된 교과목, 수업, 강의, 전공, 선수과목, 학점, 이수구분 등 교과 과정에 대한 상세 정보입니다.",
    "staff": "교직원, 교수, 행정 부서의 연락처, 담당 업무 정보입니다.",
}

# OpenAI/LLM 기본 설정.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "qwen3:4b-instruct-2507-q4_K_M")
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120"))
USE_QUERY_ANALYSIS = os.getenv("RAG_USE_QUERY_ANALYSIS", "1") == "1"
QUERY_ANALYSIS_MAX_QUERIES = int(os.getenv("QUERY_ANALYSIS_MAX_QUERIES", "1"))

# 대화 기록 관련 설정 (인메모리).
MAX_HISTORY_STORE_SIZE = int(os.getenv("MAX_HISTORY_STORE_SIZE", "1000"))

# Redis 대화 기록 백엔드 설정.
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380/0")

# 노트북에서 가져온 입력 데이터 소스 경로.
DATA_SOURCES: Dict[str, Path] = {
    "notices": DATA_DIR / "dongguk_notices.csv",
    "rules": DATA_DIR / "dongguk_rule_texts.csv",
    "rules_entry_year_guides": DATA_DIR / "dongguk_entry_year_guide_sections.csv",
    "schedule": DATA_DIR / "dongguk_schedule.csv",
    "courses_desc": DATA_DIR / "dongguk_statistics_course_descriptions.csv",
    "courses_major": DATA_DIR / "dongguk_statistics_major_course.csv",
    "courses_all": DATA_DIR / "dongguk_courses_all.csv",
    "courses_catalog": DATA_DIR / "dongguk_departments_catalog.csv",
    "courses_curriculum_sources": DATA_DIR / "dongguk_department_curriculum_sources.csv",
    "staff": DATA_DIR / "dongguk_staff_contacts.csv",
}


__all__ = [
    "BASE_DIR",
    "DATA_DIR",
    "ARTIFACT_DIR",
    "CHROMA_DIR",
    "MODEL_DIR",
    "VECTORIZER_DIR",
    "CHUNKS_DIR",
    "RAW_DIR",
    "NORMALIZED_DIR",
    "EMBED_MODEL_NAME",
    "EMBED_DEVICE",
    "EMBED_BATCH_SIZE",
    "CHUNK_SIZE",
    "CHUNK_OVERLAP",
    "HYBRID_ALPHA",
    "DEFAULT_TOP_K",
    "MIN_RETRIEVAL_SCORE",
    "RECENCY_DECAY_DAYS_BY_DATASET",
    "MAX_CONTEXT_LENGTH",
    "LLM_ROUTER_DESCRIPTIONS",
    "OPENAI_MODEL",
    "OPENAI_API_KEY",
    "OLLAMA_BASE_URL",
    "OLLAMA_CHAT_MODEL",
    "OLLAMA_TIMEOUT_SECONDS",
    "USE_QUERY_ANALYSIS",
    "QUERY_ANALYSIS_MAX_QUERIES",
    "MAX_HISTORY_STORE_SIZE",
    "REDIS_URL",
    "DATA_SOURCES",
]
