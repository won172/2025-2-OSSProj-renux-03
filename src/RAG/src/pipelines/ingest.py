"""여러 데이터셋에 대한 Chroma 인덱스 및 SQLite DB를 구축하는 데이터 수집 루틴입니다."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
import json
import argparse # Add logging import

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
from src.vectorstore.chroma_client import add_items, reset_collection, upsert_items, get_all_ids, delete_items, get_existing_ids
from src.database import (
    SessionLocal, engine, init_db,
    Notice, Rule, Schedule, Course, Staff, Chunk, CustomKnowledge
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
    "staff": DatasetArtifacts(
        key="staff",
        collection="dongguk_staff",
        chunk_path=CHUNKS_DIR / "staff.parquet",
    ),
}


def _persist_chunks(key: str, collection: str, chunks_df: pd.DataFrame) -> Tuple[pd.DataFrame, object, object]:
    if chunks_df.empty:
        print(f"⚠️ Warning: No chunks generated for {key}")
        return chunks_df, None, None

    # 메타데이터 준비
    metadatas = chunks_df.drop(columns=["chunk_text"]).to_dict(orient="records")
    metadatas = [{k: (v if v is not None else "") for k, v in m.items()} for m in metadatas]

    # 1. 기존 ID 조회 (전체가 아닌, 현재 chunks_df에 있는 ID들만 확인)
    target_ids = chunks_df["chunk_id"].astype(str).tolist()
    existing_ids = get_existing_ids(collection, target_ids)
    
    # 2. 신규 또는 업데이트가 필요한 청크 식별
    # 여기서는 간단하게 existing_ids에 없는 것만 추가(Add)하는 전략을 사용하거나
    # 항상 덮어쓰기(Upsert)를 할 수 있습니다. 
    # 효율성을 위해 '없는 것만 추가' + '기존 것은 무시' 전략을 선택할 수도 있지만,
    # 내용이 변경되었을 수 있으므로 Upsert가 안전합니다.
    # 하지만 Upsert는 모든 청크에 대해 임베딩을 다시 계산해야 하므로 비용이 듭니다.
    # 데이터 무결성을 위해 Upsert를 유지하되, 임베딩 계산을 최적화합니다.

    # 임베딩 계산 (전체 다 계산)
    # 최적화: 이미 존재하는 ID에 대해서는 임베딩 계산을 건너뛰고 싶다면?
    # -> 내용이 바뀌었는지 알 수 없으므로 위험함.
    # -> 하지만 chunk_id가 내용 해시를 포함한다면 건너뛰어도 됨.
    # -> 현재 make_doc_id는 (제목, 날짜 등)만 포함하므로 내용 변경 감지 불가.
    # -> 따라서 안전하게 전체 Upsert 수행.
    
    embeddings = encode_texts(chunks_df["chunk_text"].tolist())

    upsert_items(
        collection,
        ids=chunks_df["chunk_id"],
        documents=chunks_df["chunk_text"],
        metadatas=metadatas,
        embeddings=embeddings,
    )

    # 3. 파일 저장 (Parquet/CSV)
    artifacts = DATASET_ARTIFACTS[key]
    artifacts.chunk_path.parent.mkdir(parents=True, exist_ok=True)

    write_path = artifacts.chunk_path
    try:
        chunks_df.astype(str).to_parquet(write_path, index=False)
    except Exception:
        write_path = artifacts.csv_path
        chunks_df.to_csv(write_path, index=False, encoding="utf-8-sig")
    artifacts.chunk_path = write_path

    # 4. TF-IDF 학습 (여전히 전체 데이터 필요)
    vectorizer, matrix = train_tfidf(key, chunks_df["chunk_text"].tolist())
    return chunks_df, vectorizer, matrix


def persist_dataset_artifacts_only(key: str, chunks_df: pd.DataFrame) -> Tuple[pd.DataFrame, object, object]:
    """Chroma upsert 없이 청크 아티팩트와 TF-IDF만 갱신합니다."""
    if chunks_df.empty:
        print(f"⚠️ Warning: No chunks generated for {key}")
        return chunks_df, None, None

    artifacts = DATASET_ARTIFACTS[key]
    artifacts.chunk_path.parent.mkdir(parents=True, exist_ok=True)

    write_path = artifacts.chunk_path
    try:
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
    cols = ["chunk_id", "chunk_text", "notice_id", "rule_id", "schedule_id", "course_id", "staff_id"]
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
        text_content = row.get("clean_text", "")
        if not isinstance(text_content, str) or not text_content.strip():
            continue
        
        topic_type = row.get(column["topic"], "")
        published_date = row.get("clean_date", "")

        prefix_parts = []
        if topic_type:
            prefix_parts.append(f"게시판: {topic_type}")
        if published_date:
            prefix_parts.append(f"게시일: {published_date}")
            
        if prefix_parts:
            text_content = f"[{', '.join(prefix_parts)}]\n\n{text_content}"
        
        # URL을 포함하여 고유 ID 생성 (중복 방지 핵심)
        doc_id = (
            row.get("document_key")
            or row.get("문서키")
            or make_doc_id(row.get(column["title"]), row.get(column["topic"]), published_date, row.get(column["url"]))
        )
        
        raw_attachments = row.get(column["attachment"], [])
        if isinstance(raw_attachments, list):
            attachments_str = json.dumps(raw_attachments, ensure_ascii=False)
        else:
            attachments_str = str(raw_attachments)

        docs.append(
            {
                "doc_id": doc_id,
                "title": row.get(column["title"], ""),
                "text": text_content,
                "topics": row.get(column["topic"], ""),
                "category": row.get("카테고리", ""),
                "published_at": published_date or "",
                "url": row.get(column["url"], ""),
                "attachments": attachments_str,
                "source": "notices",
                "notice_id": row.get("db_id"),
                "document_key": row.get("document_key") or row.get("문서키"),
                "source_id": row.get("source_id") or row.get("원문ID"),
                "board_code": row.get("board_code") or row.get("게시판코드"),
                "article_id": row.get("article_id") or row.get("원문글ID"),
            }
        )

    chunks = to_chunks(
        docs,
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        include_title=True,
    )
    # 메모리 내 중복 ID 제거
    chunks_df = pd.DataFrame(chunks)
    if not chunks_df.empty:
        chunks_df.drop_duplicates(subset=["chunk_id"], inplace=True)
    return chunks_df


def ingest_notices() -> Tuple[pd.DataFrame, object, object]:
    path = DATA_SOURCES["notices"]
    if not path.exists():
        raise FileNotFoundError(f"Notice CSV not found: {path}")

    raw_df = pd.read_csv(path)
    
    session = SessionLocal()
    try:
        # 1. 기존 데이터 삭제 (공지사항과 연결된 모든 청크 삭제)
        session.query(Chunk).filter(Chunk.notice_id.isnot(None)).delete(synchronize_session=False)
        session.query(Notice).filter((Notice.is_manual == 0) | (Notice.is_manual.is_(None))).delete(synchronize_session=False)
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
    # DB에 남아있는 수동 데이터(manual notices)를 가져와서 raw_df에 합침
    manual_notices = session.query(Notice).filter(Notice.is_manual == 1).all()
    manual_data = []
    for n in manual_notices:
        manual_data.append({
            "게시판": n.board,
            "제목": n.title,
            "카테고리": n.category,
            "게시일": n.published_date,
            "상단고정": n.is_fixed,
            "상세URL": n.detail_url,
            "본문": n.content,
            "첨부파일": n.attachments, # JSON string or list?
            "db_id": n.id
        })
    
    if manual_data:
        manual_df = pd.DataFrame(manual_data)
        # raw_df에는 db_id가 이미 있음 (3. ID 매핑 단계에서).
        # manual_df와 합치기.
        raw_df = pd.concat([raw_df, manual_df], ignore_index=True)

    chunks_df = build_notice_chunks(raw_df)
    _save_chunks_to_sqlite(chunks_df, "notices")
    
    return _persist_chunks("notices", DATASET_ARTIFACTS["notices"].collection, chunks_df)


def build_notice_index_frame_from_session(session: Session) -> pd.DataFrame:
    notice_rows = []
    query_notices = session.query(Chunk, Notice).join(Notice, Chunk.notice_id == Notice.id)
    for chunk, notice in query_notices.all():
        notice_rows.append(
            {
                "chunk_id": chunk.chunk_id,
                "chunk_text": chunk.chunk_text,
                "title": notice.title,
                "topics": notice.board,
                "published_at": notice.published_date,
                "url": notice.detail_url,
                "attachments": notice.attachments,
                "source": "notices",
                "notice_id": notice.id,
                "category": notice.category,
                "question": None,
                "answer": None,
                "custom_knowledge_id": None,
            }
        )

    query_custom_knowledge = session.query(Chunk, CustomKnowledge).join(
        CustomKnowledge,
        Chunk.custom_knowledge_id == CustomKnowledge.id,
    )
    for chunk, ck in query_custom_knowledge.all():
        notice_rows.append(
            {
                "chunk_id": chunk.chunk_id,
                "chunk_text": chunk.chunk_text,
                "title": ck.question,
                "topics": ck.category or "CustomKnowledge",
                "published_at": ck.created_at.strftime("%Y-%m-%d") if ck.created_at else "",
                "url": "",
                "attachments": "[]",
                "source": "custom_knowledge",
                "notice_id": None,
                "category": ck.category,
                "question": ck.question,
                "answer": ck.answer,
                "custom_knowledge_id": ck.id,
            }
        )

    return pd.DataFrame(notice_rows) if notice_rows else pd.DataFrame()


def build_notice_index_frame_from_db() -> pd.DataFrame:
    session = SessionLocal()
    try:
        return build_notice_index_frame_from_session(session)
    finally:
        session.close()


# --- Rules ---

def build_rule_chunks(df: pd.DataFrame) -> pd.DataFrame:
    docs: List[dict] = []
    for _, row in df.iterrows():
        text = _first_nonempty(row, ["text", "내용", "본문", "article", "조문", "rule_text"])
        if not text:
            continue
        filename = _first_nonempty(row, ["filename", "파일명", "규정명", "title"])
        rel_dir = _first_nonempty(row, ["relative_dir", "경로", "folder"])
        entry_year = _first_nonempty(row, ["entry_year", "학번", "입학년도"])
        section = _first_nonempty(row, ["section", "섹션", "section_name"])
        college_name = _first_nonempty(row, ["college_name", "단과대학", "대학"])
        source_type = _first_nonempty(row, ["source_type", "문서유형"]) or "rules_text"
        source_file = _first_nonempty(row, ["source_file", "source_filename", "파일명", "filename"])
        published_at = _first_nonempty(row, ["published_at", "게시일", "기준일"])
        title = _first_nonempty(row, ["title", "규정명", "filename", "파일명"]) or text[:80] or "학칙 문서"
        doc_id = make_doc_id("rules", rel_dir, filename or title, entry_year, section, college_name)
        docs.append(
            {
                "doc_id": doc_id,
                "title": title,
                "text": text,
                "topics": section or "규정",
                "relative_dir": rel_dir,
                "filename": filename,
                "source": "rules",
                "url": "",
                "published_at": published_at,
                "rule_id": row.get("db_id"),
                "entry_year": entry_year,
                "section": section,
                "college_name": college_name,
                "source_type": source_type,
                "source_file": source_file,
            }
        )

    chunks = to_chunks(
        docs,
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        include_title=True,
    )
    chunks_df = pd.DataFrame(chunks)
    if not chunks_df.empty:
        chunks_df.drop_duplicates(subset=["chunk_id"], inplace=True)
    return chunks_df


def ingest_rules() -> Tuple[pd.DataFrame, object, object]:
    path = DATA_SOURCES["rules"]
    entry_year_guides_path = DATA_SOURCES["rules_entry_year_guides"]
    if not path.exists():
        raise FileNotFoundError(f"Rule CSV not found: {path}")

    if not entry_year_guides_path.exists():
        try:
            from src.crawlers.dongguk_entry_year_guide import build_entry_year_guide_dataframe

            guide_df = build_entry_year_guide_dataframe()
            if not guide_df.empty:
                guide_df.to_csv(entry_year_guides_path, index=False, encoding="utf-8-sig")
        except Exception:
            pass

    frames = [pd.read_csv(path).fillna("").astype(str)]
    if entry_year_guides_path.exists():
        frames.append(pd.read_csv(entry_year_guides_path).fillna("").astype(str))
    df = pd.concat(frames, ignore_index=True).fillna("").astype(str)
    
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
        
        # 학사일정 키워드와 날짜 정보를 텍스트에 포함
        date_str = f"{obj.start_date}"
        if obj.end_date and obj.end_date != obj.start_date:
            date_str += f" ~ {obj.end_date}"
        
        rich_text = f"학사일정: {obj.title}\n\n{obj.content}\n\n기간: {date_str}"
        if obj.department:
            rich_text += f"\n\n주관부서: {obj.department}"

        docs.append(
            {
                "doc_id": doc_id,
                "title": obj.title,
                "text": rich_text,
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
        # 1. 기존 데이터 삭제 (자동 수집된 것만)
        auto_schedule_query = session.query(Schedule.id).filter((Schedule.is_manual == 0) | (Schedule.is_manual.is_(None)))
        session.query(Chunk).filter(Chunk.schedule_id.in_(auto_schedule_query)).delete(synchronize_session=False)
        session.query(Schedule).filter((Schedule.is_manual == 0) | (Schedule.is_manual.is_(None))).delete(synchronize_session=False)
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
        
        # 수동 데이터 추가
        manual_schedules = session.query(Schedule).filter(Schedule.is_manual == 1).all()
        for ms in manual_schedules:
            # df 구조에 맞게 row 추가 필요
            # build_schedule_chunks는 row["db_object"]를 사용함.
            # 수동 데이터용 row 생성
            new_row = pd.Series()
            # build_schedule_chunks에서 db_object만 있으면 됨.
            new_row["db_object"] = ms
            # df에 추가하지 않고, build_schedule_chunks 로직을 보면 df를 순회함.
            # df에 append하는 것이 좋음.
            # 하지만 df는 문자열로 되어있고, db_object는 객체임.
            # df["db_object"] 컬럼에 객체가 들어있음.
            
            # DataFrame 확장이 번거로우므로, manual_schedules를 리스트로 만들어서 처리할 수도 있지만
            # 기존 로직과의 일관성을 위해 df에 추가.
            ms_df = pd.DataFrame([{"db_object": ms}])
            df = pd.concat([df, ms_df], ignore_index=True)

        chunks_df = build_schedule_chunks(df)
    finally:
        session.close()

    _save_chunks_to_sqlite(chunks_df, "schedule")
    reset_collection(DATASET_ARTIFACTS["schedule"].collection)
    return _persist_chunks("schedule", DATASET_ARTIFACTS["schedule"].collection, chunks_df)


# --- Courses ---

def build_course_chunks(combined: pd.DataFrame) -> pd.DataFrame:
    docs: List[dict] = []
    ignored_exact = {"_source_table", "db_id", "db_object", "major"}
    title_candidates = ["국문교과목명", "과목명", "course_name", "교과목명", "title", "교과목"]
    
    for _, row in combined.iterrows():
        db_id = row.get("db_id")
        title = next((str(row.get(col, "")).strip() for col in title_candidates if str(row.get(col, "")).strip()), "교과목 정보")
        code = str(row.get("학수번호", "")).strip()
        major_name = str(row.get("major", "")).strip() or str(row.get("department_name", "")).strip()
        college_name = str(row.get("college_name", "")).strip()
        curriculum_url = str(row.get("curriculum_url", "")).strip() or str(row.get("source_url", "")).strip()
        doc_id = make_doc_id(
            "courses",
            major_name,
            code or title,
            curriculum_url,
            row.get("section_title", ""),
            row.get("_source_table"),
        )

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
                # 개설학기 포맷팅 (예: "2" -> "2학기")
                if label == "개설학기" and value_str in ["1", "2"]:
                    value_str += "학기"
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
                "url": curriculum_url,
                "published_at": "",
                "course_id": db_id,
                "major": major_name,
                "college_name": college_name,
            }
        )

    chunks = to_chunks(
        docs,
        chunk_size=None, 
        chunk_overlap=0,
        include_title=True,
    )
    chunks_df = pd.DataFrame(chunks)
    if not chunks_df.empty:
        chunks_df.drop_duplicates(subset=["chunk_id"], inplace=True)
    return chunks_df


def _first_nonempty_value(row: pd.Series, candidates: Iterable[str]) -> str:
    for col in candidates:
        if col not in row.index:
            continue
        value = str(row.get(col, "")).strip()
        if value and value.lower() != "nan":
            return value
    return ""


def _load_general_courses_df(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path).fillna("").astype(str)
    if raw.empty:
        return raw

    rows: list[dict[str, str]] = []
    for _, row in raw.iterrows():
        department_name = _first_nonempty_value(row, ["department_name", "department", "major", "학과", "학과명", "전공", "major_name"])
        college_name = _first_nonempty_value(row, ["college_name", "college", "단과대학", "대학", "college_name_ko"])
        course_code = _first_nonempty_value(row, ["course_code", "학수번호", "과목코드", "course_id"])
        title = _first_nonempty_value(row, ["title", "course_name", "교과목명", "국문교과목명", "과목명", "교과목"])
        description = _first_nonempty_value(row, ["description", "해설", "비고", "교과목해설", "설명"])
        source_table = _first_nonempty_value(row, ["source_type", "source_table", "_source_table", "문서유형"]) or "general_courses"
        curriculum_url = _first_nonempty_value(row, ["curriculum_url", "source_url", "url", "상세URL"])

        normalized = row.to_dict()
        normalized["_source_table"] = source_table
        normalized["major"] = department_name
        normalized["department_name"] = department_name
        normalized["college_name"] = college_name
        normalized["학수번호"] = course_code
        normalized["title"] = title
        normalized["description"] = description
        normalized["curriculum_url"] = curriculum_url
        rows.append(normalized)

    df = pd.DataFrame(rows).fillna("").astype(str)
    return df


def ingest_courses() -> Tuple[pd.DataFrame, object, object]:
    all_courses_path = DATA_SOURCES["courses_all"]
    desc_path = DATA_SOURCES["courses_desc"]
    major_path = DATA_SOURCES["courses_major"]

    if all_courses_path.exists():
        combined = _load_general_courses_df(all_courses_path)
        if "record_type" in combined.columns:
            non_error = combined[combined["record_type"].astype(str).str.strip() != "crawl_error"].copy()
            if non_error.empty:
                raise RuntimeError(
                    "dongguk_courses_all.csv contains only crawl_error rows. "
                    "Run the curriculum crawler in a network-enabled environment before ingesting."
                )
            combined = non_error
    else:
        if not desc_path.exists() or not major_path.exists():
            raise FileNotFoundError("Course CSV files are missing.")

        desc_df = pd.read_csv(desc_path).fillna("").astype(str)
        major_df = pd.read_csv(major_path).fillna("").astype(str)
        combined = pd.merge(major_df, desc_df, on="학수번호", how="outer", suffixes=("", "_desc"))
        combined = combined.fillna("")
        combined["_source_table"] = "combined_statistics"
        combined["major"] = "통계학과"
        combined["department_name"] = "통계학과"
        combined["college_name"] = ""
        combined["curriculum_url"] = ""

        if "이수대상" in combined.columns:
            def _normalize_grade(val: str) -> str:
                val = val.replace("학사", "")
                if "," in val:
                    parts = val.replace("년", "").split(",")
                    return ", ".join([f"{p.strip()}학년" for p in parts])
                return val.replace("년", "학년")

            combined["이수대상"] = combined["이수대상"].apply(_normalize_grade)

    session = SessionLocal()
    try:
        session.query(Chunk).filter(Chunk.course_id.isnot(None)).delete()
        session.query(Course).delete()
        session.commit()
        
        course_objs = []
        title_candidates = ["교과목명", "국문교과목명", "course_name", "title", "교과목"]
        
        for _, row in combined.iterrows():
            title = next((str(row.get(col, "")).strip() for col in title_candidates if str(row.get(col, "")).strip()), "교과목 정보")
            code = str(row.get("학수번호", "")).strip()
            
            # 전체 데이터를 JSON으로 저장
            row_dict = row.to_dict()
            safe_dict = {k: str(v) for k, v in row_dict.items()}
            raw_json = json.dumps(safe_dict, ensure_ascii=False)
            
            description = str(row.get("description", "")).strip() or str(row.get("해설", "")).strip()

            obj = Course(
                course_code=code, title=title, 
                source_table=row.get("_source_table"),
                raw_data=raw_json,
                description=description
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


# --- Staff ---

def build_staff_chunks(df: pd.DataFrame) -> pd.DataFrame:
    docs = []
    exclude_cols = {"조직(트리)", "db_id", "raw_data"}
    
    for _, row in df.iterrows():
        # row는 [조직(트리), Data_0, Data_1, ...] 형태
        
        # 1. 조직(트리) 정보
        dept = row.get("조직(트리)", "")
        
        # 2. 나머지 데이터
        info_parts = []
        phone_number = ""
        
        for col in df.columns:
            if col in exclude_cols or col.startswith("Unnamed"): continue
            val = str(row.get(col, "")).strip()
            if not val or val.lower() == "nan":
                continue
            
            # 전화번호 감지 (간단한 패턴)
            if re.match(r'^\d{2,3}[-.]?\d{3,4}[-.]?\d{4}$', val):
                phone_number = val
            else:
                info_parts.append(val)
        
        content = " ".join(info_parts)
        
        # 제목: 부서명 - (첫 번째 데이터: 보통 이름/직위)
        name_candidate = info_parts[0] if info_parts else "교직원"
        title = f"{dept} - {name_candidate}"
        
        full_text = f"소속: {dept}\n\n정보: {content}"
        if phone_number:
            full_text += f"\n\n전화번호: {phone_number}"
        
        doc_id = make_doc_id("staff", dept, full_text)
        
        docs.append({
            "doc_id": doc_id,
            "title": title,
            "text": full_text,
            "topics": dept,
            "source": "staff",
            "staff_id": row.get("db_id"),
            "url": "",
            "published_at": "",
        })
        
    chunks = to_chunks(docs, chunk_size=CHUNK_SIZE, chunk_overlap=0, include_title=True)
    return pd.DataFrame(chunks)


def ingest_staff() -> Tuple[pd.DataFrame, object, object]:
    path = DATA_SOURCES["staff"]
    if not path.exists():
        print(f"⚠️ Staff CSV not found: {path}")
        return pd.DataFrame(), None, None

    df = pd.read_csv(path).fillna("").astype(str)
    
    session = SessionLocal()
    try:
        session.query(Chunk).filter(Chunk.staff_id.isnot(None)).delete()
        session.query(Staff).delete()
        session.commit()
        
        staff_objs = []
        for _, row in df.iterrows():
            raw_json = json.dumps(row.to_dict(), ensure_ascii=False)
            dept = row.get("조직(트리)", "")
            
            # 이름 필드 추정 (첫 번째 데이터 컬럼 사용)
            # 실제 컬럼명은 Data_0, Data_1...
            name_val = ""
            for col in df.columns:
                if col.startswith("Data_"):
                    val = row.get(col, "").strip()
                    if val:
                        name_val = val
                        break
            
            obj = Staff(
                department=dept,
                name=name_val,
                raw_data=raw_json
            )
            staff_objs.append(obj)
            
        session.add_all(staff_objs)
        session.commit()
        df["db_id"] = [obj.id for obj in staff_objs]
    finally:
        session.close()
        
    chunks_df = build_staff_chunks(df)
    _save_chunks_to_sqlite(chunks_df, "staff")
    reset_collection(DATASET_ARTIFACTS["staff"].collection)
    return _persist_chunks("staff", DATASET_ARTIFACTS["staff"].collection, chunks_df)


def ingest_all() -> Dict[str, Tuple[pd.DataFrame, object, object]]:
    # DB 테이블 생성/확인
    init_db()
    
    results: Dict[str, Tuple[pd.DataFrame, object, object]] = {}
    # 순서대로 실행
    results["notices"] = ingest_notices()
    results["rules"] = ingest_rules()
    results["schedule"] = ingest_schedule()
    results["courses"] = ingest_courses()
    results["staff"] = ingest_staff()
    return results


def reindex_from_db(target: str | None = None) -> Dict[str, Tuple[pd.DataFrame, object, object]]:
    """SQLite DB에 저장된 데이터를 기반으로 ChromaDB 인덱스와 TF-IDF를 재구축합니다."""
    session = SessionLocal()
    results = {}
    
    try:
        # 1. Notices
        if not target or target == "notices":
            print("🔄 Re-indexing notices from DB...")
            
            # Existing Notice query
            query_notices = session.query(Chunk, Notice).join(Notice, Chunk.notice_id == Notice.id)
            notice_data = []
            for chunk, notice in query_notices.all():
                notice_data.append({
                    "chunk_id": chunk.chunk_id,
                    "chunk_text": chunk.chunk_text,
                    "title": notice.title,
                    "topics": notice.board,
                    "published_at": notice.published_date,
                    "url": notice.detail_url,
                    "attachments": notice.attachments,
                    "source": "notices",
                    "notice_id": notice.id,
                    "category": notice.category,
                    "question": None, 
                    "answer": None, 
                    "custom_knowledge_id": None 
                })

            # New CustomKnowledge query
            query_custom_knowledge = session.query(Chunk, CustomKnowledge).join(CustomKnowledge, Chunk.custom_knowledge_id == CustomKnowledge.id)
            custom_knowledge_data = []
            for chunk, ck in query_custom_knowledge.all():
                custom_knowledge_data.append({
                    "chunk_id": chunk.chunk_id,
                    "chunk_text": chunk.chunk_text,
                    "title": ck.question, 
                    "topics": ck.category or "CustomKnowledge", 
                    "published_at": ck.created_at.strftime("%Y-%m-%d") if ck.created_at else "", 
                    "url": "", 
                    "attachments": "[]", 
                    "source": "custom_knowledge", 
                    "notice_id": None, 
                    "category": ck.category,
                    "question": ck.question,
                    "answer": ck.answer,
                    "custom_knowledge_id": ck.id
                })
            
            # Combine both data sources
            all_notices_data = notice_data + custom_knowledge_data

            if all_notices_data:
                df = pd.DataFrame(all_notices_data)
                results["notices"] = _persist_chunks("notices", DATASET_ARTIFACTS["notices"].collection, df)
        
        # 2. Rules
        if not target or target == "rules":
            print("🔄 Re-indexing rules from DB...")
            query = session.query(Chunk, Rule).join(Rule, Chunk.rule_id == Rule.id)
            data = []
            for chunk, rule in query.all():
                data.append({
                    "chunk_id": chunk.chunk_id,
                    "chunk_text": chunk.chunk_text,
                    "title": rule.filename,
                    "topics": "규정",
                    "relative_dir": rule.relative_dir,
                    "filename": rule.filename,
                    "source": "rules",
                    "url": "",
                    "published_at": "",
                    "rule_id": rule.id
                })
            if data:
                df = pd.DataFrame(data)
                results["rules"] = _persist_chunks("rules", DATASET_ARTIFACTS["rules"].collection, df)

        # 3. Schedule
        if not target or target == "schedule":
            print("🔄 Re-indexing schedule from DB...")
            query = session.query(Chunk, Schedule).join(Schedule, Chunk.schedule_id == Schedule.id)
            data = []
            for chunk, sch in query.all():
                data.append({
                    "chunk_id": chunk.chunk_id,
                    "chunk_text": chunk.chunk_text,
                    "title": sch.title,
                    "schedule_start": sch.start_date,
                    "schedule_end": sch.end_date,
                    "category": sch.category,
                    "department": sch.department,
                    "topics": sch.category or "schedule",
                    "source": "schedule",
                    "url": "",
                    "published_at": sch.start_date,
                    "schedule_id": sch.id
                })
            if data:
                df = pd.DataFrame(data)
                reset_collection(DATASET_ARTIFACTS["schedule"].collection)
                results["schedule"] = _persist_chunks("schedule", DATASET_ARTIFACTS["schedule"].collection, df)

        # 4. Courses
        if not target or target == "courses":
            print("🔄 Re-indexing courses from DB...")
            query = session.query(Chunk, Course).join(Course, Chunk.course_id == Course.id)
            data = []
            for chunk, course in query.all():
                try:
                    raw_data = json.loads(course.raw_data) if course.raw_data else {}
                except:
                    raw_data = {}
                
                data.append({
                    "chunk_id": chunk.chunk_id,
                    "chunk_text": chunk.chunk_text,
                    "title": course.title,
                    "course_code": course.course_code,
                    "source_table": course.source_table,
                    "topics": course.source_table,
                    "source": "courses",
                    "url": "",
                    "published_at": "",
                    "course_id": course.id,
                    "major": raw_data.get("major", "")
                })
            if data:
                df = pd.DataFrame(data)
                results["courses"] = _persist_chunks("courses", DATASET_ARTIFACTS["courses"].collection, df)

        # 5. Staff (New)
        if not target or target == "staff":
            print("🔄 Re-indexing staff from DB...")
            query = session.query(Chunk, Staff).join(Staff, Chunk.staff_id == Staff.id)
            data = []
            for chunk, staff in query.all():
                data.append({
                    "chunk_id": chunk.chunk_id,
                    "chunk_text": chunk.chunk_text,
                    "title": f"{staff.department} - {staff.name}",
                    "topics": staff.department,
                    "source": "staff",
                    "url": "",
                    "published_at": "",
                    "staff_id": staff.id
                })
            if data:
                df = pd.DataFrame(data)
                reset_collection(DATASET_ARTIFACTS["staff"].collection)
                results["staff"] = _persist_chunks("staff", DATASET_ARTIFACTS["staff"].collection, df)

    finally:
        session.close()
        
    return results


def main() -> None:
    # CLI 실행 시 초기화
    init_db()
    
    parser = argparse.ArgumentParser(description="RAG Data Ingestion Pipeline")
    parser.add_argument(
        "--target",
        type=str,
        choices=["notices", "rules", "schedule", "courses", "staff"],
        help="Specify a single dataset to ingest (e.g., notices). If omitted, all datasets are ingested.",
    )
    parser.add_argument(
        "--from-db",
        action="store_true",
        help="Rebuild index from SQLite database instead of raw CSV files.",
    )
    args = parser.parse_args()

    results = {}
    
    if args.from_db:
        print("🚀 Starting Re-indexing from SQLite DB...")
        results = reindex_from_db(args.target)
    elif args.target:
        print(f"🚀 Ingesting only: {args.target}")
        if args.target == "notices":
            results["notices"] = ingest_notices()
        elif args.target == "rules":
            results["rules"] = ingest_rules()
        elif args.target == "schedule":
            results["schedule"] = ingest_schedule()
        elif args.target == "courses":
            results["courses"] = ingest_courses()
        elif args.target == "staff":
            results["staff"] = ingest_staff()
    else:
        print("🚀 Ingesting ALL datasets...")
        results = ingest_all()

    for key, (chunks_df, _, _) in results.items():
        print(f"✅ {key}: {len(chunks_df)} chunks indexed")


if __name__ == "__main__":
    main()


__all__ = [
    "DATASET_ARTIFACTS",
    "build_notice_chunks",
    "build_rule_chunks",
    "build_schedule_chunks",
    "build_course_chunks",
    "build_staff_chunks",
    "ingest_notices",
    "ingest_rules",
    "ingest_schedule",
    "ingest_courses",
    "ingest_staff",
    "ingest_all",
    "SessionLocal",
    "reindex_from_db",
]
