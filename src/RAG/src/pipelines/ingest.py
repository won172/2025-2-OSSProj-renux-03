"""여러 데이터셋에 대한 Chroma 인덱스 및 SQLite DB를 구축하는 데이터 수집 루틴입니다."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
import json

import pandas as pd
import re
from sqlalchemy.orm import Session

from src.config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    CHUNKS_DIR,
    DATA_SOURCES,
)
from src.models.embedding import encode_texts
from src.search.hybrid import train_tfidf
from src.utils.preprocess import (
    apply_cleaning,
    normalize_whitespace,
    make_doc_id,
    to_chunks,
)
from src.vectorstore.chroma_client import add_items, reset_collection
from src.database import (
    SessionLocal, engine, init_db,
    Notice, Rule, Schedule, Course, Chunk
)


@dataclass
class DatasetArtifacts:
    key: str
    collection: str
    chunk_path: Path

    @property
    def csv_path(self) -> Path:
        return self.chunk_path.with_suffix(".csv")


DATASET_ARTIFACTS: Dict[str, DatasetArtifacts] = {
    "notices": DatasetArtifacts(
        key="notices",
        collection="dongguk_notices",
        chunk_path=CHUNKS_DIR / "notices.parquet",
    ),
    "rules": DatasetArtifacts(
        key="rules",
        collection="dongguk_rules",
        chunk_path=CHUNKS_DIR / "rules.parquet",
    ),
    "schedule": DatasetArtifacts(
        key="schedule",
        collection="dongguk_schedule",
        chunk_path=CHUNKS_DIR / "schedule.parquet",
    ),
    "courses": DatasetArtifacts(
        key="courses",
        collection="dongguk_courses",
        chunk_path=CHUNKS_DIR / "courses.parquet",
    ),
}


def _persist_chunks(key: str, collection: str, chunks_df: pd.DataFrame) -> Tuple[pd.DataFrame, object, object]:
    if chunks_df.empty:
        print(f"⚠️ Warning: No chunks generated for {key}")
        return chunks_df, None, None

    embeddings = encode_texts(chunks_df["chunk_text"].tolist())

    reset_collection(collection)
    
    # 메타데이터 준비 (None 처리)
    metadatas = chunks_df.drop(columns=["chunk_text"]).to_dict(orient="records")
    metadatas = [{k: (v if v is not None else "") for k, v in m.items()} for m in metadatas]

    add_items(
        collection,
        ids=chunks_df["chunk_id"],
        documents=chunks_df["chunk_text"],
        metadatas=metadatas,
        embeddings=embeddings,
    )

    artifacts = DATASET_ARTIFACTS[key]
    artifacts.chunk_path.parent.mkdir(parents=True, exist_ok=True)

    write_path = artifacts.chunk_path
    try:
        # object 타입 문제 방지 위해 string 변환
        chunks_df.astype(str).to_parquet(write_path, index=False)
    except Exception:
        write_path = artifacts.csv_path
        chunks_df.to_csv(write_path, index=False, encoding="utf-8-sig")
    artifacts.chunk_path = write_path

    vectorizer, matrix = train_tfidf(key, chunks_df["chunk_text"].tolist())
    return chunks_df, vectorizer, matrix


def _save_chunks_to_sqlite(chunks_df: pd.DataFrame, source_key: str):
    """SQLite의 chunks 테이블에 저장합니다."""
    if chunks_df.empty:
        return
    
    # 필요한 컬럼만 선택 및 확보
    cols = ["chunk_id", "chunk_text", "notice_id", "rule_id", "schedule_id", "course_id"]
    for col in cols:
        if col not in chunks_df.columns:
            chunks_df[col] = None
            
    # 저장할 데이터프레임
    to_save = chunks_df[cols].copy()
    
    # 호출자가 이미 기존 데이터를 삭제했다고 가정
    to_save.to_sql("chunks", con=engine, if_exists="append", index=False)


def _first_nonempty(row, keys: Iterable[str]) -> str:
    for key in keys:
        val = row.get(key, "") if hasattr(row, "get") else row.get(key, "")
        val_str = str(val).strip()
        if val_str and val_str.lower() != "nan":
            return val_str
    return ""


# --- Notices ---

def build_notice_chunks(df: pd.DataFrame) -> pd.DataFrame:
    column = {
        "title": "제목",
        "content": "본문",
        "date": "게시일",
        "topic": "게시판",
        "url": "상세URL",
        "attachment": "첨부파일",
    }

    cleaned = apply_cleaning(df, content_col=column["content"], date_col=column["date"])

    docs: List[dict] = []
    for _, row in cleaned.iterrows():
        text = row.get("clean_text", "")
        if not isinstance(text, str) or not text.strip():
            continue
        
        published = row.get("clean_date")
        doc_id = make_doc_id(row.get(column["title"]), row.get(column["topic"]), published)
        
        docs.append(
            {
                "doc_id": doc_id,
                "title": row.get(column["title"], ""),
                "text": text,
                "topics": row.get(column["topic"], ""),
                "published_at": published or "",
                "url": row.get(column["url"], ""),
                "attachments": row.get(column["attachment"], ""),
                "source": "notices",
                "notice_id": row.get("db_id"), # DB ID from ingest_notices
            }
        )

    chunks = to_chunks(
        docs,
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        include_title=True,
    )
    return pd.DataFrame(chunks)


def ingest_notices() -> Tuple[pd.DataFrame, object, object]:
    path = DATA_SOURCES["notices"]
    if not path.exists():
        raise FileNotFoundError(f"Notice CSV not found: {path}")

    raw_df = pd.read_csv(path)
    
    session = SessionLocal()
    try:
        # 1. 기존 데이터 삭제
        session.query(Chunk).filter(Chunk.notice_id.isnot(None)).delete()
        session.query(Notice).delete()
        session.commit()
        
        # 2. 원본 데이터 저장
        notice_objs = []
        # 날짜 포맷 통일
        raw_df["게시일"] = pd.to_datetime(raw_df["게시일"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
        
        for _, row in raw_df.iterrows():
            obj = Notice(
                board=row.get("게시판"),
                title=row.get("제목"),
                category=row.get("카테고리"),
                published_date=row.get("게시일"),
                is_fixed=str(row.get("상단고정")),
                detail_url=row.get("상세URL"),
                content=row.get("본문"),
                attachments=str(row.get("첨부파일"))
            )
            notice_objs.append(obj)
            
        session.add_all(notice_objs)
        session.commit()
        
        # 3. ID 매핑
        raw_df["db_id"] = [obj.id for obj in notice_objs]
        
    finally:
        session.close()

    # 4. 청크 생성 및 저장
    chunks_df = build_notice_chunks(raw_df)
    _save_chunks_to_sqlite(chunks_df, "notices")
    
    return _persist_chunks("notices", DATASET_ARTIFACTS["notices"].collection, chunks_df)


# --- Rules ---

def build_rule_chunks(df: pd.DataFrame) -> pd.DataFrame:
    docs: List[dict] = []
    for _, row in df.iterrows():
        text = _first_nonempty(row, ["text", "내용", "본문", "article", "조문", "rule_text"])
        if not text:
            continue
        filename = _first_nonempty(row, ["filename", "파일명", "규정명", "title"])
        rel_dir = _first_nonempty(row, ["relative_dir", "경로", "folder"])
        doc_id = make_doc_id("rules", rel_dir, filename or text[:40])
        docs.append(
            {
                "doc_id": doc_id,
                "title": filename or text[:80] or "학칙 문서",
                "text": text,
                "topics": "규정",
                "relative_dir": rel_dir,
                "filename": filename,
                "source": "rules",
                "url": "",
                "published_at": "",
                "rule_id": row.get("db_id"),
            }
        )

    chunks = to_chunks(
        docs,
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        include_title=True,
    )
    return pd.DataFrame(chunks)


def ingest_rules() -> Tuple[pd.DataFrame, object, object]:
    path = DATA_SOURCES["rules"]
    if not path.exists():
        raise FileNotFoundError(f"Rule CSV not found: {path}")

    df = pd.read_csv(path).fillna("").astype(str)
    
    session = SessionLocal()
    try:
        session.query(Chunk).filter(Chunk.rule_id.isnot(None)).delete()
        session.query(Rule).delete()
        session.commit()

        rule_objs = []
        for _, row in df.iterrows():
            text_val = _first_nonempty(row, ["text", "내용", "본문", "article", "조문", "rule_text"])
            fname = _first_nonempty(row, ["filename", "파일명", "규정명", "title"])
            rdir = _first_nonempty(row, ["relative_dir", "경로", "folder"])
            
            obj = Rule(filename=fname, relative_dir=rdir, full_text=text_val)
            rule_objs.append(obj)
            
        session.add_all(rule_objs)
        session.commit()
        df["db_id"] = [obj.id for obj in rule_objs]
    finally:
        session.close()
        
    chunks_df = build_rule_chunks(df)
    _save_chunks_to_sqlite(chunks_df, "rules")
    return _persist_chunks("rules", DATASET_ARTIFACTS["rules"].collection, chunks_df)


# --- Schedule ---

def build_schedule_chunks(df: pd.DataFrame) -> pd.DataFrame:
    docs: List[dict] = []
    for _, row in df.iterrows():
        # ingest_schedule에서 할당한 객체 사용
        obj = row.get("db_object")
        if not obj: continue

        doc_id = make_doc_id("schedule", obj.start_date, obj.end_date, obj.content)
        docs.append(
            {
                "doc_id": doc_id,
                "title": obj.title,
                "text": obj.content,
                "schedule_start": obj.start_date,
                "schedule_end": obj.end_date,
                "category": obj.category,
                "department": obj.department,
                "topics": obj.category or "schedule",
                "source": "schedule",
                "url": "",
                "published_at": obj.start_date,
                "schedule_id": obj.id,
            }
        )

    chunks = to_chunks(
        docs,
        chunk_size=CHUNK_SIZE // 2,
        chunk_overlap=CHUNK_OVERLAP // 2,
        include_title=True,
    )
    return pd.DataFrame(chunks)


def ingest_schedule() -> Tuple[pd.DataFrame, object, object]:
    path = DATA_SOURCES["schedule"]
    if not path.exists():
        raise FileNotFoundError(f"Schedule CSV not found: {path}")

    df = pd.read_csv(path).fillna("").astype(str)
    
    session = SessionLocal()
    try:
        session.query(Chunk).filter(Chunk.schedule_id.isnot(None)).delete()
        session.query(Schedule).delete()
        session.commit()

        sch_objs = []
        dept_pattern = re.compile(r"\(주관부서:\s*(.*?)\)")
        
        parsed_objs = []
        for _, row in df.iterrows():
            start_val = _first_nonempty(row, ["start", "start_date", "시작", "시작일"])
            end_val = _first_nonempty(row, ["end", "end_date", "종료", "종료일"])
            category = _first_nonempty(row, ["구분", "category", "분류", "0", "카테고리"])
            description = _first_nonempty(row, ["내용", "일정", "event", "2", "description"])
            
            if not description:
                parsed_objs.append(None)
                continue
                
            dept_match = dept_pattern.search(description)
            if dept_match:
                department = dept_match.group(1).strip()
                description = dept_pattern.sub("", description).strip()
            else:
                department = _first_nonempty(row, ["주관부서", "department", "부서"])
            
            title = description.split("\n")[0]
            obj = Schedule(
                title=title, start_date=start_val, end_date=end_val,
                category=category, department=department, content=description
            )
            sch_objs.append(obj)
            parsed_objs.append(obj)
            
        session.add_all(sch_objs)
        session.commit()
        df["db_object"] = parsed_objs
        
        # Build chunks INSIDE the session block to access lazy-loaded attributes
        chunks_df = build_schedule_chunks(df)
    finally:
        session.close()

    _save_chunks_to_sqlite(chunks_df, "schedule")
    return _persist_chunks("schedule", DATASET_ARTIFACTS["schedule"].collection, chunks_df)


# --- Courses ---

def build_course_chunks(combined: pd.DataFrame) -> pd.DataFrame:
    docs: List[dict] = []
    ignored_exact = {"_source_table", "db_id", "db_object"}
    title_candidates = ["국문교과목명", "과목명", "course_name", "교과목명", "title", "교과목"]
    
    for _, row in combined.iterrows():
        db_id = row.get("db_id")
        title = next((str(row.get(col, "")).strip() for col in title_candidates if str(row.get(col, "")).strip()), "통계학과 교과")
        code = str(row.get("학수번호", "")).strip()
        doc_id = make_doc_id("courses", code or title, row.get("_source_table"))

        text_parts: List[str] = []
        for col, value in row.items():
            if col in ignored_exact or col.startswith("Unnamed"):
                continue
            value_str = str(value).strip()
            if not value_str:
                continue
            if col in title_candidates:
                text_parts.append(value_str)
            else:
                label = normalize_whitespace(col)
                text_parts.append(f"{label}: {value_str}")
        text = "\n".join(text_parts).strip()
        if not text:
            continue
        
        docs.append(
            {
                "doc_id": doc_id,
                "title": title,
                "text": text,
                "course_code": code,
                "source_table": row.get("_source_table", ""),
                "topics": row.get("_source_table", ""),
                "source": "courses",
                "url": "",
                "published_at": "",
                "course_id": db_id
            }
        )

    chunks = to_chunks(
        docs,
        chunk_size=None, 
        chunk_overlap=0,
        include_title=True,
    )
    return pd.DataFrame(chunks)


def ingest_courses() -> Tuple[pd.DataFrame, object, object]:
    paths = [DATA_SOURCES["courses_desc"], DATA_SOURCES["courses_major"]]
    frames: List[pd.DataFrame] = []
    for key, path in zip(["description", "major"], paths):
        if not path.exists():
            continue
        df = pd.read_csv(path).fillna("").astype(str)
        df["_source_table"] = key
        frames.append(df)

    if not frames:
        raise FileNotFoundError("Course CSV files are missing.")

    combined = pd.concat(frames, ignore_index=True)
    
    session = SessionLocal()
    try:
        session.query(Chunk).filter(Chunk.course_id.isnot(None)).delete()
        session.query(Course).delete()
        session.commit()
        
        course_objs = []
        title_candidates = ["국문교과목명", "과목명", "course_name", "교과목명", "title", "교과목"]
        
        for _, row in combined.iterrows():
            title = next((str(row.get(col, "")).strip() for col in title_candidates if str(row.get(col, "")).strip()), "통계학과 교과")
            code = str(row.get("학수번호", "")).strip()
            
            # Pandas Series를 dict로 변환 시 int64 등이 json serializable 하지 않을 수 있음
            row_dict = row.to_dict()
            # 간단한 타입 변환
            safe_dict = {k: str(v) for k, v in row_dict.items()}
            raw_json = json.dumps(safe_dict, ensure_ascii=False)
            
            obj = Course(
                course_code=code, title=title, 
                source_table=row.get("_source_table"),
                raw_data=raw_json,
                description="" 
            )
            course_objs.append(obj)
            
        session.add_all(course_objs)
        session.commit()
        combined["db_id"] = [obj.id for obj in course_objs]
    finally:
        session.close()
        
    chunks_df = build_course_chunks(combined)
    _save_chunks_to_sqlite(chunks_df, "courses")
    return _persist_chunks("courses", DATASET_ARTIFACTS["courses"].collection, chunks_df)


def ingest_all() -> Dict[str, Tuple[pd.DataFrame, object, object]]:
    # DB 테이블 생성/확인
    init_db()
    
    results: Dict[str, Tuple[pd.DataFrame, object, object]] = {}
    # 순서대로 실행
    results["notices"] = ingest_notices()
    results["rules"] = ingest_rules()
    results["schedule"] = ingest_schedule()
    results["courses"] = ingest_courses()
    return results


def main() -> None:
    # CLI 실행 시 초기화
    init_db()
    artifacts = ingest_all()
    for key, (chunks_df, _, _) in artifacts.items():
        print(f"✅ {key}: {len(chunks_df)} chunks indexed")


if __name__ == "__main__":
    main()


__all__ = [
    "DATASET_ARTIFACTS",
    "build_notice_chunks",
    "build_rule_chunks",
    "build_schedule_chunks",
    "build_course_chunks",
    "ingest_notices",
    "ingest_rules",
    "ingest_schedule",
    "ingest_courses",
    "ingest_all",
]