from __future__ import annotations

import os
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey, DateTime

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


# 8. 통합 청크 (Chunks)
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


def init_db():
    """데이터베이스와 테이블을 생성합니다."""
    Base.metadata.create_all(bind=engine)

def reset_db():
    """DB를 초기화합니다 (모든 테이블 삭제 후 재생성)."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
