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

# 데이터베이스 파일 경로 설정 (RAG 폴더 최상위에 'rag_database.db' 파일로 저장됨)
DATABASE_FILE = Path(__file__).resolve().parents[1] / "rag_database.db"
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
    
    chunks = relationship("Chunk", back_populates="notice")


# 2. 학칙 (Rules)
class Rule(Base):
    __tablename__ = "rules"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, index=True)
    relative_dir = Column(String)
    full_text = Column(Text)
    
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
    error_summary = Column(Text, nullable=True)


class DocumentQualityCheck(Base):
    __tablename__ = "document_quality_checks"

    id = Column(Integer, primary_key=True, index=True)
    document_key = Column(String, index=True, nullable=False)
    check_type = Column(String, index=True, nullable=False)
    severity = Column(String, index=True, nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=kst_now, index=True)


# 9. 통합 청크 (Chunks)
class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(Integer, primary_key=True, index=True)
    chunk_id = Column(String, unique=True, index=True) # ChromaDB ID
    chunk_text = Column(Text)
    
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


# 10. RAG 질문/답변 평가 로그
class RagQueryLog(Base):
    __tablename__ = "rag_query_logs"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(String, index=True)
    session_id = Column(String, index=True)
    question = Column(Text)
    expanded_question = Column(Text)
    route = Column(Text)
    answer = Column(Text)
    fallback_triggered = Column(Boolean, default=False)
    fallback_reason = Column(String, nullable=True)
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
    created_at = Column(DateTime, default=kst_now, index=True)

    retrievals = relationship("RagRetrievalLog", back_populates="query_log")


# 11. RAG 검색 문서/점수 평가 로그
class RagRetrievalLog(Base):
    __tablename__ = "rag_retrieval_logs"

    id = Column(Integer, primary_key=True, index=True)
    query_log_id = Column(Integer, ForeignKey("rag_query_logs.id"), nullable=False, index=True)
    rank = Column(Integer)
    dataset = Column(String, index=True)
    chunk_id = Column(String, index=True)
    title = Column(Text)
    url = Column(Text)
    published_at = Column(String)
    vector_score = Column(Float, nullable=True)
    sparse_score = Column(Float, nullable=True)
    hybrid_score = Column(Float, nullable=True)
    recency_score = Column(Float, nullable=True)
    final_score = Column(Float, nullable=True)
    sort_date = Column(String, nullable=True)
    snippet = Column(Text)
    created_at = Column(DateTime, default=kst_now, index=True)

    query_log = relationship("RagQueryLog", back_populates="retrievals")


# 12. RAG 답변 사용자 피드백
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
        "rag_query_logs",
        {
            "fallback_reason": "VARCHAR",
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
        },
    )
    _ensure_sqlite_columns(
        "rag_retrieval_logs",
        {
            "sort_date": "VARCHAR",
        },
    )


def init_db():
    """데이터베이스와 테이블을 생성합니다."""
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema()

def reset_db():
    """DB를 초기화합니다 (모든 테이블 삭제 후 재생성)."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
