# settings.py
from dotenv import load_dotenv
import os
from pathlib import Path

load_dotenv()

# ===== 경로 =====
ROOT = Path(__file__).parent
DB_DIR = ROOT / "db_chroma"      # Chroma 저장소
DB_DIR.mkdir(exist_ok=True)

# ===== 데이터 소스 경로 =====
DATA = {
    "notices": "../data/dongguk_notices.csv",
    "rules": "../data/dongguk_rule_texts.csv",
    "schedule": "../data/dongguk_schedule.csv",
    "courses_desc": "../data/dongguk_statistics_course_descriptions.csv",
    "courses_major": "../data/dongguk_statistics_major_course.csv",
}

# ===== 임베딩/토큰 설정 =====
EMBED_MODEL = "nlpai-lab/KURE-v1"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 120


# ===== 라우터 키워드 규칙(간단 버전) =====
ROUTER_RULES = {
    "notices": ["공지", "알림", "모집", "합격", "발표", "마감", "장학", "등록금", "휴학", "복학", "입시", "공지사항"],
    "rules": ["학칙", "규정", "규정집", "조항", "조", "항", "시행세칙", "학사 규정"],
    "schedule": ["학사일정", "일정", "수강신청", "개강", "종강", "성적", "등록", "휴학기간", "방학"],
    "courses": ["과목", "강좌", "교과목", "수업", "전공", "선수과목", "학점", "이수구분", "통계학과"],
}
