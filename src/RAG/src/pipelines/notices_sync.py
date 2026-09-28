"""동국대학교 notices 데이터셋의 증분 수집/정규화/색인을 담당합니다."""
from __future__ import annotations

import ast
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from src.config import (
    RAG_NOTICE_DELETION_CHECK_BUDGET,
    RAG_NOTICE_DELETION_CHECK_DELAY_SECONDS,
    RAG_NOTICE_DELETION_CHECK_MAX_CONSECUTIVE_UNKNOWN,
    RAG_NOTICE_DELETION_CHECK_MAX_DELETIONS,
    RAG_NOTICE_DELETION_CHECK_MAX_FRACTION,
    RAG_NOTICE_DELETION_CHECK_MAX_SECONDS,
    RAG_NOTICE_DELETION_CHECK_MAX_UNKNOWN,
    RAG_NOTICE_DELETION_CHECK_MIN_SAMPLE,
    RAG_NOTICE_DELETION_CHECK_MODE,
    RAG_NOTICE_DELETION_CHECK_STRIKE_MAX_AGE_DAYS,
    RAG_NOTICE_DELETION_CHECK_WINDOW_MONTHS,
    RAG_NOTICES_INCREMENTAL_EMBED,
    RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS,
)
from src.crawlers.dongguk_notices import BOARD_CODES
from src.database import (
    Chunk,
    DocumentQualityCheck,
    IngestionRun,
    Notice,
    SessionLocal,
    SourceDocument,
    kst_now,
)
from src.models.embedding import encode_texts
from src.pipelines.ingest import (
    DATASET_ARTIFACTS,
    _persist_replacing_collection,
    build_notice_chunks,
    build_notice_index_frame_from_db,
    persist_dataset_artifacts_only,
    update_collection_metadata_from_frame,
)
from src.services.ingest_runtime import serialized_ingest
from src.pipelines.canonical import canonical_hash, canonical_json, source_document_key
from src.utils.notice_visibility import (
    DEPARTMENT_NOTICE_BOARDS,
    DEPARTMENT_VISIBILITY,
    PUBLIC_VISIBILITY,
    clean_department,
    extract_department_from_notice_content,
)
from src.utils.preprocess import standardize_date
from src.vectorstore.chroma_client import (
    delete_items,
    get_all_ids,
    upsert_items,
)

NOTICE_SCHEMA_VERSION = 3
NOTICE_COLLECTION = DATASET_ARTIFACTS["notices"].collection
AUTO_NOTICE_FILTER = (Notice.is_manual == 0) | (Notice.is_manual.is_(None))
NOTICE_REQUIRED_FIELDS = {
    "title": "제목이 비어 있습니다.",
    "detail_url": "상세 URL이 비어 있습니다.",
    "board_name": "게시판명이 비어 있습니다.",
    "board_code": "게시판 코드가 비어 있습니다.",
}
BOARD_NAMES_BY_CODE = {code: name for name, code in BOARD_CODES.items()}
logger = logging.getLogger(__name__)


@dataclass
class NoticeCollectResult:
    run_id: int
    changed_keys: list[str]
    hidden_keys: list[str]
    documents_seen: int
    documents_new: int
    documents_updated: int
    documents_deleted: int
    documents_failed: int
    crawl_incomplete_boards: list[str]
    seen_source_ids: list[str] = field(default_factory=list)
    missing_detection_applied: bool = False
    deletion_check: dict[str, Any] | None = None


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "t", "y", "yes", "고정", "상단고정"}


def _extract_article_id(url: str | None) -> int | None:
    if not url:
        return None
    match = re.search(r"/detail/(\d+)", str(url))
    return int(match.group(1)) if match else None


def _normalize_attachments(value: Any) -> tuple[list[dict[str, Any]], bool]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return [], False
    if isinstance(value, list):
        return value, False
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return [], False
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else [], not isinstance(parsed, list)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(text)
                return parsed if isinstance(parsed, list) else [], not isinstance(parsed, list)
            except (SyntaxError, ValueError):
                return [], True
    return [], True


def _hash_notice_content(record: dict[str, Any]) -> str:
    return canonical_hash(
        {
            "title": record["title"],
            "category": record["category"],
            "posted_at": record["published_at"],
            "content_text": record["content_text"],
            "attachments": record["attachments"],
        }
    )


def _canonical_notice_category(
    category: Any,
    board_name: Any,
    board_code: Any,
) -> tuple[str, str, str, str]:
    """Return effective/original/source/fallback category values.

    Board fallback is a coarse official board label, not an inferred detailed
    topic. Existing list/detail categories always win.
    """
    original = str(category or "").strip()
    if original:
        return original, original, "list", ""
    code = str(board_code or "").strip()
    name = str(board_name or "").strip()
    fallback = BOARD_NAMES_BY_CODE.get(code)
    if fallback is None and name in BOARD_CODES:
        fallback = name
    if fallback:
        return fallback, "", "board_fallback", fallback
    return "", "", "missing", ""


def _normalize_notice_record(row: pd.Series) -> tuple[dict[str, Any], bool]:
    detail_url = str(row.get("상세URL") or row.get("detail_url") or "").strip()
    board_name = str(row.get("게시판") or row.get("board_name") or "").strip()
    board_code = str(row.get("게시판코드") or row.get("board_code") or "").strip()
    article_id = row.get("원문글ID") or row.get("article_id") or _extract_article_id(detail_url)
    source_id = f"{board_code}:{article_id}" if board_code and article_id is not None else ""
    document_key = source_document_key("notices", source_id) if source_id else ""
    attachments, attachments_parse_failed = _normalize_attachments(row.get("첨부파일") or row.get("attachments"))

    published_at = standardize_date(row.get("게시일") or row.get("posted_at"))
    effective_category, original_category, category_source, category_fallback = _canonical_notice_category(
        row.get("카테고리") or row.get("category"),
        board_name,
        board_code,
    )
    raw_source_type = row.get("source_type")
    source_type = "" if raw_source_type is None else str(raw_source_type).strip()
    if not source_type or source_type.lower() in {"nan", "none"}:
        source_type = "html_notice"
    normalized = {
        "document_key": document_key,
        "dataset": "notices",
        "source_type": source_type,
        "source_id": source_id,
        "board_name": board_name,
        "board_code": board_code,
        "article_id": article_id,
        "title": str(row.get("제목") or row.get("title") or "").strip(),
        "category": effective_category,
        "category_original": original_category,
        "category_source": category_source,
        "category_board_fallback": category_fallback,
        "published_at": published_at or "",
        "detail_url": detail_url,
        "content_text": str(row.get("본문") or row.get("content_text") or "").strip(),
        "content_html": str(row.get("본문HTML") or row.get("content_html") or "").strip(),
        "attachments": attachments,
        "is_pinned": _coerce_bool(row.get("상단고정") or row.get("is_pinned")),
        "schema_version": NOTICE_SCHEMA_VERSION,
        "collected_at": kst_now().isoformat(),
    }
    normalized["content_hash"] = _hash_notice_content(normalized)
    return normalized, attachments_parse_failed


def _build_quality_checks(record: dict[str, Any], attachments_parse_failed: bool) -> tuple[list[dict[str, str]], str | None]:
    checks: list[dict[str, str]] = []
    parse_errors: list[str] = []

    for field, message in NOTICE_REQUIRED_FIELDS.items():
        if not str(record.get(field) or "").strip():
            checks.append({"check_type": field, "severity": "error", "message": message})
            parse_errors.append(message)

    if not record.get("published_at"):
        checks.append({"check_type": "published_at", "severity": "warning", "message": "게시일 파싱에 실패했습니다."})

    if attachments_parse_failed:
        checks.append({"check_type": "attachments", "severity": "warning", "message": "첨부파일 파싱에 실패했습니다."})

    content_text = record.get("content_text", "").strip()
    if not content_text:
        checks.append({"check_type": "content_text", "severity": "warning", "message": "본문이 비어 있어 제목과 링크만 색인합니다."})
    elif len(content_text) < 40:
        checks.append({"check_type": "content_length", "severity": "warning", "message": "본문 길이가 매우 짧습니다."})

    return checks, "\n".join(parse_errors) if parse_errors else None


def _load_normalized_notice(document: SourceDocument) -> dict[str, Any] | None:
    """Read the canonical SQLite payload, with a read-only legacy fallback.

    Sidecar JSON files existed before SQLite owned the document state.  Keeping
    this fallback lets an operator migrate an old database safely, but no new
    collection path writes or requires those files.
    """
    if document.normalized_payload_json:
        try:
            payload = json.loads(document.normalized_payload_json)
            return payload if isinstance(payload, dict) else None
        except (TypeError, json.JSONDecodeError):
            return None
    if not document.normalized_path:
        return None
    try:
        payload = json.loads(Path(document.normalized_path).read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _save_quality_checks(session, document_key: str, checks: Iterable[dict[str, str]]) -> None:
    session.query(DocumentQualityCheck).filter(DocumentQualityCheck.document_key == document_key).delete(
        synchronize_session=False
    )
    for check in checks:
        session.add(
            DocumentQualityCheck(
                document_key=document_key,
                check_type=check["check_type"],
                severity=check["severity"],
                message=check["message"],
            )
        )


def load_known_article_ids_by_board() -> dict[str, set[int]]:
    """이미 수집된 notices 원문 ID를 게시판명별로 로드합니다."""
    session = None
    try:
        session = SessionLocal()
        board_names_by_code = {code: name for name, code in BOARD_CODES.items()}
        known_ids_by_board: dict[str, set[int]] = {}
        rows = (
            session.query(SourceDocument.source_id)
            .filter(
                SourceDocument.dataset == "notices",
                SourceDocument.source_id.isnot(None),
                SourceDocument.status.in_(["active", "updated"]),
            )
            .distinct()
            .all()
        )
        for (source_id,) in rows:
            try:
                board_code, article_id_text = str(source_id).split(":", 1)
                board_name = board_names_by_code.get(board_code)
                if not board_name:
                    continue
                article_id = int(article_id_text)
            except (TypeError, ValueError):
                continue
            known_ids_by_board.setdefault(board_name, set()).add(article_id)
        return known_ids_by_board
    except Exception:
        return {}
    finally:
        if session is not None:
            session.close()


def _export_active_notices_csv(session) -> None:
    """Compatibility no-op for old maintenance callers.

    SQLite is the only canonical store; CSV exports are intentionally no
    longer produced as part of a collection/indexing transaction.
    """
    return None


def _normalized_notice_to_notice_row(normalized: dict[str, Any], *, db_id: int | None = None) -> dict[str, Any]:
    return {
        "게시판": normalized.get("board_name", ""),
        "게시판코드": normalized.get("board_code", ""),
        "원문글ID": normalized.get("article_id", ""),
        "원문ID": normalized.get("source_id", ""),
        "문서키": normalized.get("document_key", ""),
        "제목": normalized.get("title", ""),
        "카테고리": normalized.get("category", ""),
        "카테고리원본": normalized.get("category_original", normalized.get("category", "")),
        "카테고리출처": normalized.get("category_source", "list" if normalized.get("category") else "missing"),
        "카테고리게시판대체": normalized.get("category_board_fallback", ""),
        "게시일": normalized.get("published_at", ""),
        "상단고정": normalized.get("is_pinned", False),
        "상세URL": normalized.get("detail_url", ""),
        "본문": normalized.get("content_text", ""),
        "본문HTML": normalized.get("content_html", ""),
        "첨부파일": normalized.get("attachments", []),
        "source_type": normalized.get("source_type", "html_notice"),
        # 공식 수집 공지는 대상 학과 제한 없이 공개한다. 학과 전용 범위는 수동
        # 제출 경로에서만 명시적으로 부여된다.
        "department": "",
        "visibility": PUBLIC_VISIBILITY,
        "db_id": db_id,
    }


def ensure_manual_notice_source_document(
    session,
    notice: Notice,
    *,
    existing_document: SourceDocument | None = None,
    now=None,
) -> SourceDocument:
    """Upsert one manually curated notice into the canonical source layer."""
    source_id = f"manual_notice:{notice.id}"
    source_url = str(notice.detail_url or f"manual://notice/{notice.id}").strip()
    # Persist the synthetic identity on the projection too.  This keeps admin
    # updates, DB-only rebuilds, and quality checks on the same parent URL.
    notice.detail_url = source_url
    try:
        attachments = json.loads(notice.attachments or "[]")
    except (TypeError, json.JSONDecodeError):
        attachments = []
    if not isinstance(attachments, list):
        attachments = []
    payload = {
        "source_id": source_id,
        "source_type": "manual_notice",
        "board_name": str(notice.board or ""),
        "title": str(notice.title or ""),
        "category": str(notice.category or ""),
        "published_at": str(notice.published_date or ""),
        "is_pinned": str(notice.is_fixed or "").strip().lower() in {"1", "true", "y"},
        "detail_url": source_url,
        "content_text": str(notice.content or ""),
        "attachments": attachments,
        "department": clean_department(notice.department),
        "visibility": (
            DEPARTMENT_VISIBILITY
            if str(notice.visibility or "").strip() == DEPARTMENT_VISIBILITY
            and clean_department(notice.department)
            else PUBLIC_VISIBILITY
        ),
    }
    document = existing_document
    if document is None:
        document = (
            session.query(SourceDocument)
            .filter(
                SourceDocument.dataset == "notices",
                SourceDocument.source_id == source_id,
            )
            .one_or_none()
        )
    if document is None:
        document = SourceDocument(
            dataset="notices",
            source_type="manual_notice",
            source_id=source_id,
            document_key=source_document_key("notices", source_id),
        )
        session.add(document)
    timestamp = now or kst_now()
    document.source_type = "manual_notice"
    document.source_url = source_url
    document.document_key = source_document_key("notices", source_id)
    document.title = payload["title"]
    document.category = payload["category"]
    document.published_at = payload["published_at"]
    document.status = "active"
    document.content_hash = canonical_hash(payload)
    document.schema_version = NOTICE_SCHEMA_VERSION
    document.raw_payload_json = canonical_json(payload)
    document.normalized_payload_json = canonical_json(payload)
    document.collected_at = document.collected_at or timestamp
    document.last_parsed_at = timestamp
    document.parse_error = None
    # Manual chunks predate SourceDocument and often have a NULL or legacy
    # ``notice:<db_id>`` parent.  Repair them whenever this entry point runs.
    for chunk in session.query(Chunk).filter(Chunk.notice_id == notice.id).all():
        chunk.doc_id = document.document_key
    return document


def backfill_manual_notice_department_scopes(session=None) -> list[int]:
    """기존 학과 콘솔 수동 공지에 남은 ``주관: 학과`` 표기를 구조화한다.

    기존 공식 수집 공지는 건드리지 않는다. 범위를 알 수 없는 오래된 수동 공지도
    임의로 숨기지 않고 공개 상태를 유지한다.
    """
    owns_session = session is None
    if session is None:
        session = SessionLocal()

    changed_ids: list[int] = []
    try:
        notices = (
            session.query(Notice)
            .filter(Notice.is_manual == 1, Notice.board.in_(DEPARTMENT_NOTICE_BOARDS))
            .order_by(Notice.id.asc())
            .all()
        )
        for notice in notices:
            department = clean_department(notice.department) or extract_department_from_notice_content(
                notice.content
            )
            if not department:
                continue
            if (
                clean_department(notice.department) != department
                or str(notice.visibility or "").strip() != DEPARTMENT_VISIBILITY
            ):
                notice.department = department
                notice.visibility = DEPARTMENT_VISIBILITY
                changed_ids.append(notice.id)
                ensure_manual_notice_source_document(session, notice)
        if owns_session and changed_ids:
            session.commit()
        return changed_ids
    except Exception:
        if owns_session:
            session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def _ensure_manual_notice_source_documents(session) -> int:
    """Register every manually curated Notice row in the canonical source layer."""
    notices = session.query(Notice).filter(Notice.is_manual == 1).order_by(Notice.id.asc()).all()
    if not notices:
        return 0

    source_ids = [f"manual_notice:{notice.id}" for notice in notices]
    existing = {
        document.source_id: document
        for document in session.query(SourceDocument)
        .filter(
            SourceDocument.dataset == "notices",
            SourceDocument.source_id.in_(source_ids),
        )
        .all()
    }
    now = kst_now()
    for notice in notices:
        ensure_manual_notice_source_document(
            session,
            notice,
            existing_document=existing.get(f"manual_notice:{notice.id}"),
            now=now,
        )
    return len(notices)


def backfill_manual_notice_source_documents() -> int:
    """Persist canonical SourceDocument rows for manual notices."""
    session = SessionLocal()
    try:
        count = _ensure_manual_notice_source_documents(session)
        session.commit()
        return count
    finally:
        session.close()


def record_notice_ingestion_failure(error: object, *, stage: str = "crawl") -> int:
    """Persist a terminal failure that happened before collection could start.

    ``collect_notice_documents`` owns its own run once a DataFrame exists.  A
    total list-page outage happens earlier, so without this entry point the CLI
    exits non-zero but the durable ingestion history incorrectly keeps showing
    the previous success.
    """
    session = SessionLocal()
    try:
        run = IngestionRun(
            dataset="notices",
            status="failed",
            finished_at=kst_now(),
            outcome_code="upstream_unreachable",
            diagnostics_json=canonical_json(
                {"stage": stage, "error_type": type(error).__name__}
            ),
            error_summary=f"{stage}: {error}",
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return int(run.id)
    finally:
        session.close()


def _delete_notice_chunks(session, notice_ids: list[int]) -> list[str]:
    if not notice_ids:
        return []
    chunks = session.query(Chunk).filter(Chunk.notice_id.in_(notice_ids)).all()
    chunk_ids = [chunk.chunk_id for chunk in chunks if chunk.chunk_id]
    if chunk_ids:
        delete_items(NOTICE_COLLECTION, chunk_ids)
    session.query(Chunk).filter(Chunk.notice_id.in_(notice_ids)).delete(synchronize_session=False)
    return chunk_ids


def _upsert_notice_domain_rows(session, normalized_docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not normalized_docs:
        return []

    urls = [doc["detail_url"] for doc in normalized_docs if doc.get("detail_url")]
    existing_by_url = {
        notice.detail_url: notice
        for notice in session.query(Notice).filter(AUTO_NOTICE_FILTER, Notice.detail_url.in_(urls)).all()
    }

    updated_rows: list[dict[str, Any]] = []
    for normalized in normalized_docs:
        notice = existing_by_url.get(normalized["detail_url"])
        attachments_str = json.dumps(normalized.get("attachments", []), ensure_ascii=False)
        if notice is None:
            notice = Notice(
                board=normalized["board_name"],
                title=normalized["title"],
                category=normalized["category"],
                published_date=normalized["published_at"],
                is_fixed=str(normalized["is_pinned"]),
                detail_url=normalized["detail_url"],
                content=normalized["content_text"],
                attachments=attachments_str,
            )
            session.add(notice)
            session.flush()
            existing_by_url[normalized["detail_url"]] = notice
        else:
            notice.board = normalized["board_name"]
            notice.title = normalized["title"]
            notice.category = normalized["category"]
            notice.published_date = normalized["published_at"]
            notice.is_fixed = str(normalized["is_pinned"])
            notice.content = normalized["content_text"]
            notice.attachments = attachments_str

        updated_rows.append(_normalized_notice_to_notice_row(normalized, db_id=notice.id))
    return updated_rows


def _upsert_notice_chunks(session, notice_rows: list[dict[str, Any]], source_documents: dict[str, SourceDocument]) -> None:
    if not notice_rows:
        return

    notice_ids = [row["db_id"] for row in notice_rows if row.get("db_id")]
    _delete_notice_chunks(session, notice_ids)

    chunk_df = build_notice_chunks(pd.DataFrame(notice_rows))
    if chunk_df.empty:
        return

    # 문서키 기반 chunk_id는 도메인 Notice 행이 교체되어도 유지될 수 있다.
    # 새 notice_id만 지우면 이전 행을 가리키는 동일 chunk_id가 남아 UNIQUE 충돌이
    # 나므로, 삽입 직전에 청크 identity 자체로도 기존 파생 행을 정리한다.
    replacement_chunk_ids = chunk_df["chunk_id"].astype(str).tolist()
    colliding = (
        session.query(Chunk)
        .filter(Chunk.chunk_id.in_(replacement_chunk_ids))
        .all()
    )
    if colliding:
        delete_items(
            NOTICE_COLLECTION,
            [chunk.chunk_id for chunk in colliding if chunk.chunk_id],
        )
        session.query(Chunk).filter(
            Chunk.chunk_id.in_(replacement_chunk_ids)
        ).delete(synchronize_session=False)

    # Keep relational chunks and their source-document state in the same
    # SQLite transaction.  ``DataFrame.to_sql(..., con=session.bind)`` opens a
    # second connection; SQLite then reports "database is locked" while this
    # session still owns the delete transaction.
    session.bulk_insert_mappings(
        Chunk,
        chunk_df[["chunk_id", "chunk_text", "doc_id", "position", "notice_id"]].to_dict(orient="records"),
    )

    metadatas = chunk_df.drop(columns=["chunk_text"]).to_dict(orient="records")
    metadatas = [{k: (v if v is not None else "") for k, v in item.items()} for item in metadatas]
    embeddings = encode_texts(chunk_df["chunk_text"].tolist())
    upsert_items(
        NOTICE_COLLECTION,
        ids=chunk_df["chunk_id"].astype(str).tolist(),
        documents=chunk_df["chunk_text"].tolist(),
        metadatas=metadatas,
        embeddings=embeddings,
    )

    indexed_at = kst_now()
    for row in notice_rows:
        source_document = source_documents.get(row.get("원문ID"))
        if source_document is not None:
            source_document.last_indexed_at = indexed_at


def _apply_hidden_notices(session, hidden_documents: list[SourceDocument]) -> None:
    if not hidden_documents:
        return
    urls = [doc.source_url for doc in hidden_documents if doc.source_url]
    if not urls:
        return
    notices = session.query(Notice).filter(AUTO_NOTICE_FILTER, Notice.detail_url.in_(urls)).all()
    notice_ids = [notice.id for notice in notices]
    _delete_notice_chunks(session, notice_ids)
    if notice_ids:
        session.query(Notice).filter(Notice.id.in_(notice_ids)).delete(synchronize_session=False)
    indexed_at = kst_now()
    for doc in hidden_documents:
        doc.last_indexed_at = indexed_at


def collect_notice_documents(
    incoming_df: pd.DataFrame,
    *,
    allow_missing_detection: bool = False,
) -> NoticeCollectResult:
    crawl_incomplete_boards = sorted({
        str(board).strip()
        for board in incoming_df.attrs.get("crawl_incomplete_boards", [])
        if str(board).strip()
    })
    board_diagnostics = []
    for item in incoming_df.attrs.get("crawl_diagnostics", []):
        if not isinstance(item, dict):
            continue
        board_diagnostics.append(
            {
                "board_name": str(item.get("board_name") or item.get("board_code") or "unknown"),
                "status": str(item.get("status") or "unknown"),
                "list_pages_succeeded": int(item.get("list_pages_succeeded") or 0),
                "list_pages_failed": int(item.get("list_pages_failed") or 0),
                "detail_failure_count": len(item.get("detail_failures") or []),
                "records_collected": int(item.get("records_collected") or 0),
            }
        )
    retry_diagnostics = incoming_df.attrs.get("crawl_retry")
    # Missing detection is only safe after a complete crawl.  If one board is
    # unavailable or truncated, treating its unseen rows as deletions would
    # hide valid notices because of a transient upstream outage.
    effective_missing_detection = allow_missing_detection and not crawl_incomplete_boards
    session = SessionLocal()
    run = IngestionRun(dataset="notices", status="running")
    session.add(run)
    session.commit()
    session.refresh(run)
    run_id = int(run.id)

    documents_seen = 0
    documents_new = 0
    documents_updated = 0
    documents_deleted = 0
    documents_failed = 0
    changed_keys: list[str] = []
    hidden_keys: list[str] = []

    try:
        existing_docs = {
            doc.source_id: doc
            for doc in session.query(SourceDocument).filter(SourceDocument.dataset == "notices").all()
        }
        seen_source_ids: set[str] = set()

        for _, row in incoming_df.iterrows():
            normalized, attachments_parse_failed = _normalize_notice_record(row)
            if not normalized["source_id"] or not normalized["document_key"]:
                continue

            documents_seen += 1
            seen_source_ids.add(normalized["source_id"])

            raw_payload = {
                "schema_version": NOTICE_SCHEMA_VERSION,
                "dataset": "notices",
                "source_id": normalized["source_id"],
                "collected_at": normalized["collected_at"],
                "raw_record": row.to_dict(),
            }
            checks, parse_error = _build_quality_checks(normalized, attachments_parse_failed)
            _save_quality_checks(session, normalized["document_key"], checks)

            existing = existing_docs.get(normalized["source_id"])
            status = "active"
            should_index = False

            if parse_error:
                status = "parse_failed"
                documents_failed += 1
            elif existing is None:
                documents_new += 1
                should_index = True
            elif (
                existing.content_hash != normalized["content_hash"]
                or existing.source_url != normalized["detail_url"]
                or existing.source_type != normalized["source_type"]
                or existing.status in {"hidden", "deleted", "parse_failed"}
            ):
                status = "updated"
                documents_updated += 1
                should_index = True

            if existing is None:
                existing = SourceDocument(
                    dataset="notices",
                    source_type=normalized["source_type"],
                    source_id=normalized["source_id"],
                    document_key=normalized["document_key"],
                )
                session.add(existing)
                existing_docs[normalized["source_id"]] = existing

            existing.source_type = normalized["source_type"]
            existing.source_url = normalized["detail_url"]
            existing.title = normalized["title"]
            existing.category = normalized["category"]
            existing.published_at = normalized["published_at"]
            existing.status = status
            existing.content_hash = normalized["content_hash"]
            existing.schema_version = NOTICE_SCHEMA_VERSION
            existing.raw_payload_json = canonical_json(raw_payload)
            existing.normalized_payload_json = canonical_json(normalized)
            existing.collected_at = kst_now()
            existing.last_parsed_at = kst_now()
            existing.parse_error = parse_error
            existing.miss_count = 0

            if should_index and status != "parse_failed":
                changed_keys.append(normalized["document_key"])

        if effective_missing_detection:
            visible_statuses = ["active", "updated"]
            candidates = (
                session.query(SourceDocument)
                .filter(
                    SourceDocument.dataset == "notices",
                    SourceDocument.status.in_(visible_statuses),
                )
                .all()
            )
            for doc in candidates:
                if doc.source_id in seen_source_ids:
                    continue
                doc.miss_count = (doc.miss_count or 0) + 1
                normalized = _load_normalized_notice(doc)
                is_pinned = bool(normalized.get("is_pinned")) if normalized else False
                if is_pinned and doc.miss_count < 2:
                    continue
                if doc.status != "hidden":
                    doc.status = "hidden"
                    hidden_keys.append(doc.document_key)
                    documents_deleted += 1

        if documents_seen == 0:
            run.status = "success"
        elif documents_failed >= documents_seen:
            run.status = "failed"
        elif documents_failed > 0:
            run.status = "partial_success"
        else:
            run.status = "success"
        if crawl_incomplete_boards and run.status == "success":
            run.status = "partial_success"
        if documents_failed > 0:
            run.outcome_code = "parse_failure"
        elif crawl_incomplete_boards:
            run.outcome_code = "partial_boards"
        else:
            run.outcome_code = "success"
        from src.services.source_schema import (
            fingerprint_dataframe,
            observe_source_structures,
        )

        source_schema = observe_source_structures(
            session,
            dataset="notices",
            ingestion_run_id=run_id,
            structures=[
                fingerprint_dataframe(
                    incoming_df,
                    source_name="notice_boards",
                    source_format="html_projection",
                )
            ],
        )
        run.diagnostics_json = canonical_json(
            {
                "boards": board_diagnostics,
                "retry": retry_diagnostics if isinstance(retry_diagnostics, dict) else None,
                "incomplete_boards": crawl_incomplete_boards,
                "source_schema": source_schema,
            }
        )
        if crawl_incomplete_boards:
            run.error_summary = (
                "incomplete notice boards; missing detection disabled: "
                + ", ".join(crawl_incomplete_boards)
            )
        run.documents_seen = documents_seen
        run.documents_new = documents_new
        run.documents_updated = documents_updated
        run.documents_deleted = documents_deleted
        run.documents_failed = documents_failed
        run.finished_at = kst_now()
        session.commit()

        return NoticeCollectResult(
            run_id=run_id,
            changed_keys=changed_keys,
            hidden_keys=hidden_keys,
            documents_seen=documents_seen,
            documents_new=documents_new,
            documents_updated=documents_updated,
            documents_deleted=documents_deleted,
            documents_failed=documents_failed,
            crawl_incomplete_boards=crawl_incomplete_boards,
            seen_source_ids=sorted(seen_source_ids),
            missing_detection_applied=effective_missing_detection,
        )
    except Exception as exc:
        session.rollback()
        run.status = "failed"
        run.finished_at = kst_now()
        run.error_summary = str(exc)
        session.add(run)
        session.commit()
        raise
    finally:
        session.close()


def apply_notice_normalized_documents(
    *,
    document_keys: Iterable[str] | None = None,
    apply_index: bool = False,
) -> None:
    session = SessionLocal()
    try:
        query = session.query(SourceDocument).filter(SourceDocument.dataset == "notices")
        if document_keys is not None:
            keys = list(document_keys)
            if not keys:
                return
            query = query.filter(SourceDocument.document_key.in_(keys))

        documents = query.all()
        source_docs_by_source_id = {doc.source_id: doc for doc in documents if doc.source_id}
        managed_documents = [doc for doc in documents if doc.source_type != "manual_notice"]
        active_docs = [doc for doc in managed_documents if doc.status in {"active", "updated"}]
        hidden_docs = [doc for doc in managed_documents if doc.status in {"hidden", "deleted"}]

        normalized_rows: list[dict[str, Any]] = []
        for doc in active_docs:
            normalized = _load_normalized_notice(doc)
            if not normalized:
                doc.status = "parse_failed"
                doc.parse_error = "normalized JSON을 읽지 못했습니다."
                continue
            # Lazy, transaction-safe migration for a legacy document reached by
            # normal maintenance.  Subsequent reads no longer touch its file.
            if not doc.normalized_payload_json:
                doc.normalized_payload_json = canonical_json(normalized)
            normalized_rows.append(normalized)

        notice_rows = _upsert_notice_domain_rows(session, normalized_rows)
        _apply_hidden_notices(session, hidden_docs)

        if apply_index:
            _upsert_notice_chunks(session, notice_rows, source_docs_by_source_id)

        session.commit()
    finally:
        session.close()


def refresh_notice_artifacts() -> None:
    """DB의 notice chunks를 기준으로 parquet, TF-IDF, (필요 시) Chroma를 재생성합니다.

    Chroma 밀집 벡터는 _upsert_notice_chunks/_delete_notice_chunks가 변경분만 증분
    upsert/삭제하므로, 매 갱신마다 전량 재임베딩할 필요가 없다. 따라서 기본적으로는
    parquet/TF-IDF만 전체 재생성하고 Chroma는 손대지 않는다(전역 통계인 TF-IDF는
    임베딩 비용이 없으므로 전체 재생성이 저렴하다).

    단, Chroma 청크 수가 DB 청크 수와 어긋나면(중단된 빌드·과거 누락 등) 증분 유지가
    깨진 것이므로 안전하게 1회 전량 재임베딩으로 자가복구한다.
    RAG_NOTICES_INCREMENTAL_EMBED=0이면 종전대로 항상 전량 재임베딩한다.
    """
    frame = build_notice_index_frame_from_db()
    if frame.empty:
        raise RuntimeError("Canonical notices frame is empty; preserving the existing index.")

    aligned = False
    if RAG_NOTICES_INCREMENTAL_EMBED:
        try:
            live_ids = set(get_all_ids(NOTICE_COLLECTION))
            frame_ids = set(frame["chunk_id"].astype(str))
            aligned = live_ids == frame_ids
        except Exception:
            aligned = False

    if aligned:
        # Chroma는 이미 증분 유지됨 → 임베딩 없이 parquet/TF-IDF만 전체 재생성.
        stamped_frame, _, _ = persist_dataset_artifacts_only("notices", frame)
        update_collection_metadata_from_frame("notices", stamped_frame)
    else:
        # 토글 OFF 또는 Chroma 불일치(자가복구): 기존 벡터를 보존한 채
        # 새 corpus를 올리고 검증한 뒤 stale ID만 제거한다.
        _persist_replacing_collection("notices", NOTICE_COLLECTION, frame)


def rebuild_notices_from_source_documents() -> tuple[pd.DataFrame, object, object]:
    """Rebuild notices from SQLite canonical payloads only.

    This is the recovery/migration entry point for an interrupted or legacy
    notice index.  It deliberately never reads CSV or writes JSON snapshots.
    """
    session = SessionLocal()
    try:
        _ensure_manual_notice_source_documents(session)
        documents = (
            session.query(SourceDocument)
            .filter(SourceDocument.dataset == "notices")
            .order_by(SourceDocument.id.asc())
            .all()
        )
        managed_documents = [doc for doc in documents if doc.source_type != "manual_notice"]
        active_docs = [doc for doc in managed_documents if doc.status in {"active", "updated"}]
        hidden_docs = [doc for doc in managed_documents if doc.status in {"hidden", "deleted"}]
        normalized = [_load_normalized_notice(doc) for doc in active_docs]
        normalized_rows = [row for row in normalized if row is not None]
        if not normalized_rows:
            return pd.DataFrame(), None, None

        notice_rows = _upsert_notice_domain_rows(session, normalized_rows)
        _apply_hidden_notices(session, hidden_docs)
        auto_notice_ids = [
            notice_id
            for (notice_id,) in session.query(Notice.id).filter(AUTO_NOTICE_FILTER).all()
        ]
        _delete_notice_chunks(session, auto_notice_ids)
        chunks_df = build_notice_chunks(pd.DataFrame(notice_rows))
        if not chunks_df.empty:
            session.bulk_insert_mappings(
                Chunk,
                chunks_df[["chunk_id", "chunk_text", "doc_id", "position", "notice_id"]].to_dict(orient="records"),
            )
        indexed_at = kst_now()
        for doc in active_docs:
            doc.last_indexed_at = indexed_at
        session.commit()
    finally:
        session.close()

    # Include custom knowledge chunks in the normal notices corpus, exactly as
    # the regular artifact refresh path does.
    frame = build_notice_index_frame_from_db()
    if frame.empty:
        return frame, None, None
    return _persist_replacing_collection("notices", NOTICE_COLLECTION, frame)


# ===== 정기 증분 수집의 삭제 감지 =====
# 증분 수집은 게시판 앞쪽 페이지만 읽으므로 사이트에서 지워진 글(상세 URL이 목록
# 페이지로 302)이 활성으로 남는다. 아래 확인은 증분 목록에 보이지 않은 최근 활성
# 공지의 상세 URL을 제한된 예산(요청 수·벽시계 시간) 안에서 직접 확인한다.
#
# 삭제 확정은 기존 경로를 그대로 쓴다: SourceDocument.status를 "deleted"로 바꾸고
# 그 document_key를 sync_notices의 target_keys에 넣으면
# apply_notice_normalized_documents → _apply_hidden_notices가 Notice 행과 SQLite
# 청크·Chroma 벡터를 지우고, refresh_notice_artifacts가 parquet/TF-IDF를 재생성하며,
# _finalize_notice_derivatives가 corpus_revision과 파생 DAG를 갱신한다.
#
# 누적 표식(probe strike)은 스키마 변경 없이 IngestionRun 진단에 둔다:
# ``diagnostics_json["deletion_check"]["probe_strike_ledger"]``는
# ``{document_id: {"run_id", "at", "document_key", "source_url"}}``이며 매 실행이
# 직전 원장을 이어받아 갱신한다. 전체 수집 경로의 SourceDocument.miss_count는
# 읽지도 쓰지도 않는다(고정글 유예 등 기존 의미 유지). 삭제는 직전 원장에 같은
# 모드의 "상세 확인 missing" 표식이 있고(최대 경과일 이내) 이번 확인도 missing일
# 때만, 즉 서로 다른 두 실행의 상세 확인이 모두 missing일 때만 확정된다.
NOTICE_DELETION_MODES = frozenset({"off", "dry_run", "enforce"})
NOTICE_DELETION_DIAGNOSTICS_KEY = "deletion_check"
NOTICE_DELETION_LEDGER_KEY = "probe_strike_ledger"
_NOTICE_VISIBLE_STATUSES = ("active", "updated")
_DIAGNOSTICS_LOOKBACK_RUNS = 50

NoticeProbe = Callable[[str], tuple[str, "int | None"]]


@dataclass(frozen=True)
class NoticeDeletionCheckSettings:
    mode: str = "off"
    window_months: int = 6
    budget: int = 60
    delay_seconds: float = 0.5
    max_consecutive_unknown: int = 5
    max_total_unknown: int = 10
    max_seconds: float = 120.0
    max_deletions: int = 20
    max_fraction: float = 0.2
    min_sample: int = 20
    strike_max_age_days: float = 7.0
    request_timeout: float = 15.0

    @classmethod
    def from_config(cls) -> "NoticeDeletionCheckSettings":
        return cls(
            mode=RAG_NOTICE_DELETION_CHECK_MODE,
            window_months=RAG_NOTICE_DELETION_CHECK_WINDOW_MONTHS,
            budget=RAG_NOTICE_DELETION_CHECK_BUDGET,
            delay_seconds=RAG_NOTICE_DELETION_CHECK_DELAY_SECONDS,
            max_consecutive_unknown=RAG_NOTICE_DELETION_CHECK_MAX_CONSECUTIVE_UNKNOWN,
            max_total_unknown=RAG_NOTICE_DELETION_CHECK_MAX_UNKNOWN,
            max_seconds=RAG_NOTICE_DELETION_CHECK_MAX_SECONDS,
            max_deletions=RAG_NOTICE_DELETION_CHECK_MAX_DELETIONS,
            max_fraction=RAG_NOTICE_DELETION_CHECK_MAX_FRACTION,
            min_sample=RAG_NOTICE_DELETION_CHECK_MIN_SAMPLE,
            strike_max_age_days=RAG_NOTICE_DELETION_CHECK_STRIKE_MAX_AGE_DAYS,
            request_timeout=RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS,
        )


@dataclass(frozen=True)
class _DeletionProbeTarget:
    document_id: int
    document_key: str
    source_url: str
    pending: bool


def _recent_deletion_diagnostics(session, run_id: int) -> list[dict[str, Any]]:
    """현재 실행 이전 notices 실행들의 deletion_check 진단(최신순)."""
    runs = (
        session.query(IngestionRun)
        .filter(
            IngestionRun.dataset == "notices",
            IngestionRun.id < run_id,
            IngestionRun.diagnostics_json.isnot(None),
        )
        .order_by(IngestionRun.id.desc())
        .limit(_DIAGNOSTICS_LOOKBACK_RUNS)
        .all()
    )
    found: list[dict[str, Any]] = []
    for run in runs:
        try:
            decoded = json.loads(run.diagnostics_json or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        info = decoded.get(NOTICE_DELETION_DIAGNOSTICS_KEY) if isinstance(decoded, dict) else None
        if isinstance(info, dict):
            found.append(info)
    return found


def _load_deletion_cursor(history: list[dict[str, Any]]) -> int:
    for info in history:
        if isinstance(info.get("cursor_document_id"), int):
            return int(info["cursor_document_id"])
    return 0


def _parse_ledger_time(value: Any):
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=None)


def _load_probe_strike_ledger(
    history: list[dict[str, Any]],
    *,
    mode: str,
    max_age_days: float,
) -> dict[int, dict[str, Any]]:
    """같은 모드의 가장 최근 원장을 읽고 오래된 표식은 버린다."""
    for info in history:
        if info.get("mode") != mode or not isinstance(info.get(NOTICE_DELETION_LEDGER_KEY), dict):
            continue
        now = kst_now().replace(tzinfo=None)
        ledger: dict[int, dict[str, Any]] = {}
        for raw_id, entry in info[NOTICE_DELETION_LEDGER_KEY].items():
            if not isinstance(entry, dict):
                continue
            try:
                document_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            struck_at = _parse_ledger_time(entry.get("at"))
            if struck_at is None or (now - struck_at) > timedelta(days=max(max_age_days, 0)):
                continue
            ledger[document_id] = dict(entry)
        return ledger
    return {}


def _plan_deletion_probes(
    session,
    *,
    seen_source_ids: set[str],
    ledger: dict[int, dict[str, Any]],
    cursor: int,
    settings: NoticeDeletionCheckSettings,
) -> tuple[list[_DeletionProbeTarget], dict[int, _DeletionProbeTarget]]:
    """확인 대상을 고른다.

    대상: 게시일이 창(window_months × 30일) 안이고, 이번 증분 목록에 보이지 않은
    활성/updated 공식 공지(수동 공지 제외, URL과 source_id 일치). 반환:
    (예산만큼 자른 확인 계획, 전체 후보 {document_id: target}).

    순서: 직전 원장에 probe strike가 있는 글(확정 대기)을 먼저 확인하고, 남은 예산은
    나머지 후보를 document id 오름차순으로 직전 커서 다음부터 순환하며 쓴다.
    확정 대기 글이 예산보다 많으면 그 실행은 순환 없이 확정 대기만 확인하며, 커서는
    그대로 유지된다. 따라서 대기 글이 줄어들면 순환은 멈췄던 위치에서 이어진다.
    """
    from src.crawlers.dongguk_notices import parse_official_notice_detail_url

    cutoff = (kst_now().date() - timedelta(days=30 * max(settings.window_months, 0))).isoformat()
    documents = (
        session.query(SourceDocument)
        .filter(
            SourceDocument.dataset == "notices",
            SourceDocument.status.in_(_NOTICE_VISIBLE_STATUSES),
            SourceDocument.source_type != "manual_notice",
            SourceDocument.published_at >= cutoff,
        )
        .order_by(SourceDocument.id.asc())
        .all()
    )
    candidates: dict[int, _DeletionProbeTarget] = {}
    for doc in documents:
        if doc.source_id in seen_source_ids:
            continue
        parsed = parse_official_notice_detail_url(doc.source_url)
        # URL과 원문 식별자가 어긋난 행은 어떤 글을 확인하는지 확신할 수 없다.
        if parsed is None or f"{parsed[0]}:{parsed[1]}" != doc.source_id:
            continue
        entry = ledger.get(int(doc.id))
        pending = bool(entry) and entry.get("source_url") == doc.source_url
        candidates[int(doc.id)] = _DeletionProbeTarget(
            document_id=int(doc.id),
            document_key=str(doc.document_key),
            source_url=str(doc.source_url),
            pending=pending,
        )

    pending = [item for item in candidates.values() if item.pending]
    rotation = [item for item in candidates.values() if not item.pending]
    rotation = [item for item in rotation if item.document_id > cursor] + [
        item for item in rotation if item.document_id <= cursor
    ]
    budget = max(settings.budget, 0)
    return (pending + rotation)[:budget], candidates


def _default_notice_probe(settings: NoticeDeletionCheckSettings):
    import requests

    from src.crawlers.dongguk_notices import probe_notice_detail

    http = requests.Session()

    def probe(url: str):
        return probe_notice_detail(url, session=http, timeout=settings.request_timeout)

    return probe, http.close


def _effective_min_sample(settings: NoticeDeletionCheckSettings) -> int:
    """비율 상한이 항상 평가되도록 최소 표본을 예산 이하로 제한한다."""
    return max(1, min(settings.min_sample, max(settings.budget, 1)))


def _deletion_cap_exceeded(confirmed: int, checked: int, settings: NoticeDeletionCheckSettings) -> bool:
    if confirmed <= 0:
        return False
    if confirmed > max(settings.max_deletions, 0):
        return True
    return (
        checked >= _effective_min_sample(settings)
        and confirmed / max(checked, 1) > settings.max_fraction
    )


def _stage_deletion_check_record(
    session,
    run_id: int,
    summary: dict[str, Any],
    *,
    deleted: int,
    capped: bool,
) -> None:
    """진단·원장·삭제 수를 주어진 세션에 올린다(commit은 호출자 책임).

    확정 삭제 상태 변경과 같은 트랜잭션으로 commit해야, 기록 실패 시 상태만 바뀌고
    색인 반영 대상(hidden_keys)에서 빠지는 불일치가 생기지 않는다.
    """
    run = session.get(IngestionRun, run_id)
    if run is None:
        return
    diagnostics: dict[str, Any] = {}
    try:
        decoded = json.loads(run.diagnostics_json or "{}")
        if isinstance(decoded, dict):
            diagnostics.update(decoded)
    except (TypeError, json.JSONDecodeError):
        pass
    diagnostics[NOTICE_DELETION_DIAGNOSTICS_KEY] = summary
    run.diagnostics_json = canonical_json(diagnostics)
    run.documents_deleted = int(run.documents_deleted or 0) + deleted
    if capped:
        if run.status == "success":
            run.status = "partial_success"
        run.error_summary = (
            (run.error_summary + "; ") if run.error_summary else ""
        ) + "notice deletion check capped; no deletions applied"


def _record_deletion_check(run_id: int, summary: dict[str, Any], *, deleted: int, capped: bool) -> None:
    session = SessionLocal()
    try:
        _stage_deletion_check_record(session, run_id, summary, deleted=deleted, capped=capped)
        session.commit()
    finally:
        session.close()


def run_notice_deletion_check(
    collect_result: NoticeCollectResult,
    *,
    settings: NoticeDeletionCheckSettings | None = None,
    probe: NoticeProbe | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[str], dict[str, Any]]:
    """증분 목록에 없던 최근 활성 공지를 확인하고, 서로 다른 두 실행의 상세 확인에서
    모두 사라진 글만 deleted로 바꾼다.

    반환: (deleted로 바뀐 document_key 목록, 진단 요약). 진단은 해당 IngestionRun의
    ``diagnostics_json["deletion_check"]``에도 저장되며, 다음 실행의 순환 커서
    (``cursor_document_id``: 이번에 순환 확인한 마지막 document id, 순환 확인이 없으면
    이전 값 유지)와 probe strike 원장을 함께 담는다.

    ``dry_run``은 SourceDocument를 바꾸지 않고 자기 원장(dry_run 모드 전용)만 이어
    가므로 would_delete가 enforce와 같은 규칙으로 계산된다. enforce는 enforce 원장만
    읽는다.
    """
    settings = settings or NoticeDeletionCheckSettings.from_config()
    mode = settings.mode if settings.mode in NOTICE_DELETION_MODES else "off"
    summary: dict[str, Any] = {
        "mode": mode,
        "status": "skipped",
        "skip_reason": None,
        "window_months": settings.window_months,
        "budget": settings.budget,
        "candidates": 0,
        "checked": 0,
        "present": 0,
        "missing_strike_1": 0,
        "confirmed_deleted": 0,
        "would_delete": 0,
        "unknown": 0,
        "strikes_cleared": 0,
        "state_changed_during_check": 0,
        "aborted_reason": None,
        "capped": False,
        "deleted_document_keys": [],
    }

    skip_reason = None
    if settings.mode not in NOTICE_DELETION_MODES:
        skip_reason = "invalid_mode"
        logger.warning(
            "[notices] 알 수 없는 삭제 감지 모드 %r — off로 처리합니다 (off|dry_run|enforce)",
            settings.mode,
        )
    elif mode == "off":
        skip_reason = "disabled"
    elif collect_result.crawl_incomplete_boards:
        # 목록 수집이 불완전한 실행은 사이트가 불안정하다는 신호다.
        skip_reason = "incomplete_boards"
    elif collect_result.missing_detection_applied:
        # 전체 목록 대조가 이미 수행된 실행이다.
        skip_reason = "full_missing_detection"
    elif settings.budget <= 0:
        skip_reason = "zero_budget"
    if skip_reason is not None:
        summary["skip_reason"] = skip_reason
        if mode != "off" or skip_reason == "invalid_mode":
            _record_deletion_check(collect_result.run_id, summary, deleted=0, capped=False)
        return [], summary

    # 1) 후보 선정: 짧은 읽기 트랜잭션. 네트워크 확인 중에는 DB 세션을 잡지 않는다.
    seen_source_ids = set(collect_result.seen_source_ids)
    session = SessionLocal()
    try:
        history = _recent_deletion_diagnostics(session, collect_result.run_id)
        previous_cursor = _load_deletion_cursor(history)
        previous_ledger = _load_probe_strike_ledger(
            history,
            mode=mode,
            max_age_days=settings.strike_max_age_days,
        )
        plan, candidates = _plan_deletion_probes(
            session,
            seen_source_ids=seen_source_ids,
            ledger=previous_ledger,
            cursor=previous_cursor,
            settings=settings,
        )
    finally:
        session.close()
    summary["candidates"] = len(candidates)
    summary["pending_strikes"] = sum(1 for item in candidates.values() if item.pending)

    # 2) 상세 URL 확인(요청 수·벽시계 시간·연속/누적 unknown 상한).
    close_probe = None
    if probe is None:
        probe, close_probe = _default_notice_probe(settings)
    outcomes: list[tuple[_DeletionProbeTarget, str, int | None]] = []
    consecutive_unknown = 0
    total_unknown = 0
    started = clock()
    try:
        for index, target in enumerate(plan):
            if settings.max_seconds > 0 and clock() - started >= settings.max_seconds:
                summary["aborted_reason"] = "time_budget"
                break
            if index and settings.delay_seconds > 0:
                sleep(settings.delay_seconds)
            try:
                outcome, http_status = probe(target.source_url)
            except Exception:  # noqa: BLE001 - 확인 실패는 unknown이다.
                outcome, http_status = "unknown", None
            if outcome not in {"present", "missing"}:
                outcome = "unknown"
            outcomes.append((target, outcome, http_status))
            if outcome == "unknown":
                consecutive_unknown += 1
                total_unknown += 1
                if (
                    settings.max_consecutive_unknown > 0
                    and consecutive_unknown >= settings.max_consecutive_unknown
                ):
                    summary["aborted_reason"] = "consecutive_unknown"
                    break
                if settings.max_total_unknown > 0 and total_unknown >= settings.max_total_unknown:
                    summary["aborted_reason"] = "total_unknown"
                    break
            else:
                consecutive_unknown = 0
    finally:
        if close_probe is not None:
            close_probe()
    summary["elapsed_seconds"] = round(max(clock() - started, 0.0), 3)
    rotation_checked = [target.document_id for target, _, _ in outcomes if not target.pending]
    summary["cursor_document_id"] = rotation_checked[-1] if rotation_checked else previous_cursor
    summary["checked"] = len(outcomes)
    summary["http_statuses"] = {}
    for _, _, http_status in outcomes:
        key = str(http_status) if http_status is not None else "error"
        summary["http_statuses"][key] = summary["http_statuses"].get(key, 0) + 1

    # 3) 결과 반영: 확인 직전 상태와 달라진 행(동시 수정)은 건드리지 않는다.
    deleted_keys: list[str] = []
    struck_at = kst_now().replace(tzinfo=None).isoformat()
    session = SessionLocal()
    try:
        present_ids: list[int] = []
        strike_targets: list[_DeletionProbeTarget] = []
        confirm_docs: list[SourceDocument] = []
        for target, outcome, _ in outcomes:
            if outcome == "unknown":
                summary["unknown"] += 1
                continue
            doc = session.get(SourceDocument, target.document_id)
            if (
                doc is None
                or doc.status not in _NOTICE_VISIBLE_STATUSES
                or doc.source_url != target.source_url
            ):
                summary["state_changed_during_check"] += 1
                continue
            if outcome == "present":
                summary["present"] += 1
                present_ids.append(target.document_id)
            elif target.pending:
                confirm_docs.append(doc)
            else:
                summary["missing_strike_1"] += 1
                strike_targets.append(target)

        summary["would_delete"] = len(confirm_docs)
        capped = _deletion_cap_exceeded(len(confirm_docs), len(outcomes), settings)
        summary["capped"] = capped
        summary["strikes_cleared"] = sum(1 for doc_id in present_ids if doc_id in previous_ledger)

        # 다음 원장: 이전 표식 중 여전히 후보(보이지 않은 활성 글)인 것만 이어받고,
        # 200이면 해제, 확정 삭제되면 제거. 상한 초과 실행은 새 표식을 추가하지 않는다.
        ledger: dict[int, dict[str, Any]] = {
            doc_id: entry
            for doc_id, entry in previous_ledger.items()
            if doc_id in candidates and candidates[doc_id].pending and doc_id not in present_ids
        }
        if not capped:
            for target in strike_targets:
                ledger[target.document_id] = {
                    "run_id": collect_result.run_id,
                    "at": struck_at,
                    "document_key": target.document_key,
                    "source_url": target.source_url,
                }
            for doc in confirm_docs:
                if mode == "enforce":
                    doc.status = "deleted"
                    deleted_keys.append(str(doc.document_key))
                ledger.pop(int(doc.id), None)
        summary[NOTICE_DELETION_LEDGER_KEY] = {str(doc_id): entry for doc_id, entry in sorted(ledger.items())}
        summary["confirmed_deleted"] = len(deleted_keys)
        summary["deleted_document_keys"] = list(deleted_keys)
        summary["status"] = "capped" if summary["capped"] else "completed"
        # 상태 변경(enforce)과 진단·원장을 한 트랜잭션으로 commit한다. dry_run은
        # SourceDocument를 바꾸지 않으므로 진단만 기록된다. 기록이 실패하면 전체가
        # rollback되어 deleted로 바뀐 행도 남지 않는다.
        _stage_deletion_check_record(
            session,
            collect_result.run_id,
            summary,
            deleted=len(deleted_keys),
            capped=bool(summary["capped"]) and mode == "enforce",
        )
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
    finally:
        session.close()

    if summary["capped"]:
        logger.warning(
            "[notices] 삭제 감지 안전 상한 초과 — 적용 안 함 mode=%s checked=%s would_delete=%s",
            mode,
            summary["checked"],
            summary["would_delete"],
        )
    return deleted_keys, summary


def _notice_collect_summary(result: NoticeCollectResult) -> dict[str, int]:
    summary = {
        "run_id": result.run_id,
        "seen": result.documents_seen,
        "new": result.documents_new,
        "updated": result.documents_updated,
        "deleted": result.documents_deleted,
        "failed": result.documents_failed,
        "incomplete_boards": len(result.crawl_incomplete_boards),
    }
    check = result.deletion_check
    if isinstance(check, dict):
        summary["deletion_checked"] = int(check.get("checked") or 0)
        summary["deletion_strike_1"] = int(check.get("missing_strike_1") or 0)
        summary["deletion_confirmed"] = int(check.get("confirmed_deleted") or 0)
        summary["deletion_unknown"] = int(check.get("unknown") or 0)
        summary["deletion_capped"] = int(bool(check.get("capped")))
        summary["deletion_enforce"] = int(check.get("mode") == "enforce")
    return summary


def _apply_scheduled_deletion_check(
    collect_result: NoticeCollectResult,
    *,
    settings: NoticeDeletionCheckSettings | None,
    probe: NoticeProbe | None,
    sleep: Callable[[float], None],
) -> None:
    """삭제 감지 실패가 정상 증분 반영을 막지 않도록 격리한다."""
    try:
        deleted_keys, check = run_notice_deletion_check(
            collect_result,
            settings=settings,
            probe=probe,
            sleep=sleep,
        )
    except Exception as exc:  # noqa: BLE001 - 삭제 감지는 보조 단계다.
        logger.error("[notices] 삭제 감지 실패: %s", exc, exc_info=True)
        check = {"status": "error", "error_type": type(exc).__name__}
        deleted_keys = []
        try:
            _record_deletion_check(collect_result.run_id, check, deleted=0, capped=False)
        except Exception:  # noqa: BLE001
            pass
    collect_result.deletion_check = check
    if deleted_keys:
        collect_result.hidden_keys = list(dict.fromkeys(collect_result.hidden_keys + deleted_keys))
        collect_result.documents_deleted += len(deleted_keys)


def _finalize_notice_derivatives(result: NoticeCollectResult) -> None:
    """Close the notice run only after artifact lineage and ontology stages."""
    from src.services.corpus_revision import frame_corpus_revision
    from src.services.derivative_dag import run_post_ingestion_dag

    artifact = DATASET_ARTIFACTS["notices"]
    frame = pd.read_parquet(artifact.chunk_path, columns=["corpus_revision"])
    revision = frame_corpus_revision(frame)
    dag = run_post_ingestion_dag("notices", ingestion_run_id=result.run_id)
    session = SessionLocal()
    try:
        run = session.get(IngestionRun, result.run_id)
        if run is None:
            return
        diagnostics: dict[str, Any] = {}
        try:
            decoded = json.loads(run.diagnostics_json or "{}")
            if isinstance(decoded, dict):
                diagnostics.update(decoded)
        except (TypeError, json.JSONDecodeError):
            pass
        diagnostics["derivative_dag"] = dag
        run.diagnostics_json = canonical_json(diagnostics)
        run.corpus_revision = revision
        run.finished_at = kst_now()
        if dag.get("status") == "failed":
            if run.status == "success":
                run.status = "partial_success"
            run.outcome_code = "derivative_failure"
            run.error_summary = (
                (run.error_summary + "; ") if run.error_summary else ""
            ) + f"derivative DAG failed at {dag.get('failed_stage') or 'unknown'}"
        session.commit()
    finally:
        session.close()


def _mark_notice_pipeline_failure(run_id: int, exc: Exception) -> None:
    session = SessionLocal()
    try:
        run = session.get(IngestionRun, run_id)
        if run is None:
            return
        run.status = "failed"
        run.outcome_code = "pipeline_failure"
        run.error_summary = f"{type(exc).__name__}: {exc}"
        run.finished_at = kst_now()
        session.commit()
    finally:
        session.close()


@serialized_ingest("notices")
def sync_notices(
    incoming_df: pd.DataFrame,
    *,
    allow_missing_detection: bool = False,
    mode: str = "full-sync",
    deletion_check: bool = False,
    deletion_settings: NoticeDeletionCheckSettings | None = None,
    deletion_probe: NoticeProbe | None = None,
    deletion_sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    """공지 수집 결과를 raw/normalized/indexed 계층에 반영합니다.

    ``deletion_check=True``는 정기 증분 수집용이다. 설정
    (``RAG_NOTICE_DELETION_CHECK_*``)에 따라 목록에 보이지 않은 최근 공지의 상세
    URL을 확인하고, 확정 삭제분은 숨김 공지와 같은 경로로 색인에서 제거한다.
    ``collect-only`` 모드에서는 색인과 상태가 어긋나지 않도록 실행하지 않는다.
    """
    collect_result = collect_notice_documents(
        incoming_df,
        allow_missing_detection=allow_missing_detection,
    )

    try:
        if mode == "collect-only":
            return _notice_collect_summary(collect_result)

        if deletion_check:
            _apply_scheduled_deletion_check(
                collect_result,
                settings=deletion_settings,
                probe=deletion_probe,
                sleep=deletion_sleep,
            )

        target_keys = list(dict.fromkeys(collect_result.changed_keys + collect_result.hidden_keys))
        if mode == "normalize-only":
            apply_notice_normalized_documents(document_keys=target_keys, apply_index=False)

        if mode == "index-only":
            apply_notice_normalized_documents(document_keys=target_keys, apply_index=True)
            refresh_notice_artifacts()
            _finalize_notice_derivatives(collect_result)
            return _notice_collect_summary(collect_result)

        if mode == "full-sync":
            apply_notice_normalized_documents(document_keys=target_keys, apply_index=True)
            refresh_notice_artifacts()
            _finalize_notice_derivatives(collect_result)

        return _notice_collect_summary(collect_result)
    except Exception as exc:
        _mark_notice_pipeline_failure(collect_result.run_id, exc)
        raise


def normalize_existing_notice_documents() -> None:
    session = SessionLocal()
    try:
        docs = session.query(SourceDocument).filter(
            SourceDocument.dataset == "notices",
            (SourceDocument.normalized_payload_json.isnot(None)) | (SourceDocument.normalized_path.isnot(None)),
        )
        keys = [doc.document_key for doc in docs]
    finally:
        session.close()
    apply_notice_normalized_documents(document_keys=keys, apply_index=False)


def migrate_legacy_notice_payloads(*, batch_size: int = 500) -> dict[str, int]:
    """Copy legacy sidecar JSON into SQLite without changing search indexes.

    This is intentionally idempotent.  It is the only supported path that
    reads ``normalized_path`` after the SQLite canonical-store migration.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    session = SessionLocal()
    migrated = raw_migrated = missing = invalid = 0
    source_type_repaired = content_hash_repaired = 0
    try:
        docs = (
            session.query(SourceDocument)
            .filter(SourceDocument.dataset == "notices")
            .order_by(SourceDocument.id.asc())
            .all()
        )
        for index, doc in enumerate(docs, start=1):
            if not doc.normalized_payload_json:
                payload = _load_normalized_notice(doc)
                if payload is None:
                    if doc.normalized_path:
                        invalid += 1
                    else:
                        missing += 1
                else:
                    doc.normalized_payload_json = canonical_json(payload)
                    migrated += 1
            if not doc.raw_payload_json and doc.raw_path:
                try:
                    raw_payload = json.loads(Path(doc.raw_path).read_text(encoding="utf-8"))
                    if isinstance(raw_payload, dict):
                        doc.raw_payload_json = canonical_json(raw_payload)
                        raw_migrated += 1
                except (OSError, UnicodeError, json.JSONDecodeError):
                    # The normalized representation remains sufficient for
                    # retrieval; report the issue through the existing invalid
                    # counter without blocking all valid documents.
                    invalid += 1
            # Older rows could turn pandas NaN into the literal source type
            # "nan".  Repair that metadata while the migration already owns
            # the canonical JSON write, so runtime citations no longer expose
            # an invalid source type.
            source_type = str(doc.source_type or "").strip().lower()
            if source_type in {"nan", "none"} and doc.normalized_payload_json:
                try:
                    normalized = json.loads(doc.normalized_payload_json)
                except (TypeError, json.JSONDecodeError):
                    normalized = None
                if isinstance(normalized, dict):
                    normalized["source_type"] = "html_notice"
                    doc.source_type = "html_notice"
                    doc.normalized_payload_json = canonical_json(normalized)
                    doc.content_hash = _hash_notice_content(normalized)
                    doc.schema_version = NOTICE_SCHEMA_VERSION
                    source_type_repaired += 1
            if doc.normalized_payload_json:
                try:
                    normalized_for_hash = json.loads(doc.normalized_payload_json)
                except (TypeError, json.JSONDecodeError):
                    normalized_for_hash = None
                if isinstance(normalized_for_hash, dict):
                    canonical_payload = canonical_json(normalized_for_hash)
                    if doc.normalized_payload_json != canonical_payload:
                        doc.normalized_payload_json = canonical_payload
                    expected_hash = _hash_notice_content(normalized_for_hash)
                    if doc.content_hash != expected_hash:
                        doc.content_hash = expected_hash
                        content_hash_repaired += 1
                    if doc.schema_version != NOTICE_SCHEMA_VERSION:
                        doc.schema_version = NOTICE_SCHEMA_VERSION
            if index % batch_size == 0:
                session.commit()
        session.commit()
        return {
            "migrated": migrated,
            "raw_migrated": raw_migrated,
            "missing": missing,
            "invalid": invalid,
            "source_type_repaired": source_type_repaired,
            "content_hash_repaired": content_hash_repaired,
        }
    finally:
        session.close()


__all__ = [
    "apply_notice_normalized_documents",
    "backfill_manual_notice_department_scopes",
    "backfill_manual_notice_source_documents",
    "collect_notice_documents",
    "ensure_manual_notice_source_document",
    "normalize_existing_notice_documents",
    "migrate_legacy_notice_payloads",
    "NoticeDeletionCheckSettings",
    "record_notice_ingestion_failure",
    "run_notice_deletion_check",
    "refresh_notice_artifacts",
    "rebuild_notices_from_source_documents",
    "sync_notices",
]
