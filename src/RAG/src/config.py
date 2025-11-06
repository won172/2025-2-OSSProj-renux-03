"""Project-wide configuration and constants."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

# Resolve important filesystem locations.
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ARTIFACT_DIR = BASE_DIR / "artifacts"
CHROMA_DIR = ARTIFACT_DIR / "db_chroma"
MODEL_DIR = ARTIFACT_DIR / "models"
VECTORIZER_DIR = ARTIFACT_DIR / "vectorizers"
CHUNKS_DIR = ARTIFACT_DIR / "chunks"

# Ensure directories exist when the module is imported.
for _path in (DATA_DIR, ARTIFACT_DIR, CHROMA_DIR, MODEL_DIR, VECTORIZER_DIR, CHUNKS_DIR):
    _path.mkdir(parents=True, exist_ok=True)

# Embedding configuration.
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL", "nlpai-lab/KURE-v1")
EMBED_DEVICE = os.getenv("EMBED_DEVICE", "cpu")
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "8"))

# Chunking and retrieval defaults.
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "120"))
HYBRID_ALPHA = float(os.getenv("HYBRID_ALPHA", "0.4"))
DEFAULT_TOP_K = int(os.getenv("DEFAULT_TOP_K", "5"))

# OpenAI / LLM defaults.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Input data sources derived from the notebook.
DATA_SOURCES: Dict[str, Path] = {
    "notices": DATA_DIR / "dongguk_notices.csv",
    "rules": DATA_DIR / "dongguk_rule_texts.csv",
    "schedule": DATA_DIR / "dongguk_schedule.csv",
    "courses_desc": DATA_DIR / "dongguk_statistics_course_descriptions.csv",
    "courses_major": DATA_DIR / "dongguk_statistics_major_course.csv",
}

# Simple keyword router rules preserved from the notebook experiments.
ROUTER_RULES = {
    "notices": ["공지", "알림", "모집", "합격", "발표", "마감", "장학", "등록금", "휴학", "복학", "입시", "공지사항"],
    "rules": ["학칙", "규정", "규정집", "조항", "조", "항", "시행세칙", "학사 규정"],
    "schedule": ["학사일정", "일정", "수강신청", "개강", "종강", "성적", "등록", "휴학기간", "방학"],
    "courses": ["과목", "강좌", "교과목", "수업", "전공", "선수과목", "학점", "이수구분", "통계학과"],
}

__all__ = [
    "BASE_DIR",
    "DATA_DIR",
    "ARTIFACT_DIR",
    "CHROMA_DIR",
    "MODEL_DIR",
    "VECTORIZER_DIR",
    "CHUNKS_DIR",
    "EMBED_MODEL_NAME",
    "EMBED_DEVICE",
    "EMBED_BATCH_SIZE",
    "CHUNK_SIZE",
    "CHUNK_OVERLAP",
    "HYBRID_ALPHA",
    "DEFAULT_TOP_K",
    "OPENAI_MODEL",
    "OPENAI_API_KEY",
    "DATA_SOURCES",
    "ROUTER_RULES",
]
