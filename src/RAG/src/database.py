from __future__ import annotations

import os
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey, DateTime, Float, Boolean, UniqueConstraint

def kst_now():
    return datetime.now(timezone(timedelta(hours=9)))

from sqlalchemy.orm import sessionmaker, declarative_base, relationship

# 데이터베이스 파일 경로 (기본: RAG 폴더 최상위의 'rag_database.db').
#
# RAG_DATABASE_FILE로 바꿀 수 있다. 경로가 고정돼 있으면 개발자는 항상 자기 로컬 DB로만
# 테스트하게 되고, 새 체크아웃처럼 테이블이 없는 상태를 재현할 방법이 없다. 실제로
# 스케줄러 실행 기록을 추가했을 때 로컬 596건이 전부 통과하고 CI에서만 깨졌다 —
# 로컬에는 ingestion_runs 테이블이 있었기 때문이다.
DATABASE_FILE = Path(
    os.getenv("RAG_DATABASE_FILE")
    or Path(__file__).resolve().parents[1] / "rag_database.db"
)
DATABASE_URL = f"sqlite:///{DATABASE_FILE}"

# SQLAlchemy 엔진 생성
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# SQLAlchemy 세션 설정
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 모든 모델 클래스가 상속받을 기본 클래스
Base = declarative_base()


# 1. 공지사항 (Notices)
class Notice(Base):
    __tablename__ = "notices"

    id = Column(Integer, primary_key=True, index=True)
    board = Column(String, index=True)
    title = Column(String)
    category = Column(String, index=True)
    published_date = Column(String) 
    is_fixed = Column(String)
    detail_url = Column(String, unique=True, index=True)
    content = Column(Text)
    attachments = Column(Text)
    is_manual = Column(Integer, default=0) # 0: auto, 1: manual
    # 학과 콘솔에서 제출한 항목의 대상 학과와 공개 범위. 기존 수집 공지는
    # ``public``으로 두고, 학과 전용 항목은 검색·홈 브리핑 모두에서 이 값을
    # 기준으로 제외한다.
    department = Column(String, index=True, nullable=True)
    visibility = Column(String, index=True, nullable=False, default="public")
    
    chunks = relationship("Chunk", back_populates="notice")


# 2. 학칙 (Rules)
class Rule(Base):
    __tablename__ = "rules"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, index=True)
    relative_dir = Column(String)
    full_text = Column(Text)
    title = Column(String, nullable=True)
    source_type = Column(String, nullable=True)
    source_url = Column(Text, nullable=True)
    source_page_url = Column(Text, nullable=True)
    source_version = Column(String, nullable=True, index=True)
    published_at = Column(String, nullable=True, index=True)
    
    chunks = relationship("Chunk", back_populates="rule")


# 3. 학사일정 (Schedule)
class Schedule(Base):
    __tablename__ = "schedule"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    start_date = Column(String)
    end_date = Column(String)
    category = Column(String)
    department = Column(String)
    content = Column(Text)
    is_manual = Column(Integer, default=0) # 0: auto, 1: manual
    
    chunks = relationship("Chunk", back_populates="schedule")


# 4. 교과과정 (Courses)
class Course(Base):
    __tablename__ = "courses"

    id = Column(Integer, primary_key=True, index=True)
    course_code = Column(String, index=True)
    title = Column(String, index=True)
    description = Column(Text)
    source_table = Column(String)
    raw_data = Column(Text)
    
    chunks = relationship("Chunk", back_populates="course")


# 5. 교직원 (Staff) - 새로 추가
class Staff(Base):
    __tablename__ = "staff"

    id = Column(Integer, primary_key=True, index=True)
    department = Column(String, index=True) # 소속 (트리상 부서)
    name = Column(String, index=True)
    position = Column(String)
    role = Column(String) # 담당업무
    phone = Column(String)
    email = Column(String)
    raw_data = Column(Text) # 전체 데이터 JSON

    chunks = relationship("Chunk", back_populates="staff")


# 6. 사용자 정의 지식 (CustomKnowledge)
class CustomKnowledge(Base):
    __tablename__ = "custom_knowledge"

    id = Column(Integer, primary_key=True, index=True)
    question = Column(Text, index=True)
    answer = Column(Text)
    category = Column(String)
    created_at = Column(DateTime, default=kst_now)

    chunks = relationship("Chunk", back_populates="custom_knowledge")


# 7. 승인 대기 항목 (PendingItems)
class PendingItem(Base):
    __tablename__ = "pending_items"

    id = Column(Integer, primary_key=True, index=True)
    source_type = Column(String)  # 'custom_knowledge', 'notice', etc.
    data = Column(Text)  # JSON payload
    status = Column(String, default="pending")  # pending, approved, rejected
    created_at = Column(DateTime, default=kst_now)
    # 검수 처리 기록 — 반려 사유를 제출자에게 돌려주고, 누가 언제 처리했는지 남긴다.
    review_note = Column(Text, nullable=True)
    reviewed_by = Column(String, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    # 승인 후에도 챗봇 노출을 내릴 수 있도록 하는 플래그(내용은 보존).
    disabled = Column(Boolean, default=False, nullable=False)


# 8. 수집 원본/정규화 메타데이터
class SourceDocument(Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint("dataset", "source_id", name="uq_source_documents_dataset_source_id"),
        UniqueConstraint("document_key", name="uq_source_documents_document_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    dataset = Column(String, index=True, nullable=False)
    source_type = Column(String, nullable=False)
    source_id = Column(String, index=True, nullable=False)
    source_url = Column(Text)
    document_key = Column(String, index=True, nullable=False)
    title = Column(String)
    category = Column(String, index=True)
    published_at = Column(String, index=True)
    status = Column(String, default="active", index=True)
    content_hash = Column(String, index=True)
    schema_version = Column(Integer, default=1)
    # Canonical captured and normalized representations.  These deliberately
    # live with the document record instead of in sidecar JSON files so one DB
    # transaction owns identity, status, payload, and indexing state.
    raw_payload_json = Column(Text, nullable=True)
    normalized_payload_json = Column(Text, nullable=True)
    # Legacy export locations are kept only to read older databases during the
    # one-time migration.  New collection code must not depend on them.
    raw_path = Column(Text)
    normalized_path = Column(Text)
    collected_at = Column(DateTime, default=kst_now, index=True)
    last_parsed_at = Column(DateTime, nullable=True)
    last_indexed_at = Column(DateTime, nullable=True)
    parse_error = Column(Text, nullable=True)
    miss_count = Column(Integer, default=0)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id = Column(Integer, primary_key=True, index=True)
    dataset = Column(String, index=True, nullable=False)
    started_at = Column(DateTime, default=kst_now, index=True)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String, default="running", index=True)
    documents_seen = Column(Integer, default=0)
    documents_new = Column(Integer, default=0)
    documents_updated = Column(Integer, default=0)
    documents_deleted = Column(Integer, default=0)
    documents_failed = Column(Integer, default=0)
    # Machine-readable outcome. ``status`` remains the broad lifecycle state;
    # this field distinguishes empty upstream data, schema drift, partial fetch,
    # and indexing failures without parsing a Korean error message.
    outcome_code = Column(String, nullable=True, index=True)
    diagnostics_json = Column(Text, nullable=True)
    corpus_revision = Column(String, nullable=True, index=True)
    error_summary = Column(Text, nullable=True)


class SourceSchemaFingerprint(Base):
    """Versioned structural signature observed at an upstream boundary."""

    __tablename__ = "source_schema_fingerprints"
    __table_args__ = (
        UniqueConstraint(
            "dataset",
            "source_name",
            "fingerprint",
            name="uq_source_schema_dataset_name_fingerprint",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    dataset = Column(String, nullable=False, index=True)
    source_name = Column(String, nullable=False, index=True)
    source_format = Column(String, nullable=False)
    fingerprint = Column(String, nullable=False, index=True)
    structure_json = Column(Text, nullable=False)
    first_seen_at = Column(DateTime, default=kst_now, nullable=False, index=True)
    last_seen_at = Column(DateTime, default=kst_now, nullable=False, index=True)
    observation_count = Column(Integer, nullable=False, default=1)
    is_current = Column(Boolean, nullable=False, default=True, index=True)
    last_ingestion_run_id = Column(Integer, nullable=True, index=True)


class DocumentQualityCheck(Base):
    __tablename__ = "document_quality_checks"

    id = Column(Integer, primary_key=True, index=True)
    document_key = Column(String, index=True, nullable=False)
    check_type = Column(String, index=True, nullable=False)
    severity = Column(String, index=True, nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=kst_now, index=True)


# 9. 온톨로지/지식 그래프 파생 투영
class OntologyEntity(Base):
    """정본 문서에서 결정적으로 투영한 대학 도메인 엔터티.

    이 테이블은 정본이 아니다. ``SourceDocument`` 또는 검수된 별칭 목록에서
    언제든 다시 만들 수 있는 검색용 투영이며, 실제 주장 근거는
    ``OntologyEvidence``가 보존한다.
    """

    __tablename__ = "ontology_entities"
    __table_args__ = (
        UniqueConstraint("entity_key", name="uq_ontology_entities_entity_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    entity_key = Column(String, nullable=False, index=True)
    entity_type = Column(String, nullable=False, index=True)
    canonical_name = Column(String, nullable=False, index=True)
    properties_json = Column(Text, nullable=False, default="{}")
    extraction_method = Column(String, nullable=False, default="deterministic", index=True)
    status = Column(String, nullable=False, default="active", index=True)
    schema_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=kst_now, index=True)
    updated_at = Column(DateTime, default=kst_now, onupdate=kst_now, index=True)


class OntologyAlias(Base):
    """학생 표현이나 과거 학과명을 canonical entity에 연결한다."""

    __tablename__ = "ontology_aliases"
    __table_args__ = (
        UniqueConstraint(
            "alias_key",
            "entity_key",
            "source_dataset",
            name="uq_ontology_aliases_alias_entity_source",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    alias_key = Column(String, nullable=False, index=True)
    alias = Column(String, nullable=False)
    entity_key = Column(String, nullable=False, index=True)
    source_dataset = Column(String, nullable=False, index=True)
    source_document_key = Column(String, nullable=True, index=True)
    status = Column(String, nullable=False, default="active", index=True)
    created_at = Column(DateTime, default=kst_now, index=True)


class OntologyRelation(Base):
    """엔터티 사이의 의미 관계. 원문 근거는 별도 evidence 행에 둔다."""

    __tablename__ = "ontology_relations"
    __table_args__ = (
        UniqueConstraint("relation_key", name="uq_ontology_relations_relation_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    relation_key = Column(String, nullable=False, index=True)
    subject_key = Column(String, nullable=False, index=True)
    predicate = Column(String, nullable=False, index=True)
    object_key = Column(String, nullable=False, index=True)
    qualifiers_json = Column(Text, nullable=False, default="{}")
    confidence = Column(Float, nullable=False, default=1.0)
    extraction_method = Column(String, nullable=False, default="deterministic", index=True)
    review_status = Column(String, nullable=False, default="approved", index=True)
    status = Column(String, nullable=False, default="active", index=True)
    schema_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=kst_now, index=True)
    updated_at = Column(DateTime, default=kst_now, onupdate=kst_now, index=True)

    evidence = relationship(
        "OntologyEvidence",
        back_populates="relation",
        cascade="all, delete-orphan",
    )


class OntologyEvidence(Base):
    """관계 주장을 정본 문서와 retrieval-affecting 필드에 연결한다."""

    __tablename__ = "ontology_evidence"
    __table_args__ = (
        UniqueConstraint(
            "relation_id",
            "document_key",
            "evidence_locator",
            name="uq_ontology_evidence_relation_document_locator",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    relation_id = Column(
        Integer,
        ForeignKey("ontology_relations.id"),
        nullable=False,
        index=True,
    )
    source_dataset = Column(String, nullable=False, index=True)
    document_key = Column(String, nullable=False, index=True)
    extraction_method = Column(String, nullable=False, default="deterministic", index=True)
    evidence_locator = Column(String, nullable=False)
    evidence_text = Column(Text, nullable=False)
    source_url = Column(Text, nullable=True)
    published_at = Column(String, nullable=True, index=True)
    observed_at = Column(DateTime, default=kst_now, index=True)

    relation = relationship("OntologyRelation", back_populates="evidence")


class OntologyBuildRun(Base):
    """온톨로지 파생 투영 실행과 품질 집계를 기록한다."""

    __tablename__ = "ontology_build_runs"

    id = Column(Integer, primary_key=True, index=True)
    schema_version = Column(Integer, nullable=False, default=1)
    datasets_json = Column(Text, nullable=False, default="[]")
    started_at = Column(DateTime, default=kst_now, index=True)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="running", index=True)
    documents_seen = Column(Integer, nullable=False, default=0)
    entities_emitted = Column(Integer, nullable=False, default=0)
    aliases_emitted = Column(Integer, nullable=False, default=0)
    relations_emitted = Column(Integer, nullable=False, default=0)
    evidence_emitted = Column(Integer, nullable=False, default=0)
    validation_errors_json = Column(Text, nullable=False, default="[]")
    build_revision = Column(String, nullable=True, index=True)
    corpus_revisions_json = Column(Text, nullable=False, default="{}")
    trigger_dataset = Column(String, nullable=True, index=True)
    trigger_ingestion_run_id = Column(Integer, nullable=True, index=True)
    error_summary = Column(Text, nullable=True)


class OntologyShadowLog(Base):
    """관계 검색을 실제 검색 결과에 섞기 전 관찰한 shadow 실행 기록.

    질문 원문은 기존 ``RagQueryLog``에만 남기고 여기에는 해시와 제한된 그래프
    식별자만 저장한다. 따라서 shadow 경로는 답변·검색 순위와 독립적으로 성능과
    도달 범위를 비교할 수 있다.
    """

    __tablename__ = "ontology_shadow_logs"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=True, index=True)
    query_hash = Column(String, nullable=False, index=True)
    route_json = Column(Text, nullable=False, default="[]")
    linked_entities_json = Column(Text, nullable=False, default="[]")
    traversed_relations_json = Column(Text, nullable=False, default="[]")
    document_keys_json = Column(Text, nullable=False, default="[]")
    retrieved_document_keys_json = Column(Text, nullable=False, default="[]")
    overlap_document_keys_json = Column(Text, nullable=False, default="[]")
    linked_entity_count = Column(Integer, nullable=False, default=0)
    relation_count = Column(Integer, nullable=False, default=0)
    document_count = Column(Integer, nullable=False, default=0)
    retrieved_document_count = Column(Integer, nullable=False, default=0)
    overlap_document_count = Column(Integer, nullable=False, default=0)
    max_hops = Column(Integer, nullable=False, default=0)
    max_entities = Column(Integer, nullable=False, default=0)
    max_relations = Column(Integer, nullable=False, default=0)
    max_documents = Column(Integer, nullable=False, default=0)
    relation_limit_reached = Column(Boolean, nullable=False, default=False)
    document_limit_reached = Column(Boolean, nullable=False, default=False)
    latency_ms = Column(Float, nullable=False, default=0.0)
    status = Column(String, nullable=False, default="success", index=True)
    error_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=kst_now, index=True)


# 10. 통합 청크 (Chunks)
class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(Integer, primary_key=True, index=True)
    chunk_id = Column(String, unique=True, index=True) # ChromaDB ID
    chunk_text = Column(Text)
    # ``chunk_id`` identifies the vector, while this pair preserves the
    # document hierarchy required to reconstruct adjacent context after a
    # SQLite-only reindex.  Without it, a rebuilt parquet index can no longer
    # tell which chunks came from the same original document.
    doc_id = Column(String, index=True, nullable=True)
    position = Column(Integer, nullable=True)
    
    # Foreign Keys (Nullable)
    notice_id = Column(Integer, ForeignKey("notices.id"), nullable=True)
    rule_id = Column(Integer, ForeignKey("rules.id"), nullable=True)
    schedule_id = Column(Integer, ForeignKey("schedule.id"), nullable=True)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=True)
    staff_id = Column(Integer, ForeignKey("staff.id"), nullable=True) # 새로 추가
    custom_knowledge_id = Column(Integer, ForeignKey("custom_knowledge.id"), nullable=True)

    # Relationships
    notice = relationship("Notice", back_populates="chunks")
    rule = relationship("Rule", back_populates="chunks")
    schedule = relationship("Schedule", back_populates="chunks")
    course = relationship("Course", back_populates="chunks")
    staff = relationship("Staff", back_populates="chunks") # 새로 추가
    custom_knowledge = relationship("CustomKnowledge", back_populates="chunks")


# 11. RAG 질문/답변 평가 로그
class RagQueryLog(Base):
    __tablename__ = "rag_query_logs"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(String, index=True)
    session_id = Column(String, index=True)
    question = Column(Text)
    expanded_question = Column(Text)
    as_of = Column(String, nullable=True, index=True)
    route = Column(Text)
    answer = Column(Text)
    fallback_triggered = Column(Boolean, default=False)
    fallback_reason = Column(String, nullable=True)
    grounding_checked = Column(Boolean, default=False)
    grounding_grounded = Column(Boolean, nullable=True)
    grounding_score = Column(Float, nullable=True)
    date_filter_applied = Column(Boolean, default=False)
    date_filter_relaxed = Column(Boolean, default=False)
    analysis_intent = Column(String, nullable=True)
    analysis_entities_json = Column(Text, nullable=True)
    analysis_time_focus = Column(String, nullable=True)
    analysis_search_queries_json = Column(Text, nullable=True)
    analysis_needs_clarification = Column(Boolean, default=False)
    analysis_clarification_reason = Column(Text, nullable=True)
    analysis_used = Column(Boolean, default=False)
    analysis_failed = Column(Boolean, default=False)
    matched_queries_json = Column(Text, nullable=True)
    top_hybrid_score = Column(Float, nullable=True)
    source_count = Column(Integer, default=0)
    stage_timings_json = Column(Text, nullable=True)
    llm_usage_json = Column(Text, nullable=True)
    estimated_llm_cost_usd = Column(Float, nullable=True)
    created_at = Column(DateTime, default=kst_now, index=True)

    retrievals = relationship("RagRetrievalLog", back_populates="query_log")


# 12. RAG 검색 문서/점수 평가 로그
class RagRetrievalLog(Base):
    __tablename__ = "rag_retrieval_logs"

    id = Column(Integer, primary_key=True, index=True)
    query_log_id = Column(Integer, ForeignKey("rag_query_logs.id"), nullable=False, index=True)
    rank = Column(Integer)
    dataset = Column(String, index=True)
    chunk_id = Column(String, index=True)
    document_key = Column(String, nullable=True, index=True)
    title = Column(Text)
    url = Column(Text)
    published_at = Column(String)
    vector_score = Column(Float, nullable=True)
    sparse_score = Column(Float, nullable=True)
    hybrid_score = Column(Float, nullable=True)
    recency_score = Column(Float, nullable=True)
    final_score = Column(Float, nullable=True)
    sort_date = Column(String, nullable=True)
    # Async follow-up generation must transport the exact source identity that
    # the completed answer exposed. Recomputing it from this reduced log row
    # loses campus/effective-date metadata and produces a different lineage.
    source_ref = Column(String, nullable=True, index=True)
    snippet = Column(Text)
    created_at = Column(DateTime, default=kst_now, index=True)

    query_log = relationship("RagQueryLog", back_populates="retrievals")


# 13. RAG 답변 사용자 피드백
class RagFeedback(Base):
    __tablename__ = "rag_feedback"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(String, index=True, nullable=False)
    session_id = Column(String, index=True, nullable=True)
    rating = Column(Integer, nullable=False)
    reason = Column(String, nullable=True)
    comment = Column(Text, nullable=True)
    major = Column(String, nullable=True)
    created_at = Column(DateTime, default=kst_now, index=True)


def _ensure_sqlite_columns(table_name: str, columns: dict[str, str]) -> None:
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
        }
        for column_name, column_type in columns.items():
            if column_name in existing:
                continue
            connection.exec_driver_sql(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
            )


def ensure_runtime_schema() -> None:
    """기존 SQLite 파일에 누락된 운영 로그 컬럼을 보강합니다."""
    _ensure_sqlite_columns(
        "rules",
        {
            "title": "VARCHAR",
            "source_type": "VARCHAR",
            "source_url": "TEXT",
            "source_page_url": "TEXT",
            "source_version": "VARCHAR",
            "published_at": "VARCHAR",
        },
    )
    _ensure_sqlite_columns(
        "notices",
        {
            "department": "VARCHAR",
            "visibility": "VARCHAR DEFAULT 'public'",
        },
    )
    _ensure_sqlite_columns(
        "source_documents",
        {
            "raw_payload_json": "TEXT",
            "normalized_payload_json": "TEXT",
        },
    )
    _ensure_sqlite_columns(
        "chunks",
        {
            "doc_id": "VARCHAR",
            "position": "INTEGER",
        },
    )
    _ensure_sqlite_columns(
        "rag_query_logs",
        {
            "as_of": "VARCHAR",
            "fallback_reason": "VARCHAR",
            "grounding_checked": "BOOLEAN DEFAULT 0",
            "grounding_grounded": "BOOLEAN",
            "grounding_score": "FLOAT",
            "date_filter_applied": "BOOLEAN DEFAULT 0",
            "date_filter_relaxed": "BOOLEAN DEFAULT 0",
            "analysis_intent": "VARCHAR",
            "analysis_entities_json": "TEXT",
            "analysis_time_focus": "VARCHAR",
            "analysis_search_queries_json": "TEXT",
            "analysis_needs_clarification": "BOOLEAN DEFAULT 0",
            "analysis_clarification_reason": "TEXT",
            "analysis_used": "BOOLEAN DEFAULT 0",
            "analysis_failed": "BOOLEAN DEFAULT 0",
            "matched_queries_json": "TEXT",
            "top_hybrid_score": "FLOAT",
            "source_count": "INTEGER DEFAULT 0",
            "stage_timings_json": "TEXT",
            "llm_usage_json": "TEXT",
            "estimated_llm_cost_usd": "FLOAT",
        },
    )
    _ensure_sqlite_columns(
        "rag_retrieval_logs",
        {
            "sort_date": "VARCHAR",
            "source_ref": "VARCHAR",
            "document_key": "VARCHAR",
        },
    )
    _ensure_sqlite_columns(
        "rag_feedback",
        {
            "major": "VARCHAR",
        },
    )
    _ensure_sqlite_columns(
        "pending_items",
        {
            "review_note": "TEXT",
            "reviewed_by": "VARCHAR",
            "reviewed_at": "DATETIME",
            "disabled": "BOOLEAN DEFAULT 0",
        },
    )
    _ensure_sqlite_columns(
        "ingestion_runs",
        {
            "outcome_code": "VARCHAR",
            "diagnostics_json": "TEXT",
            "corpus_revision": "VARCHAR",
        },
    )
    _ensure_sqlite_columns(
        "ontology_build_runs",
        {
            "build_revision": "VARCHAR",
            "corpus_revisions_json": "TEXT DEFAULT '{}'",
            "trigger_dataset": "VARCHAR",
            "trigger_ingestion_run_id": "INTEGER",
        },
    )
    _ensure_sqlite_columns(
        "ontology_shadow_logs",
        {
            "retrieved_document_keys_json": "TEXT DEFAULT '[]'",
            "overlap_document_keys_json": "TEXT DEFAULT '[]'",
            "retrieved_document_count": "INTEGER DEFAULT 0",
            "overlap_document_count": "INTEGER DEFAULT 0",
            "max_entities": "INTEGER DEFAULT 0",
            "max_relations": "INTEGER DEFAULT 0",
            "max_documents": "INTEGER DEFAULT 0",
            "relation_limit_reached": "BOOLEAN DEFAULT 0",
            "document_limit_reached": "BOOLEAN DEFAULT 0",
        },
    )


def init_db():
    """데이터베이스와 테이블을 생성합니다."""
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema()


def verify_database_writable() -> None:
    """Acquire and release a SQLite write lock without changing user data."""
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        connection.exec_driver_sql("ROLLBACK")

def reset_db():
    """DB를 초기화합니다 (모든 테이블 삭제 후 재생성)."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
