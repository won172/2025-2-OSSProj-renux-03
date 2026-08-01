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
# 기본 KURE-v1(무료, MIT)은 한국어 검색 벤치마크 최상위. 더 가벼운 무료 대안으로
# 교체할 때는 EMBED_MODEL과 함께 프리픽스 설정이 필요할 수 있다:
#   intfloat/multilingual-e5-small (118M, 5배 가벼움):
#     EMBED_QUERY_PREFIX="query: "  EMBED_PASSAGE_PREFIX="passage: "
#   Alibaba-NLP/gte-multilingual-base (305M): 프리픽스 불필요
# ⚠️ 모델 교체 시 반드시 scripts/build_indices.py로 전체 재인덱싱 필요(벡터 호환 안 됨).
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL", "nlpai-lab/KURE-v1")
EMBED_DEVICE = os.getenv("EMBED_DEVICE", "cpu")
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "8"))
# E5 계열처럼 질의/문서에 서로 다른 프리픽스를 요구하는 모델 지원(KURE/BGE-M3는 빈 값).
EMBED_QUERY_PREFIX = os.getenv("EMBED_QUERY_PREFIX", "")
EMBED_PASSAGE_PREFIX = os.getenv("EMBED_PASSAGE_PREFIX", "")
# HuggingFace 모델 로드 시 저장소 동봉 파이썬 코드를 실행할지 여부(trust_remote_code).
# ⚠️ 공급망 위험: 신뢰할 수 없는 모델은 로드 중 임의 코드를 실행할 수 있으므로 기본 비활성.
# 기본 모델(KURE-v1, bge-reranker-v2-m3)은 표준 XLM-RoBERTa 구조라 이 플래그가 필요 없다.
# gte-multilingual-base처럼 커스텀 모델링 코드를 쓰는 모델만, 고정 리비전과 함께 1로 켠다.
MODEL_TRUST_REMOTE_CODE = os.getenv("MODEL_TRUST_REMOTE_CODE", "0") == "1"
# 모델 리비전 고정(공급망 무결성). 비우면 HF 기본(main 최신)을 따른다 — 운영은 커밋 해시 권장.
EMBED_MODEL_REVISION = os.getenv("EMBED_MODEL_REVISION") or None

# Cross-encoder 리랭커 (정확도 향상 — 하이브리드 top-N 후보를 질의-문서 쌍으로 정밀 재정렬).
# 모델(~2GB) 다운로드와 CPU 추론 지연(쿼리당 1~5초)이 있어 기본 비활성.
# 켜려면: RERANKER_ENABLED=1 (모델은 무료, 최초 1회 자동 다운로드)
RERANKER_ENABLED = os.getenv("RERANKER_ENABLED", "0") == "1"
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
RERANKER_CANDIDATES = int(os.getenv("RERANKER_CANDIDATES", "20"))  # 재정렬할 상위 후보 수
RERANKER_MODEL_REVISION = os.getenv("RERANKER_MODEL_REVISION") or None  # 리랭커 리비전 고정(선택)
RERANKER_MODE = os.getenv("RERANKER_MODE", "conditional").strip().lower()
RERANKER_MAX_TOP_GAP = float(os.getenv("RERANKER_MAX_TOP_GAP", "0.08"))
RERANKER_MIN_TEXT_CHARS = int(os.getenv("RERANKER_MIN_TEXT_CHARS", "450"))

# TF-IDF 아티팩트(*.pkl) 무결성 검증.
# pkl은 joblib.load 시 임의 코드를 실행할 수 있어, 학습 시 기록한 SHA-256 매니페스트와
# 로드 직전 파일 해시를 대조한다. 불일치 시 fail-closed(로드 거부).
# TFIDF_VERIFY_INTEGRITY=0 으로 끌 수 있으나 운영에서는 끄지 말 것(검증 우회).
TFIDF_VERIFY_INTEGRITY = os.getenv("TFIDF_VERIFY_INTEGRITY", "1") == "1"
# 매니페스트에 해당 데이터셋 항목이 아예 없을 때의 동작.
# 기본(0): 경고만 하고 로드 허용(신규 데이터셋·부트스트랩 호환).
# 1: 매니페스트 미등록 아티팩트도 로드 거부(엄격 모드 — 운영 권장).
TFIDF_REQUIRE_MANIFEST = os.getenv("TFIDF_REQUIRE_MANIFEST", "0") == "1"
# Sparse 검색 토크나이저. "korean"은 Kiwi가 설치되어 있으면 형태소 분석을 사용하고,
# 없으면 조사/어미와 띄어쓰기 차이에 강한 경량 한국어 토크나이저로 폴백한다.
# "default"로 두면 scikit-learn 기본 token_pattern을 사용한다.
TFIDF_TOKENIZER = os.getenv("TFIDF_TOKENIZER", "korean").strip().lower()

# Parent-document 확장: 검색은 작은 청크로 하되, 생성 컨텍스트에는 같은 문서의
# 이웃 청크(앞뒤 1개)를 함께 제공해 잘린 근거를 보완한다(추가 비용 없음, 기본 활성).
PARENT_CONTEXT_ENABLED = os.getenv("PARENT_CONTEXT_ENABLED", "1") == "1"

# 청크 분할과 검색 기본값. 300/600/900자 Contextual BM25 A/B에서
# 장문 공지·규정의 recall@20이 600자에서 가장 높아 기본값을 600자로 정했다.
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "600"))
# 일정·교과·연락처는 정형 행 단위 의미를 유지한다. 장문 청킹 실험의 영향이
# 이 데이터셋까지 번지지 않도록 종전 300자 기준을 별도로 고정한다.
STRUCTURED_CHUNK_SIZE = int(os.getenv("STRUCTURED_CHUNK_SIZE", "300"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "80")) # 청크 겹침 기본값
HYBRID_ALPHA = float(os.getenv("HYBRID_ALPHA", "0.4")) # 하이브리드 검색 가중치 기본값
HYBRID_FUSION_MODE = os.getenv("HYBRID_FUSION_MODE", "rrf").strip().lower()
HYBRID_RRF_K = int(os.getenv("HYBRID_RRF_K", "60"))
DEFAULT_TOP_K = int(os.getenv("DEFAULT_TOP_K", "5")) # 검색 결과 개수 기본값
RECENCY_WEIGHT = float(os.getenv("RECENCY_WEIGHT", "0.4")) # Re-ranking 가중치 추가
MIN_RETRIEVAL_SCORE = float(os.getenv("MIN_RETRIEVAL_SCORE", "0.12")) # 검색 실패 판단 최소 하이브리드 점수
# RRF 점수는 절대 관련도가 아니라 순위 합의도다. 질의와 근거의 어휘 접점이 전혀
# 없을 때는 원래 dense/BM25 점수 중 하나가 이 값 이상이어야 생성 단계로 보낸다.
RRF_UNALIGNED_MIN_COMPONENT_SCORE = float(
    os.getenv("RRF_UNALIGNED_MIN_COMPONENT_SCORE", "0.40")
)
RECENCY_DECAY_DAYS_BY_DATASET = {
    "notices": float(os.getenv("RECENCY_DECAY_DAYS_NOTICES", "90")),
    "schedule": float(os.getenv("RECENCY_DECAY_DAYS_SCHEDULE", "180")),
    "rules": float(os.getenv("RECENCY_DECAY_DAYS_RULES", "1095")),
    # 학식은 매일 갱신 — 지난 메뉴는 빠르게 감쇠시키고 당일/이번주 메뉴를 선호한다.
    # (미래 날짜 메뉴는 recency가 1.0으로 클램프되어 다가오는 식단이 우대됨)
    "meals": float(os.getenv("RECENCY_DECAY_DAYS_MEALS", "4")),
}
# "현재 진행 중" 공지에서 마감일을 추출하지 못한 경우, 최근 게시물만
# 보수적으로 남길 수 있는 최대 게시 경과일. 마감일 미상 문서를 전부 버리지는 않는다.
ACTIVE_NOTICE_UNKNOWN_MAX_AGE_DAYS = int(
    os.getenv("ACTIVE_NOTICE_UNKNOWN_MAX_AGE_DAYS", "90")
)

# 컨텍스트 관련 설정
MAX_CONTEXT_LENGTH = int(os.getenv("MAX_CONTEXT_LENGTH", "4000"))

# LLM 라우터가 각 데이터셋의 역할을 이해하는 데 사용하는 설명
LLM_ROUTER_DESCRIPTIONS = {
    "notices": "학교 생활 전반에 걸친 공지사항, 모집, 발표, 장학금, 등록금, 입시, 휴학, 복학 관련 안내입니다.",
    "rules": "학사 운영, 졸업, 성적, 징계 등 학교의 공식적인 학칙, 규정, 시행세칙에 대한 정보입니다.",
    "schedule": "수강신청, 개강, 종강, 방학, 시험 등 주요 학사일정에 대한 정보입니다.",
    "courses": "개설된 교과목, 수업, 강의, 전공, 선수과목, 학점, 이수구분 등 교과 과정에 대한 상세 정보입니다.",
    "staff": "교직원, 교수, 행정 부서의 연락처, 담당 업무 정보입니다.",
    "meals": "학생식당(상록원 1/2/3층, 솥앤누들, 누리터, 경영관 D-Flex) 학식 식단, 오늘/이번주 메뉴, 중식·석식, 가격 정보입니다.",
}

# OpenAI/LLM 기본 설정.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # 질의분석/라우터용 (항상 OpenAI)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
RAG_REQUIRE_OPENAI_API_KEY = os.getenv("RAG_REQUIRE_OPENAI_API_KEY", "0") == "1"
# 검증된 SHA-256 전사 캐시는 항상 사용하고, 캐시 미스만 선택한 vision 모델로
# 전사한다. 신규 공지에만 적용되므로 기존 전체 코퍼스를 반복 호출하지 않는다.
RAG_NOTICE_IMAGE_OCR_ENABLED = os.getenv("RAG_NOTICE_IMAGE_OCR_ENABLED", "1") == "1"
RAG_NOTICE_IMAGE_OCR_MODEL = os.getenv("RAG_NOTICE_IMAGE_OCR_MODEL", "gpt-4o-mini")
RAG_NOTICE_IMAGE_OCR_MAX_IMAGES = int(os.getenv("RAG_NOTICE_IMAGE_OCR_MAX_IMAGES", "2"))
RAG_NOTICE_IMAGE_OCR_MAX_BYTES = int(
    os.getenv("RAG_NOTICE_IMAGE_OCR_MAX_BYTES", str(5 * 1024 * 1024))
)
RAG_NOTICE_IMAGE_OCR_MIN_TEXT_CHARS = int(
    os.getenv("RAG_NOTICE_IMAGE_OCR_MIN_TEXT_CHARS", "20")
)
RAG_ROUTER_CACHE_TTL_SECONDS = int(os.getenv("RAG_ROUTER_CACHE_TTL_SECONDS", "300"))
RAG_SEMANTIC_CACHE_ENABLED = os.getenv("RAG_SEMANTIC_CACHE_ENABLED", "0") == "1"
RAG_SEMANTIC_CACHE_THRESHOLD = float(os.getenv("RAG_SEMANTIC_CACHE_THRESHOLD", "0.97"))
RAG_SEMANTIC_CACHE_TTL_SECONDS = int(os.getenv("RAG_SEMANTIC_CACHE_TTL_SECONDS", "1800"))
RAG_SEMANTIC_CACHE_MAX = int(os.getenv("RAG_SEMANTIC_CACHE_MAX", "500"))

# 답변 생성 프로바이더 선택: "openai" 또는 "ollama".
# LLM_PROVIDER 로 전환하며, 둘 다 LangChain 채팅 인터페이스(.ainvoke/.astream)를 사용한다.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").strip().lower()
# 생성 실패 시 반대 프로바이더로 자동 폴백할지 여부 (비스트리밍 경로에만 적용).
LLM_FALLBACK_ENABLED = os.getenv("LLM_FALLBACK_ENABLED", "1") == "1"

# 답변 생성용 OpenAI 모델 설정.
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_CHAT_TEMPERATURE = float(os.getenv("OPENAI_CHAT_TEMPERATURE", "0.2"))
OPENAI_CHAT_TIMEOUT_SECONDS = int(os.getenv("OPENAI_CHAT_TIMEOUT_SECONDS", "60"))
OPENAI_CHAT_MAX_RETRIES = int(os.getenv("OPENAI_CHAT_MAX_RETRIES", "2"))
# Cost estimation is based on configurable USD prices per 1M text tokens.
# Keep these values aligned with the billing page for the deployed model.
OPENAI_CHAT_INPUT_COST_PER_1M = float(os.getenv("OPENAI_CHAT_INPUT_COST_PER_1M", "0"))
OPENAI_CHAT_OUTPUT_COST_PER_1M = float(os.getenv("OPENAI_CHAT_OUTPUT_COST_PER_1M", "0"))

# 답변 생성용 로컬(Ollama) 모델 설정.
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "qwen3:4b-instruct-2507-q4_K_M")
OLLAMA_CHAT_TEMPERATURE = float(os.getenv("OLLAMA_CHAT_TEMPERATURE", "0.2"))
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120"))
OLLAMA_REQUEST_RETRIES = int(os.getenv("OLLAMA_REQUEST_RETRIES", "2"))
USE_QUERY_ANALYSIS = os.getenv("RAG_USE_QUERY_ANALYSIS", "1") == "1"
QUERY_ANALYSIS_MAX_QUERIES = int(os.getenv("QUERY_ANALYSIS_MAX_QUERIES", "1"))

# 질의 분해(멀티 데이터셋 검색) 설정.
# 졸업/수강 계획처럼 한 질문이 요건(rules)+과목(courses)+일정(schedule)+연락처(staff) 등
# 여러 데이터셋을 동시에 필요로 할 때, 질의분석기가 질문을 측면별 서브쿼리로 분해하고
# 필요한 데이터셋 합집합을 라우트에 더해 융합 답변을 만들도록 한다. 단순 질문은 영향받지 않는다.
RAG_DECOMPOSE_ENABLED = os.getenv("RAG_DECOMPOSE_ENABLED", "1") == "1"
# 복합 질문에서 사용할 분해 서브쿼리 최대 개수(라우트 데이터셋 수와 곱해져 검색 횟수가 되므로 과도하지 않게).
RAG_MAX_SUBQUERIES = int(os.getenv("RAG_MAX_SUBQUERIES", "4"))
# 답변 이후 사용자가 이어서 물어볼 만한 추천 후속질문 생성.
RAG_SUGGEST_FOLLOWUPS = os.getenv("RAG_SUGGEST_FOLLOWUPS", "1") == "1"
RAG_SUGGEST_FOLLOWUPS_COUNT = int(os.getenv("RAG_SUGGEST_FOLLOWUPS_COUNT", "5"))
# 생성된 답변이 검색 컨텍스트로 뒷받침되는지 사후 점검한다.
RAG_GROUNDING_CHECK_ENABLED = os.getenv("RAG_GROUNDING_CHECK_ENABLED", "1") == "1"
RAG_COLLEGE_SCOPE_ENABLED = os.getenv("RAG_COLLEGE_SCOPE_ENABLED", "1") == "1"
RAG_GROUNDING_MIN_SCORE = float(os.getenv("RAG_GROUNDING_MIN_SCORE", "0.5"))
RAG_GROUNDING_FAILURE_POLICY = os.getenv(
    "RAG_GROUNDING_FAILURE_POLICY",
    "replace",
).strip().lower()
# grounding 실패 시 이미 전송한 토큰을 회수할 수 없으므로, 검증을 켠 스트림은 기본적으로
# 생성 결과를 메모리에 모아 검증한 뒤 한 번에 내보낸다.
RAG_STREAM_BUFFER_UNTIL_GROUNDED = (
    os.getenv("RAG_STREAM_BUFFER_UNTIL_GROUNDED", "1") == "1"
)
# 질의 분석의 intent와 안전한 키워드 보강으로 필요한 데이터셋만 검색한다.
# 장애 진단 등에서만 환경변수로 전체 검색을 명시적으로 켤 수 있다.
RAG_SEARCH_ALL_DATASETS = os.getenv("RAG_SEARCH_ALL_DATASETS", "0") == "1"
# 검색 시 질문을 여러 서브쿼리로 펼치지 않고, 사용자 질문 1개로만 검색한다.
RAG_SINGLE_QUERY_RETRIEVAL = os.getenv("RAG_SINGLE_QUERY_RETRIEVAL", "1") == "1"
# 하이브리드 검색은 이 개수의 결과를 데이터셋별 후단 정렬에 넘긴다. 내부 dense/sparse
# 후보는 5배(기본 100개)까지 모으므로 recency가 top-k 절단 전에 개입할 여지가 생긴다.
RAG_RETRIEVAL_TOP_K_PER_DATASET = int(
    os.getenv("RAG_RETRIEVAL_TOP_K_PER_DATASET", "20")
)
# 재현 가능한 골든 테스트/과거 시점 검증에서만 요청의 asOf 값을 허용한다.
# 운영 기본값에서는 클라이언트가 임의의 과거 시점을 현재 답변처럼 만들 수 없게 막는다.
RAG_ALLOW_AS_OF_OVERRIDE = os.getenv("RAG_ALLOW_AS_OF_OVERRIDE", "0") == "1"
# 전체 데이터셋 검색 뒤 OpenAI가 직접 관련성/중복/충돌을 판정할 때의 비용 상한.
# 데이터셋별 후보 수를 먼저 제한해 특정 코퍼스가 shortlist를 독점하지 않게 한다.
RAG_EVIDENCE_CANDIDATES_PER_DATASET = int(os.getenv("RAG_EVIDENCE_CANDIDATES_PER_DATASET", "3"))
RAG_EVIDENCE_MAX_CANDIDATES = int(os.getenv("RAG_EVIDENCE_MAX_CANDIDATES", "18"))
RAG_EVIDENCE_TEXT_CHARS = int(os.getenv("RAG_EVIDENCE_TEXT_CHARS", "700"))
RAG_EVIDENCE_TIMEOUT_SECONDS = int(os.getenv("RAG_EVIDENCE_TIMEOUT_SECONDS", "20"))

# 데이터 자동 갱신 스케줄러 (rag-service 프로세스 내 APScheduler).
# 이미 로드된 임베딩 모델을 재사용하고 Chroma 클라이언트를 단일 프로세스가 소유하므로
# 별도 워커 컨테이너 대비 메모리·동시성 안전성이 좋다. 기본 비활성(배포 env에서 1로 켠다).
RAG_SCHEDULER_ENABLED = os.getenv("RAG_SCHEDULER_ENABLED", "0") == "1"
# 공지 갱신 주기(시간). 기본 6시간 = 하루 4회.
RAG_NOTICES_REFRESH_HOURS = float(os.getenv("RAG_NOTICES_REFRESH_HOURS", "6"))
# 학식 갱신 주기(시간). 기본 24시간 = 매일.
RAG_MEALS_REFRESH_HOURS = float(os.getenv("RAG_MEALS_REFRESH_HOURS", "24"))
# 공지 갱신 시 게시판당 페이지 수(known-id 조기 중단의 안전 상한).
RAG_NOTICES_REFRESH_MAX_PAGES = int(os.getenv("RAG_NOTICES_REFRESH_MAX_PAGES", "30"))
# 스케줄러 크롤 요청 한 번당 제한과 최대 시도 횟수. 수동 크롤러 기본값과 분리해
# 백그라운드 갱신이 장시간 네트워크 대기에 묶이지 않도록 한다.
RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS = float(
    os.getenv("RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS", "15")
)
RAG_SCHEDULER_REQUEST_RETRIES = int(os.getenv("RAG_SCHEDULER_REQUEST_RETRIES", "2"))
# 공지 인덱스 재생성 시 Chroma 전량 재임베딩을 피하고 증분 dense 유지를 사용한다.
# (변경/신규 공지는 _upsert_notice_chunks가 이미 Chroma에 증분 upsert/삭제하므로,
#  refresh 단계에서는 parquet/TF-IDF만 전체 재생성하면 된다. Chroma 카운트가 어긋나면
#  안전하게 1회 전량 재임베딩으로 자가복구한다.)
RAG_NOTICES_INCREMENTAL_EMBED = os.getenv("RAG_NOTICES_INCREMENTAL_EMBED", "1") == "1"

# 대화 기록 관련 설정 (인메모리).
MAX_HISTORY_STORE_SIZE = int(os.getenv("MAX_HISTORY_STORE_SIZE", "1000"))

# Redis 대화 기록 백엔드 설정.
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380/0")
# 대화 이력 키의 만료 시간(초). 게스트 세션 등이 무한히 쌓여 메모리가 증가하는 것을 막는다.
# 기본 7일. 0 이하로 설정하면 만료 없이 영구 보관한다.
_redis_history_ttl_raw = int(os.getenv("REDIS_HISTORY_TTL_SECONDS", str(7 * 24 * 60 * 60)))
REDIS_HISTORY_TTL_SECONDS = _redis_history_ttl_raw if _redis_history_ttl_raw > 0 else None

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
    "meals": DATA_DIR / "dongguk_meals.csv",
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
    "EMBED_QUERY_PREFIX",
    "EMBED_PASSAGE_PREFIX",
    "MODEL_TRUST_REMOTE_CODE",
    "EMBED_MODEL_REVISION",
    "RERANKER_ENABLED",
    "RERANKER_MODEL",
    "RERANKER_CANDIDATES",
    "RERANKER_MODEL_REVISION",
    "RERANKER_MODE",
    "RERANKER_MAX_TOP_GAP",
    "RERANKER_MIN_TEXT_CHARS",
    "PARENT_CONTEXT_ENABLED",
    "CHUNK_SIZE",
    "STRUCTURED_CHUNK_SIZE",
    "CHUNK_OVERLAP",
    "HYBRID_ALPHA",
    "HYBRID_FUSION_MODE",
    "HYBRID_RRF_K",
    "DEFAULT_TOP_K",
    "MIN_RETRIEVAL_SCORE",
    "RRF_UNALIGNED_MIN_COMPONENT_SCORE",
    "RECENCY_WEIGHT",
    "RECENCY_DECAY_DAYS_BY_DATASET",
    "ACTIVE_NOTICE_UNKNOWN_MAX_AGE_DAYS",
    "TFIDF_VERIFY_INTEGRITY",
    "TFIDF_REQUIRE_MANIFEST",
    "TFIDF_TOKENIZER",
    "MAX_CONTEXT_LENGTH",
    "LLM_ROUTER_DESCRIPTIONS",
    "OPENAI_MODEL",
    "OPENAI_API_KEY",
    "RAG_REQUIRE_OPENAI_API_KEY",
    "RAG_NOTICE_IMAGE_OCR_ENABLED",
    "RAG_NOTICE_IMAGE_OCR_MODEL",
    "RAG_NOTICE_IMAGE_OCR_MAX_IMAGES",
    "RAG_NOTICE_IMAGE_OCR_MAX_BYTES",
    "RAG_NOTICE_IMAGE_OCR_MIN_TEXT_CHARS",
    "RAG_ROUTER_CACHE_TTL_SECONDS",
    "RAG_SEMANTIC_CACHE_ENABLED",
    "RAG_SEMANTIC_CACHE_THRESHOLD",
    "RAG_SEMANTIC_CACHE_TTL_SECONDS",
    "RAG_SEMANTIC_CACHE_MAX",
    "LLM_PROVIDER",
    "LLM_FALLBACK_ENABLED",
    "OPENAI_CHAT_MODEL",
    "OPENAI_CHAT_TEMPERATURE",
    "OPENAI_CHAT_TIMEOUT_SECONDS",
    "OPENAI_CHAT_MAX_RETRIES",
    "OPENAI_CHAT_INPUT_COST_PER_1M",
    "OPENAI_CHAT_OUTPUT_COST_PER_1M",
    "OLLAMA_BASE_URL",
    "OLLAMA_CHAT_MODEL",
    "OLLAMA_CHAT_TEMPERATURE",
    "OLLAMA_TIMEOUT_SECONDS",
    "USE_QUERY_ANALYSIS",
    "QUERY_ANALYSIS_MAX_QUERIES",
    "RAG_DECOMPOSE_ENABLED",
    "RAG_MAX_SUBQUERIES",
    "RAG_SUGGEST_FOLLOWUPS",
    "RAG_SUGGEST_FOLLOWUPS_COUNT",
    "RAG_GROUNDING_CHECK_ENABLED",
    "RAG_COLLEGE_SCOPE_ENABLED",
    "RAG_GROUNDING_MIN_SCORE",
    "RAG_GROUNDING_FAILURE_POLICY",
    "RAG_STREAM_BUFFER_UNTIL_GROUNDED",
    "RAG_SEARCH_ALL_DATASETS",
    "RAG_SINGLE_QUERY_RETRIEVAL",
    "RAG_RETRIEVAL_TOP_K_PER_DATASET",
    "RAG_ALLOW_AS_OF_OVERRIDE",
    "RAG_EVIDENCE_CANDIDATES_PER_DATASET",
    "RAG_EVIDENCE_MAX_CANDIDATES",
    "RAG_EVIDENCE_TEXT_CHARS",
    "RAG_EVIDENCE_TIMEOUT_SECONDS",
    "RAG_SCHEDULER_ENABLED",
    "RAG_NOTICES_REFRESH_HOURS",
    "RAG_MEALS_REFRESH_HOURS",
    "RAG_NOTICES_REFRESH_MAX_PAGES",
    "RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS",
    "RAG_SCHEDULER_REQUEST_RETRIES",
    "RAG_NOTICES_INCREMENTAL_EMBED",
    "MAX_HISTORY_STORE_SIZE",
    "REDIS_URL",
    "REDIS_HISTORY_TTL_SECONDS",
    "DATA_SOURCES",
]
