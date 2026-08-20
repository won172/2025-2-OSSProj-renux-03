"""여러 데이터셋에 대한 Chroma 인덱스 및 SQLite DB를 구축하는 데이터 수집 루틴입니다."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
import json
import argparse # Add logging import
import hashlib

import pandas as pd
import re
from sqlalchemy.orm import Session

from src.config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    STRUCTURED_CHUNK_SIZE,
    CHUNKS_DIR,
    DATA_SOURCES,
)
from src.models.embedding import encode_texts
from src.search.hybrid import train_bm25
from src.services.campus_scope import (
    classify_campus_scope,
    enrich_documents_with_campus_scope,
)
from src.utils.preprocess import (
    apply_cleaning,
    normalize_whitespace,
    make_doc_id,
    to_chunks,
)
from src.services.notice_versioning import annotate_notice_versions
from src.services.rule_versioning import annotate_rule_versions
from src.services.retrieval_context import enrich_retrieval_fields
from src.vectorstore.chroma_client import add_items, reset_collection, upsert_items, get_all_ids, delete_items, get_existing_ids
from src.database import (
    SessionLocal, engine, init_db,
    Notice, Rule, Schedule, Course, Staff, Chunk, CustomKnowledge, SourceDocument, kst_now
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
    "meals": DatasetArtifacts(
        key="meals",
        collection="dongguk_meals",
        chunk_path=CHUNKS_DIR / "meals.parquet",
    ),
}


def _canonicalize_campus_scope_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach canonical campus metadata to legacy/DB reconstruction frames."""
    if frame.empty:
        return frame.copy()
    canonical = frame.copy()
    canonical["campus_scope"] = [
        classify_campus_scope(row).value for _, row in canonical.iterrows()
    ]
    return canonical


def _chunk_parent_identity(
    chunk: Chunk,
    fallback_doc_id: str,
    fallback_positions: dict[str, int],
) -> tuple[str, int]:
    """Read persisted parent metadata, with a deterministic legacy fallback.

    Old databases predate ``chunks.doc_id`` and ``chunks.position``.  Keeping
    the fallback here lets a DB-only rebuild remain usable while the next
    normal ingest persists the metadata permanently.
    """
    doc_id = str(chunk.doc_id or fallback_doc_id)
    if chunk.position is not None:
        try:
            return doc_id, int(chunk.position)
        except (TypeError, ValueError):
            pass
    position = fallback_positions.get(doc_id, 0)
    fallback_positions[doc_id] = position + 1
    return doc_id, position


def _persist_chunks(key: str, collection: str, chunks_df: pd.DataFrame) -> Tuple[pd.DataFrame, object, object]:
    if chunks_df.empty:
        print(f"⚠️ Warning: No chunks generated for {key}")
        return chunks_df, None, None

    chunks_df = enrich_retrieval_fields(chunks_df)
    retrieval_text = chunks_df["retrieval_text"].fillna("").astype(str)

    # 메타데이터 준비
    metadatas = chunks_df.drop(
        columns=["chunk_text", "retrieval_text"],
        errors="ignore",
    ).to_dict(orient="records")
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
    
    embeddings = encode_texts(retrieval_text.tolist())

    upsert_items(
        collection,
        ids=chunks_df["chunk_id"],
        documents=retrieval_text,
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

    # 4. BM25 학습 (전체 문서 길이 통계가 필요)
    vectorizer, matrix = train_bm25(
        key,
        retrieval_text.tolist(),
        chunk_ids=chunks_df["chunk_id"].astype(str).tolist(),
    )
    return chunks_df, vectorizer, matrix


def persist_dataset_artifacts_only(key: str, chunks_df: pd.DataFrame) -> Tuple[pd.DataFrame, object, object]:
    """Chroma upsert 없이 청크 아티팩트와 BM25만 갱신합니다."""
    if chunks_df.empty:
        print(f"⚠️ Warning: No chunks generated for {key}")
        return chunks_df, None, None

    chunks_df = enrich_retrieval_fields(chunks_df)
    retrieval_text = chunks_df["retrieval_text"].fillna("").astype(str)
    artifacts = DATASET_ARTIFACTS[key]
    artifacts.chunk_path.parent.mkdir(parents=True, exist_ok=True)

    write_path = artifacts.chunk_path
    try:
        chunks_df.astype(str).to_parquet(write_path, index=False)
    except Exception:
        write_path = artifacts.csv_path
        chunks_df.to_csv(write_path, index=False, encoding="utf-8-sig")
    artifacts.chunk_path = write_path

    vectorizer, matrix = train_bm25(
        key,
        retrieval_text.tolist(),
        chunk_ids=chunks_df["chunk_id"].astype(str).tolist(),
    )
    return chunks_df, vectorizer, matrix


def _persist_replacing_collection(
    key: str,
    collection: str,
    chunks_df: pd.DataFrame,
) -> Tuple[pd.DataFrame, object, object]:
    """새 청크를 먼저 올린 뒤 더 이상 유효하지 않은 벡터만 제거한다.

    전량 ``reset`` 후 임베딩하면 CPU 재구축 동안 서비스 컬렉션이 비게 된다.
    upsert가 성공하기 전에는 기존 벡터를 보존하고, 모든 파생 아티팩트 생성까지
    끝난 뒤에만 고아 ID를 정리한다.
    """
    previous_ids = set(get_all_ids(collection))
    result = _persist_chunks(key, collection, chunks_df)
    current_ids = set(chunks_df["chunk_id"].astype(str))
    stale_ids = sorted(previous_ids - current_ids)
    if stale_ids:
        delete_items(collection, stale_ids)
    return result


def _save_chunks_to_sqlite(chunks_df: pd.DataFrame, source_key: str):
    """SQLite의 chunks 테이블에 저장합니다."""
    if chunks_df.empty:
        return
    
    # 필요한 컬럼만 선택 및 확보
    cols = [
        "chunk_id", "chunk_text", "doc_id", "position", "notice_id", "rule_id",
        "schedule_id", "course_id", "staff_id", "custom_knowledge_id",
    ]
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


_TITLE_DEADLINE_PATTERNS = (
    # (~9/20), (마감 6월 21일), (4/1~7/31)
    re.compile(
        r"(?:~|마감|기한|까지)\s*"
        r"(?:(?P<year>\d{4})\s*[.\-/년]\s*)?"
        r"(?P<month>\d{1,2})\s*(?:[.\-/월])\s*"
        r"(?P<day>\d{1,2})\s*일?"
    ),
    # (2026.08.06까지), (10월 12일 마감), (12.22까지)
    re.compile(
        r"(?:(?P<year>\d{4})\s*[.\-/년]\s*)?"
        r"(?P<month>\d{1,2})\s*(?:[.\-/월])\s*"
        r"(?P<day>\d{1,2})\s*일?\s*"
        r"(?:까지|마감|기한)"
    ),
)
_BODY_DEADLINE_KEYWORD_PATTERN = re.compile(
    r"("
    r"지원서\s*접수|지원\s*기간|지원\s*마감|모집\s*기간|모집\s*마감|"
    r"신청\s*기간|신청기간|접수\s*기간|접수기간|"
    r"제출\s*기간|제출기간|서류\s*제출|서류제출|"
    r"등록\s*기간|등록기간|납부\s*기간|납부기간|"
    r"수강\s*신청|수강신청|수강\s*취소|수강취소|"
    r"신청\s*마감|접수\s*마감|제출\s*마감|마감\s*일|마감일|"
    r"신청\s*기한|접수\s*기한|제출\s*기한|기한"
    r")"
)
_BODY_FULL_DATE_END_PATTERN = re.compile(
    r"(?:~|-|–|—|∼|〜)\s*"
    r"(?P<year>\d{4})\s*[.\-/년]\s*"
    r"(?P<month>\d{1,2})\s*[.\-/월]\s*"
    r"(?P<day>\d{1,2})"
)
_BODY_MONTH_DAY_END_PATTERN = re.compile(
    r"(?:~|-|–|—|∼|〜)\s*(?P<month>\d{1,2})\s*[.\-/월]\s*(?P<day>\d{1,2})"
)
_BODY_FULL_DATE_SINGLE_PATTERN = re.compile(
    r"(?P<year>\d{4})\s*[.\-/년]\s*"
    r"(?P<month>\d{1,2})\s*[.\-/월]\s*"
    r"(?P<day>\d{1,2})"
)
_BODY_MONTH_DAY_SINGLE_PATTERN = re.compile(
    r"(?P<month>\d{1,2})\s*[\-/월]\s*(?P<day>\d{1,2})"
)


def _parse_date_parts(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_notice_deadline_from_title(title: object, published_date: str | None) -> str | None:
    if not isinstance(title, str) or not published_date:
        return None

    published = pd.to_datetime(published_date, errors="coerce")
    if pd.isna(published):
        return None

    match = next(
        (
            candidate
            for pattern in _TITLE_DEADLINE_PATTERNS
            if (candidate := pattern.search(title)) is not None
        ),
        None,
    )
    if not match:
        return None

    deadline = _parse_date_parts(
        int(match.group("year") or published.year),
        int(match.group("month")),
        int(match.group("day")),
    )
    if deadline is None:
        return None

    published_day = published.date()
    if deadline < published_day:
        deadline = _parse_date_parts(deadline.year + 1, deadline.month, deadline.day)
        if deadline is None:
            return None
    return deadline.strftime("%Y-%m-%d")


def _parse_notice_deadline_from_body(content: object, published_date: str | None) -> str | None:
    if not isinstance(content, str) or not content.strip():
        return None

    published = pd.to_datetime(published_date, errors="coerce") if published_date else pd.NaT
    published_year = None if pd.isna(published) else int(published.year)

    keyword_match = _BODY_DEADLINE_KEYWORD_PATTERN.search(content)
    if not keyword_match:
        return None

    # 기간 라벨 근처만 본다. 너무 넓게 잡으면 본문 내 다른 날짜를 마감일로 오인할 수 있다.
    window = content[keyword_match.end() : keyword_match.end() + 180]

    full_match = _BODY_FULL_DATE_END_PATTERN.search(window)
    if full_match:
        deadline = _parse_date_parts(
            int(full_match.group("year")),
            int(full_match.group("month")),
            int(full_match.group("day")),
        )
        return None if deadline is None else deadline.strftime("%Y-%m-%d")

    month_day_match = _BODY_MONTH_DAY_END_PATTERN.search(window)
    if month_day_match and published_year is not None:
        deadline = _parse_date_parts(
            published_year,
            int(month_day_match.group("month")),
            int(month_day_match.group("day")),
        )
        if deadline is None:
            return None
        published_day = published.date()
        if deadline < published_day:
            deadline = _parse_date_parts(deadline.year + 1, deadline.month, deadline.day)
            if deadline is None:
                return None
        return deadline.strftime("%Y-%m-%d")

    full_single_match = _BODY_FULL_DATE_SINGLE_PATTERN.search(window)
    if full_single_match:
        deadline = _parse_date_parts(
            int(full_single_match.group("year")),
            int(full_single_match.group("month")),
            int(full_single_match.group("day")),
        )
        return None if deadline is None else deadline.strftime("%Y-%m-%d")

    month_day_single_match = _BODY_MONTH_DAY_SINGLE_PATTERN.search(window)
    if month_day_single_match and published_year is not None:
        deadline = _parse_date_parts(
            published_year,
            int(month_day_single_match.group("month")),
            int(month_day_single_match.group("day")),
        )
        if deadline is None:
            return None
        published_day = published.date()
        if deadline < published_day:
            deadline = _parse_date_parts(deadline.year + 1, deadline.month, deadline.day)
            if deadline is None:
                return None
        return deadline.strftime("%Y-%m-%d")

    return None


def _extract_notice_apply_deadline(title: object, content: object, published_date: str | None) -> str | None:
    return (
        _parse_notice_deadline_from_title(title, published_date)
        or _parse_notice_deadline_from_body(content, published_date)
    )


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
        raw_title = row.get(column["title"], "")
        title = "" if pd.isna(raw_title) else str(raw_title).strip()
        raw_url = row.get(column["url"], "")
        url = "" if pd.isna(raw_url) else str(raw_url).strip()

        text_content = row.get("clean_text", "")
        if not isinstance(text_content, str):
            text_content = ""
        text_content = text_content.strip()

        # 본문이 비어 제목·링크만으로 채워진 문서인지 여기서 기록해 둔다. 공지 5,528건 중
        # 1,445건(25.6%)이 본문 0자이고 914건은 첨부조차 없다. 이런 문서는 답변을
        # 뒷받침하지 못하면서 근거 자리(데이터셋당 3개)를 차지한다 — "2023년 통계데이터
        # 활용대회"가 근거 그룹 하나를 통째로 쓰고 "본문이 비어 있어 확인이 필요하다"고만
        # 답한 사례가 그것이다.
        has_body = bool(text_content and text_content.strip())
        if not text_content:
            fallback_parts = []
            if title:
                fallback_parts.append(f"공지 제목: {title}")
            if url:
                fallback_parts.append(f"본문이 비어 있어 상세 내용은 공지 링크를 확인하세요: {url}")
            if not fallback_parts:
                continue
            text_content = "\n".join(fallback_parts)

        if not text_content.strip():
            continue
        
        topic_type = row.get(column["topic"], "")
        published_date = row.get("clean_date", "")
        apply_deadline = _extract_notice_apply_deadline(
            title,
            text_content,
            published_date,
        )

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
            # CSV 결측(NaN)이 "nan" 문자열이 되어 다운스트림 json.loads를 실패시키는 것 방지
            if attachments_str.strip().lower() in ("nan", "none", ""):
                attachments_str = "[]"

        docs.append(
            {
                "doc_id": doc_id,
                "title": title,
                "text": text_content,
                "topics": row.get(column["topic"], ""),
                "category": row.get("카테고리", ""),
                "category_original": row.get("카테고리원본", row.get("카테고리", "")),
                "category_source": row.get("카테고리출처", "list" if row.get("카테고리") else "missing"),
                "category_board_fallback": row.get("카테고리게시판대체", ""),
                "published_at": published_date or "",
                "apply_deadline": apply_deadline,
                "url": url,
                "attachments": attachments_str,
                # 첨부가 있으면 링크로 안내할 수 있으므로 근거로서 값이 남는다.
                "has_substantive_body": "1" if (has_body or attachments_str not in ("[]", "")) else "0",
                "source": "notices",
                "source_type": row.get("source_type", "html_notice"),
                "notice_id": row.get("db_id"),
                "document_key": row.get("document_key") or row.get("문서키"),
                "source_id": row.get("source_id") or row.get("원문ID"),
                "board_code": row.get("board_code") or row.get("게시판코드"),
                "article_id": row.get("article_id") or row.get("원문글ID"),
            }
        )

    enrich_documents_with_campus_scope(docs)
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
        chunks_df = annotate_notice_versions(chunks_df)
    return chunks_df


def ingest_notices() -> Tuple[pd.DataFrame, object, object]:
    # Normal operation is strictly SQLite → derived indexes.  CSV remains only
    # as an explicit legacy bootstrap path when no canonical notice exists.
    session = SessionLocal()
    try:
        has_canonical_notices = session.query(SourceDocument.id).filter(SourceDocument.dataset == "notices").first() is not None
    finally:
        session.close()
    if has_canonical_notices:
        from src.pipelines.notices_sync import rebuild_notices_from_source_documents
        return rebuild_notices_from_source_documents()

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

        # 4. DB에 남아있는 수동 데이터(manual notices)를 가져와서 raw_df에 합침
        #    세션이 닫히기 전에 조회해야 하므로 try 블록 내부에서 처리한다.
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
    finally:
        session.close()

    # 5. 청크 생성 및 저장
    if manual_data:
        manual_df = pd.DataFrame(manual_data)
        # raw_df에는 db_id가 이미 있음 (3. ID 매핑 단계에서).
        # manual_df와 합치기.
        raw_df = pd.concat([raw_df, manual_df], ignore_index=True)

    chunks_df = build_notice_chunks(raw_df)
    _save_chunks_to_sqlite(chunks_df, "notices")
    
    reset_collection(DATASET_ARTIFACTS["notices"].collection)
    return _persist_chunks("notices", DATASET_ARTIFACTS["notices"].collection, chunks_df)


def build_notice_index_frame_from_session(session: Session) -> pd.DataFrame:
    notice_rows = []
    source_documents = (
        session.query(SourceDocument)
        .filter(SourceDocument.dataset == "notices")
        .all()
    )
    source_documents_by_key = {
        str(doc.document_key): doc
        for doc in source_documents
        if doc.document_key
    }
    source_documents_by_url = {
        str(doc.source_url): doc
        for doc in source_documents
        if doc.source_url and doc.document_key
    }
    fallback_positions: dict[str, int] = {}

    query_notices = (
        session.query(Chunk, Notice)
        .join(Notice, Chunk.notice_id == Notice.id)
        .order_by(Notice.id.asc(), Chunk.position.asc(), Chunk.id.asc())
    )
    for chunk, notice in query_notices.all():
        doc_id, position = _chunk_parent_identity(
            chunk,
            (
                source_documents_by_url[str(notice.detail_url)].document_key
                if str(notice.detail_url) in source_documents_by_url
                else f"notice:{notice.id}"
            ),
            fallback_positions,
        )
        source_document = source_documents_by_key.get(doc_id)
        notice_rows.append(
            {
                "chunk_id": chunk.chunk_id,
                "chunk_text": chunk.chunk_text,
                "doc_id": doc_id,
                "position": position,
                "title": notice.title,
                "topics": notice.board,
                "published_at": notice.published_date,
                # 마감일은 청크가 아니라 공지 문서의 속성이다. 청크마다 다시
                # 추출하면 지원서 접수일과 합격자 발표일이 서로 다른 마감으로
                # 저장될 수 있으므로 원문 전체에서 한 번 결정해 공유한다.
                "apply_deadline": _extract_notice_apply_deadline(
                    notice.title,
                    notice.content,
                    notice.published_date,
                ),
                "url": notice.detail_url,
                "attachments": notice.attachments,
                # 본문·첨부가 모두 없으면 근거로 쓸 수 없다(공지의 25.6%가 본문 0자).
                "has_substantive_body": (
                    "1"
                    if ((notice.content or "").strip() or (notice.attachments or "[]") not in ("[]", ""))
                    else "0"
                ),
                "source": "notices",
                "source_type": (
                    source_document.source_type if source_document is not None else "html_notice"
                ),
                "document_key": doc_id,
                "source_id": (
                    source_document.source_id if source_document is not None else ""
                ),
                "notice_id": notice.id,
                "category": notice.category,
                "question": None,
                "answer": None,
                "custom_knowledge_id": None,
            }
        )

    query_custom_knowledge = (
        session.query(Chunk, CustomKnowledge)
        .join(CustomKnowledge, Chunk.custom_knowledge_id == CustomKnowledge.id)
        .order_by(CustomKnowledge.id.asc(), Chunk.position.asc(), Chunk.id.asc())
    )
    for chunk, ck in query_custom_knowledge.all():
        doc_id, position = _chunk_parent_identity(
            chunk,
            f"custom_knowledge:{ck.id}",
            fallback_positions,
        )
        notice_rows.append(
            {
                "chunk_id": chunk.chunk_id,
                "chunk_text": chunk.chunk_text,
                "doc_id": doc_id,
                "position": position,
                "title": ck.question,
                "topics": ck.category or "CustomKnowledge",
                "published_at": ck.created_at.strftime("%Y-%m-%d") if ck.created_at else "",
                "apply_deadline": None,
                "url": "",
                "attachments": "[]",
                # 승인된 지식은 답 본문 자체이므로 항상 근거로 쓸 수 있다.
                "has_substantive_body": "1",
                "source": "custom_knowledge",
                "notice_id": None,
                "category": ck.category,
                "question": ck.question,
                "answer": ck.answer,
                "custom_knowledge_id": ck.id,
            }
        )

    if not notice_rows:
        return pd.DataFrame()
    frame = annotate_notice_versions(pd.DataFrame(notice_rows))
    return _canonicalize_campus_scope_frame(frame)


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
        source_url = _first_nonempty(row, ["source_url", "url", "원문URL"])
        source_page_url = _first_nonempty(row, ["source_page_url", "landing_url", "안내페이지URL"])
        source_version = _first_nonempty(row, ["source_version", "version", "원문버전"])
        source_sha256 = _first_nonempty(row, ["source_sha256", "sha256"])
        page_start = _first_nonempty(row, ["page_start", "시작페이지"])
        page_end = _first_nonempty(row, ["page_end", "종료페이지"])
        page_label = _first_nonempty(row, ["page_label", "인쇄페이지"])
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
                "url": source_url,
                "published_at": published_at,
                "rule_id": row.get("db_id"),
                "entry_year": entry_year,
                "section": section,
                "college_name": college_name,
                "source_type": source_type,
                "source_file": source_file,
                "source_page_url": source_page_url,
                "source_version": source_version,
                "source_sha256": source_sha256,
                "page_start": page_start,
                "page_end": page_end,
                "page_label": page_label,
            }
        )

    enrich_documents_with_campus_scope(docs)
    chunks = to_chunks(
        docs,
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        include_title=True,
    )
    chunks_df = pd.DataFrame(chunks)
    if not chunks_df.empty:
        chunks_df.drop_duplicates(subset=["chunk_id"], inplace=True)
        chunks_df = annotate_rule_versions(chunks_df)
    return chunks_df


def _entry_year_guide_cache_is_stale(output_path: Path, dependencies: Iterable[Path]) -> bool:
    """Rebuild generated guide rows after either source data or parser changes."""
    if not output_path.exists():
        return True
    output_mtime = output_path.stat().st_mtime_ns
    return any(
        dependency.exists() and dependency.stat().st_mtime_ns > output_mtime
        for dependency in dependencies
    )


def ingest_rules(*, force_source_reload: bool = False) -> Tuple[pd.DataFrame, object, object]:
    session = SessionLocal()
    try:
        if (
            not force_source_reload
            and session.query(Rule.id).first() is not None
            and session.query(Chunk.id).filter(Chunk.rule_id.isnot(None)).first() is not None
        ):
            existing = reindex_from_db("rules").get("rules")
            if existing is not None:
                return existing
    finally:
        session.close()

    path = DATA_SOURCES["rules"]
    entry_year_guides_path = DATA_SOURCES["rules_entry_year_guides"]
    if not path.exists():
        raise FileNotFoundError(f"Rule CSV not found: {path}")

    from src.crawlers import dongguk_entry_year_guide as guide_crawler

    guide_dependencies = [
        *entry_year_guides_path.parent.glob(guide_crawler.PDF_GLOB),
        guide_crawler.SOURCE_MANIFEST_PATH,
        Path(guide_crawler.__file__),
    ]
    if _entry_year_guide_cache_is_stale(entry_year_guides_path, guide_dependencies):
        guide_df = guide_crawler.build_entry_year_guide_dataframe()
        if guide_df.empty:
            raise RuntimeError("No entry-year guide sections were extracted.")
        guide_df.to_csv(entry_year_guides_path, index=False, encoding="utf-8-sig")

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
            
            obj = Rule(
                filename=fname,
                relative_dir=rdir,
                full_text=text_val,
                title=_first_nonempty(row, ["title", "규정명"]) or fname,
                source_type=_first_nonempty(row, ["source_type", "문서유형"]),
                source_url=_first_nonempty(row, ["source_url", "url", "원문URL"]),
                source_page_url=_first_nonempty(row, ["source_page_url", "landing_url"]),
                source_version=_first_nonempty(row, ["source_version", "version"]),
                published_at=_first_nonempty(row, ["published_at", "게시일", "기준일"]),
            )
            rule_objs.append(obj)
            
        session.add_all(rule_objs)
        session.commit()
        df["db_id"] = [obj.id for obj in rule_objs]
    finally:
        session.close()
        
    chunks_df = build_rule_chunks(df)
    _save_chunks_to_sqlite(chunks_df, "rules")
    return _persist_replacing_collection(
        "rules",
        DATASET_ARTIFACTS["rules"].collection,
        chunks_df,
    )


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

    enrich_documents_with_campus_scope(docs)
    chunks = to_chunks(
        docs,
        chunk_size=STRUCTURED_CHUNK_SIZE // 2,
        chunk_overlap=CHUNK_OVERLAP // 2,
        include_title=True,
    )
    return pd.DataFrame(chunks)


def ingest_schedule(*, refresh_from_csv: bool = False) -> Tuple[pd.DataFrame, object, object]:
    session = SessionLocal()
    try:
        if (
            not refresh_from_csv
            and session.query(Schedule.id).first() is not None
            and session.query(Chunk.id).filter(Chunk.schedule_id.isnot(None)).first() is not None
        ):
            existing = reindex_from_db("schedule").get("schedule")
            if existing is not None:
                return existing
    finally:
        session.close()

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
        credit = _first_nonempty(row, ["credit_value", "credit", "학점"])
        grade = _first_nonempty(row, ["recommended_grades", "grade", "이수대상", "학년"])
        semester = _first_nonempty(row, ["offered_semesters", "semester", "개설학기", "학기"])
        course_type = _first_nonempty(row, ["course_type", "전공구분", "이수구분"])
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
                "credit": credit,
                "grade": grade,
                "semester": semester,
                "course_type": course_type,
                "curriculum_year": row.get("curriculum_year", ""),
                "source_page": row.get("source_page", ""),
                "source_type": row.get("source_type", ""),
                "source_priority": row.get("source_priority", ""),
                "course_code_conflict": row.get("course_code_conflict", False),
                "availability_status": row.get("availability_status", "curriculum_only"),
                "data_quality_score": row.get("data_quality_score", ""),
                "collection_status": row.get("collection_status", ""),
            }
        )

    # 교과 설명이 비정상적으로 긴 경우 단일 거대 청크가 임베딩 품질을 해치므로
    # 일반 청크의 2배 크기로 상한을 둔다(대부분의 교과목은 한 청크에 그대로 들어감).
    enrich_documents_with_campus_scope(docs)
    chunks = to_chunks(
        docs,
        chunk_size=STRUCTURED_CHUNK_SIZE * 2,
        chunk_overlap=CHUNK_OVERLAP,
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


def ingest_courses(*, refresh_from_csv: bool = False) -> Tuple[pd.DataFrame, object, object]:
    session = SessionLocal()
    try:
        if (
            not refresh_from_csv
            and session.query(Course.id).first() is not None
            and session.query(Chunk.id).filter(Chunk.course_id.isnot(None)).first() is not None
        ):
            existing = reindex_from_db("courses").get("courses")
            if existing is not None:
                return existing
    finally:
        session.close()

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
    # 교과 doc_id는 내용 기반이라 텍스트가 바뀌면 새 ID가 생긴다 —
    # 컬렉션을 리셋하지 않으면 옛 청크가 고아로 남아 검색을 오염시킴(staff/schedule과 동일 패턴).
    reset_collection(DATASET_ARTIFACTS["courses"].collection)
    return _persist_chunks("courses", DATASET_ARTIFACTS["courses"].collection, chunks_df)


# --- Staff ---

def build_staff_chunks(df: pd.DataFrame) -> pd.DataFrame:
    docs = []
    exclude_cols = {"조직(트리)", "db_id", "raw_data"}
    
    for _, row in df.iterrows():
        # row는 명명 컬럼([조직(트리), 성명, 직위, 담당업무, 전화번호]) 또는
        # 구버전([조직(트리), Data_0, Data_1, ...]) 형태 모두 지원

        # 1. 조직(트리) 정보
        dept = row.get("조직(트리)", "")

        # 2. 나머지 데이터
        info_parts = []
        phone_number = ""

        # 명명 컬럼이 있으면 전화번호는 해당 컬럼을 우선 사용("770-2773" 같은 내선형 포함)
        if "전화번호" in df.columns:
            phone_number = str(row.get("전화번호", "")).strip()
            if phone_number.lower() == "nan":
                phone_number = ""

        for col in df.columns:
            if col in exclude_cols or col == "전화번호" or col.startswith("Unnamed"): continue
            val = str(row.get(col, "")).strip()
            if not val or val.lower() == "nan":
                continue

            # 전화번호 감지 (간단한 패턴 — 내선형 'NNN-NNNN'도 인식)
            if not phone_number and re.match(r'^\d{2,4}[-.]?\d{3,4}([-.]?\d{4})?$', val):
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
            # 연락처 질의 순위에 쓰려면 본문에 녹아든 값이 아니라 별도 필드가 필요하다.
            # "사무실 번호"를 물었는데 번호가 없는 교수 행이 1순위로 나오던 문제를 여기서 막는다.
            "staff_position": str(row.get("직위", "") or "").strip(),
            "staff_role": str(row.get("담당업무", "") or "").strip(),
            "staff_phone": phone_number,
        })
        
    enrich_documents_with_campus_scope(docs)
    chunks = to_chunks(
        docs,
        chunk_size=STRUCTURED_CHUNK_SIZE,
        chunk_overlap=0,
        include_title=True,
    )
    return pd.DataFrame(chunks)


def ingest_staff(*, refresh_from_csv: bool = False) -> Tuple[pd.DataFrame, object, object]:
    """교직원 명부를 적재한다.

    `refresh_from_csv=False`(기본)면 DB에 이미 행이 있을 때 CSV를 다시 읽지 않는다.
    courses·schedule과 달리 이 함수에는 예외 인자가 없어서, 적재 로직을 고쳐도
    CSV를 다시 읽힐 방법이 없었다 — 컬럼 매핑 버그가 오래 남은 이유다.
    """
    session = SessionLocal()
    try:
        if refresh_from_csv:
            pass
        elif session.query(Staff.id).first() is not None and session.query(Chunk.id).filter(Chunk.staff_id.isnot(None)).first() is not None:
            existing = reindex_from_db("staff").get("staff")
            if existing is not None:
                return existing
    finally:
        session.close()

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

            # 명명 컬럼(성명·직위·담당업무·전화번호)을 그대로 옮긴다. 예전에는 `Data_`로
            # 시작하는 컬럼에서 이름을 찾았는데 이 CSV의 컬럼은 한글이라 한 건도 걸리지
            # 않았고, 직위·전화번호는 대입조차 없었다. 그 결과 4,303행 전부에서
            # name/position/phone이 비어 raw_data JSON 말고는 쓸 수 있는 값이 없었다.
            # 연락처 질의에서 "번호가 있는 행"이나 "행정 직위"로 추릴 수 없던 원인이다.
            def _named(*candidates: str) -> str:
                for column in candidates:
                    if column not in df.columns:
                        continue
                    value = str(row.get(column, "")).strip()
                    if value and value.lower() != "nan":
                        return value
                return ""

            name_val = _named("성명", "이름", "name")
            if not name_val:
                # 구버전 CSV(Data_0, Data_1 …) 호환.
                for col in df.columns:
                    if col.startswith("Data_"):
                        value = str(row.get(col, "")).strip()
                        if value and value.lower() != "nan":
                            name_val = value
                            break

            obj = Staff(
                department=dept,
                name=name_val,
                position=_named("직위", "position"),
                role=_named("담당업무", "role"),
                phone=_named("전화번호", "phone"),
                email=_named("이메일", "email"),
                raw_data=raw_json,
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


# --- Meals (학식 식단) ---

def build_meal_chunks(df: pd.DataFrame) -> pd.DataFrame:
    """학식 CSV(날짜·식당·메뉴)를 (날짜×식당) 단위 청크로 만듭니다.

    한 끼/하루의 메뉴가 검색 시 통째로 나오도록 (날짜, 식당)당 한 청크로 둔다.
    날짜는 published_at/schedule_start 에 넣어 '오늘/이번주 학식' 날짜 필터가 동작하게 한다.
    """
    docs: List[dict] = []
    for _, row in df.iterrows():
        meal_date = str(row.get("date", "")).strip()
        if not meal_date:
            continue
        weekday = str(row.get("weekday", "")).strip()
        restaurant = str(row.get("restaurant", "")).strip()
        menu_text = str(row.get("menu_text", "")).strip()
        is_closed = str(row.get("is_closed", "")).strip().lower() in {"true", "1", "1.0"}

        date_label = f"{meal_date}({weekday})" if weekday else meal_date
        title = f"{date_label} {restaurant} 학식"

        if is_closed or not menu_text or menu_text == "휴무":
            body = f"{restaurant}는 {date_label}에 휴무입니다."
        else:
            body = menu_text
        rich_text = f"{date_label} {restaurant} 학식 식단 메뉴\n\n{body}"

        doc_id = make_doc_id("meals", meal_date, restaurant)
        docs.append(
            {
                "doc_id": doc_id,
                "title": title,
                "text": rich_text,
                "topics": f"학식 식단 메뉴 {restaurant}",
                "source": "meals",
                "restaurant": restaurant,
                "meal_date": meal_date,
                "weekday": weekday,
                # 휴무 청크는 검색 시 약하게 패널티를 받아(메뉴/가격 질의에서 운영일이 우선),
                # 단 휴무 여부 질의에는 여전히 노출되도록 인덱스에는 남긴다.
                "is_closed": "1" if (is_closed or body.strip().endswith("휴무입니다.")) else "0",
                "schedule_start": meal_date,
                "schedule_end": meal_date,
                "published_at": meal_date,
                "url": "https://dgucoop.dongguk.edu/store/store.php?w=4",
            }
        )

    # 하루·식당당 한 청크(분할 안 함) — 메뉴 전체가 하나의 근거로 검색되도록.
    enrich_documents_with_campus_scope(docs)
    chunks = to_chunks(docs, chunk_size=None, include_title=True)
    return pd.DataFrame(chunks)


def _meal_document_key(row: pd.Series) -> str:
    return f"meals:{str(row.get('date', '')).strip()}:{str(row.get('restaurant', '')).strip()}"


def store_meals_in_db(df: pd.DataFrame) -> int:
    """Persist collected meal rows before any indexing work.

    ``SourceDocument`` is the canonical record for volatile, externally
    collected meals.  The original DataFrame can therefore disappear after the
    request without affecting reindex or recovery.
    """
    if df.empty:
        return 0
    session = SessionLocal()
    try:
        now = kst_now()
        seen: set[str] = set()
        for _, raw_row in df.fillna("").astype(str).iterrows():
            row = raw_row.to_dict()
            source_id = f"{row.get('date', '').strip()}:{row.get('restaurant', '').strip()}"
            if not source_id.strip(":"):
                continue
            seen.add(source_id)
            document_key = f"meals:{source_id}"
            payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
            document = (
                session.query(SourceDocument)
                .filter(SourceDocument.dataset == "meals", SourceDocument.source_id == source_id)
                .one_or_none()
            )
            if document is None:
                document = SourceDocument(
                    dataset="meals",
                    source_type="html_meal",
                    source_id=source_id,
                    document_key=document_key,
                )
                session.add(document)
            document.source_url = "https://dgucoop.dongguk.edu/store/store.php?w=4"
            document.title = f"{row.get('date', '').strip()} {row.get('restaurant', '').strip()} 학식"
            document.category = "meals"
            document.published_at = row.get("date", "").strip()
            document.status = "active"
            document.content_hash = digest
            document.schema_version = 1
            document.raw_payload_json = payload
            document.normalized_payload_json = payload
            document.collected_at = now
            document.last_parsed_at = now
            document.parse_error = None

        # This crawl is a complete time window.  Do not delete historical rows;
        # retain them as hidden so the canonical DB remains auditable.
        for document in session.query(SourceDocument).filter(SourceDocument.dataset == "meals").all():
            if document.source_id not in seen and document.status == "active":
                document.status = "hidden"
        session.commit()
        return len(seen)
    finally:
        session.close()


def load_meals_from_db() -> pd.DataFrame:
    session = SessionLocal()
    try:
        rows: list[dict] = []
        documents = (
            session.query(SourceDocument)
            .filter(SourceDocument.dataset == "meals", SourceDocument.status == "active")
            .order_by(SourceDocument.published_at.asc(), SourceDocument.id.asc())
            .all()
        )
        for document in documents:
            try:
                payload = json.loads(document.normalized_payload_json or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        return pd.DataFrame(rows).fillna("").astype(str) if rows else pd.DataFrame()
    finally:
        session.close()


def ingest_meals(collected_df: pd.DataFrame | None = None) -> Tuple[pd.DataFrame, object, object]:
    """Build the meals index exclusively from SQLite canonical documents.

    ``collected_df`` exists only at the collection boundary: it is persisted
    first, then immediately reloaded from SQLite to prove indexing has no CSV
    dependency.  A legacy CSV is imported only when bootstrapping an empty DB.
    """
    if collected_df is not None:
        store_meals_in_db(collected_df)
    df = load_meals_from_db()
    if df.empty:
        path = DATA_SOURCES["meals"]
        if not path.exists():
            print("⚠️ Meals DB is empty and no legacy seed CSV is available")
            return pd.DataFrame(), None, None
        # One-time backward-compatible bootstrap; runtime collectors never
        # write this file and all later indexing reads the database.
        store_meals_in_db(pd.read_csv(path).fillna("").astype(str))
        df = load_meals_from_db()
    chunks_df = build_meal_chunks(df)
    if chunks_df.empty:
        print("⚠️ Warning: No meal chunks generated; preserving existing meals index")
        return chunks_df, None, None
    reset_collection(DATASET_ARTIFACTS["meals"].collection)
    return _persist_chunks("meals", DATASET_ARTIFACTS["meals"].collection, chunks_df)


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
    results["meals"] = ingest_meals()
    return results


def reindex_from_db(target: str | None = None) -> Dict[str, Tuple[pd.DataFrame, object, object]]:
    """SQLite DB에 저장된 데이터를 기반으로 ChromaDB 인덱스와 TF-IDF를 재구축합니다."""
    session = SessionLocal()
    results = {}
    
    try:
        # 1. Notices
        if not target or target == "notices":
            print("🔄 Re-indexing notices from DB...")
            df = build_notice_index_frame_from_session(session)
            if not df.empty:
                reset_collection(DATASET_ARTIFACTS["notices"].collection)
                results["notices"] = _persist_chunks("notices", DATASET_ARTIFACTS["notices"].collection, df)
        
        # 2. Rules
        if not target or target == "rules":
            print("🔄 Re-indexing rules from DB...")
            rule_rows = [
                {
                    "text": rule.full_text,
                    "filename": rule.filename,
                    "relative_dir": rule.relative_dir,
                    "db_id": rule.id,
                    "title": rule.title or rule.filename,
                    "source_type": rule.source_type or "rules_text",
                    "source_url": rule.source_url or "",
                    "source_page_url": rule.source_page_url or "",
                    "source_version": rule.source_version or "",
                    "published_at": rule.published_at or "",
                }
                for rule in session.query(Rule).order_by(Rule.id.asc()).all()
                if str(rule.full_text or "").strip()
            ]
            if rule_rows:
                # Rule.full_text가 정본이다. 기존 300자 파생 청크를 다시 이어 붙이면
                # overlap과 줄바꿈 오차가 누적되므로 정본에서 600자로 직접 재분할한다.
                df = _canonicalize_campus_scope_frame(
                    build_rule_chunks(pd.DataFrame(rule_rows))
                )
                session.query(Chunk).filter(Chunk.rule_id.isnot(None)).delete(
                    synchronize_session=False
                )
                session.bulk_insert_mappings(
                    Chunk,
                    df[
                        [
                            "chunk_id",
                            "chunk_text",
                            "doc_id",
                            "position",
                            "rule_id",
                        ]
                    ].to_dict(orient="records"),
                )
                session.commit()
                results["rules"] = _persist_replacing_collection(
                    "rules",
                    DATASET_ARTIFACTS["rules"].collection,
                    df,
                )

        # 3. Schedule
        if not target or target == "schedule":
            print("🔄 Re-indexing schedule from DB...")
            query = (
                session.query(Chunk, Schedule)
                .join(Schedule, Chunk.schedule_id == Schedule.id)
                .order_by(Schedule.id.asc(), Chunk.position.asc(), Chunk.id.asc())
            )
            data = []
            fallback_positions = {}
            for chunk, sch in query.all():
                doc_id, position = _chunk_parent_identity(
                    chunk, f"schedule:{sch.id}", fallback_positions,
                )
                data.append({
                    "chunk_id": chunk.chunk_id,
                    "chunk_text": chunk.chunk_text,
                    "doc_id": doc_id,
                    "position": position,
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
                df = _canonicalize_campus_scope_frame(pd.DataFrame(data))
                reset_collection(DATASET_ARTIFACTS["schedule"].collection)
                results["schedule"] = _persist_chunks("schedule", DATASET_ARTIFACTS["schedule"].collection, df)

        # 4. Courses
        if not target or target == "courses":
            print("🔄 Re-indexing courses from DB...")
            query = (
                session.query(Chunk, Course)
                .join(Course, Chunk.course_id == Course.id)
                .order_by(Course.id.asc(), Chunk.position.asc(), Chunk.id.asc())
            )
            data = []
            fallback_positions = {}
            for chunk, course in query.all():
                doc_id, position = _chunk_parent_identity(
                    chunk, f"course:{course.id}", fallback_positions,
                )
                try:
                    raw_data = json.loads(course.raw_data) if course.raw_data else {}
                except (json.JSONDecodeError, TypeError, ValueError):
                    raw_data = {}
                
                data.append({
                    "chunk_id": chunk.chunk_id,
                    "chunk_text": chunk.chunk_text,
                    "doc_id": doc_id,
                    "position": position,
                    "title": course.title,
                    "course_code": course.course_code,
                    "source_table": course.source_table,
                    "topics": course.source_table,
                    "source": "courses",
                    "url": "",
                    "published_at": "",
                    "course_id": course.id,
                    "major": raw_data.get("major", ""),
                    "college_name": raw_data.get("college_name", ""),
                    "credit": raw_data.get("credit_value") or raw_data.get("credit", ""),
                    "grade": raw_data.get("recommended_grades") or raw_data.get("grade", ""),
                    "semester": raw_data.get("offered_semesters") or raw_data.get("semester", ""),
                    "course_type": raw_data.get("course_type", ""),
                    "curriculum_year": raw_data.get("curriculum_year", ""),
                    "source_page": raw_data.get("source_page", ""),
                    "source_type": raw_data.get("source_type", ""),
                    "source_priority": raw_data.get("source_priority", ""),
                    "course_code_conflict": raw_data.get("course_code_conflict", False),
                    "availability_status": raw_data.get("availability_status", "curriculum_only"),
                    "data_quality_score": raw_data.get("data_quality_score", ""),
                    "collection_status": raw_data.get("collection_status", ""),
                })
            if data:
                df = _canonicalize_campus_scope_frame(pd.DataFrame(data))
                reset_collection(DATASET_ARTIFACTS["courses"].collection)
                results["courses"] = _persist_chunks("courses", DATASET_ARTIFACTS["courses"].collection, df)

        # 5. Staff (New)
        if not target or target == "staff":
            print("🔄 Re-indexing staff from DB...")
            query = (
                session.query(Chunk, Staff)
                .join(Staff, Chunk.staff_id == Staff.id)
                .order_by(Staff.id.asc(), Chunk.position.asc(), Chunk.id.asc())
            )
            data = []
            fallback_positions = {}
            for chunk, staff in query.all():
                doc_id, position = _chunk_parent_identity(
                    chunk, f"staff:{staff.id}", fallback_positions,
                )
                data.append({
                    "chunk_id": chunk.chunk_id,
                    "chunk_text": chunk.chunk_text,
                    "doc_id": doc_id,
                    "position": position,
                    "title": f"{staff.department} - {staff.name}",
                    "topics": staff.department,
                    "source": "staff",
                    "url": "",
                    "published_at": "",
                    "staff_id": staff.id
                })
            if data:
                df = _canonicalize_campus_scope_frame(pd.DataFrame(data))
                reset_collection(DATASET_ARTIFACTS["staff"].collection)
                results["staff"] = _persist_chunks("staff", DATASET_ARTIFACTS["staff"].collection, df)

        # 6. Meals — unlike the old implementation, this is now a first-class
        # SQLite-backed corpus and can be rebuilt without a CSV snapshot.
        if not target or target == "meals":
            print("🔄 Re-indexing meals from DB...")
            meals_df = load_meals_from_db()
            if not meals_df.empty:
                df = build_meal_chunks(meals_df)
                if not df.empty:
                    df = _canonicalize_campus_scope_frame(df)
                    reset_collection(DATASET_ARTIFACTS["meals"].collection)
                    results["meals"] = _persist_chunks("meals", DATASET_ARTIFACTS["meals"].collection, df)

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
        choices=["notices", "rules", "schedule", "courses", "staff", "meals"],
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
        elif args.target == "meals":
            results["meals"] = ingest_meals()
    else:
        print("🚀 Ingesting ALL datasets...")
        results = ingest_all()

    for key, (chunks_df, _, _) in results.items():
        print(f"✅ {key}: {len(chunks_df)} chunks indexed")


if __name__ == "__main__":
    main()


__all__ = [
    "DATASET_ARTIFACTS",
    "_extract_notice_apply_deadline",
    "build_notice_chunks",
    "build_rule_chunks",
    "build_schedule_chunks",
    "build_course_chunks",
    "build_staff_chunks",
    "build_meal_chunks",
    "ingest_notices",
    "ingest_rules",
    "ingest_schedule",
    "ingest_courses",
    "ingest_staff",
    "ingest_meals",
    "ingest_all",
    "SessionLocal",
    "reindex_from_db",
]
