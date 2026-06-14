"""동국대학교 notices 데이터셋의 증분 수집/정규화/색인을 담당합니다."""
from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from src.config import DATA_SOURCES, NORMALIZED_DIR, RAW_DIR
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
    _persist_chunks,
    build_notice_chunks,
    build_notice_index_frame_from_db,
)
from src.utils.preprocess import standardize_date
from src.vectorstore.chroma_client import delete_items, reset_collection, upsert_items

NOTICE_SCHEMA_VERSION = 1
NOTICE_COLLECTION = DATASET_ARTIFACTS["notices"].collection
AUTO_NOTICE_FILTER = (Notice.is_manual == 0) | (Notice.is_manual.is_(None))
NOTICE_REQUIRED_FIELDS = {
    "title": "제목이 비어 있습니다.",
    "content_text": "본문이 비어 있습니다.",
    "detail_url": "상세 URL이 비어 있습니다.",
    "board_name": "게시판명이 비어 있습니다.",
    "board_code": "게시판 코드가 비어 있습니다.",
}


@dataclass
class NoticeCollectResult:
    changed_keys: list[str]
    hidden_keys: list[str]
    documents_seen: int
    documents_new: int
    documents_updated: int
    documents_deleted: int
    documents_failed: int


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)


def _json_default(value: Any):
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    return str(value)


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
    raw = json.dumps(
        {
            "title": record["title"],
            "category": record["category"],
            "posted_at": record["published_at"],
            "content_text": record["content_text"],
            "attachments": record["attachments"],
        },
        ensure_ascii=False,
        sort_keys=True,
        default=_json_default,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _normalize_notice_record(row: pd.Series) -> tuple[dict[str, Any], bool]:
    detail_url = str(row.get("상세URL") or row.get("detail_url") or "").strip()
    board_name = str(row.get("게시판") or row.get("board_name") or "").strip()
    board_code = str(row.get("게시판코드") or row.get("board_code") or "").strip()
    article_id = row.get("원문글ID") or row.get("article_id") or _extract_article_id(detail_url)
    source_id = f"{board_code}:{article_id}" if board_code and article_id is not None else ""
    document_key = f"notices:{source_id}" if source_id else ""
    attachments, attachments_parse_failed = _normalize_attachments(row.get("첨부파일") or row.get("attachments"))

    published_at = standardize_date(row.get("게시일") or row.get("posted_at"))
    normalized = {
        "document_key": document_key,
        "dataset": "notices",
        "source_type": "html_notice",
        "source_id": source_id,
        "board_name": board_name,
        "board_code": board_code,
        "article_id": article_id,
        "title": str(row.get("제목") or row.get("title") or "").strip(),
        "category": str(row.get("카테고리") or row.get("category") or "").strip(),
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


def _raw_notice_path(document_key: str, published_at: str) -> Path:
    if published_at:
        try:
            dt = datetime.strptime(published_at, "%Y-%m-%d")
        except ValueError:
            dt = kst_now()
    else:
        dt = kst_now()
    return RAW_DIR / "notices" / f"{dt.year:04d}" / f"{dt.month:02d}" / f"{_safe_filename(document_key)}.json"


def _normalized_notice_path(document_key: str) -> Path:
    return NORMALIZED_DIR / "notices" / f"{_safe_filename(document_key)}.json"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


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

    if len(record.get("content_text", "").strip()) < 40:
        checks.append({"check_type": "content_length", "severity": "warning", "message": "본문 길이가 매우 짧습니다."})

    return checks, "\n".join(parse_errors) if parse_errors else None


def _load_normalized_notice(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return None
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
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


def _export_active_notices_csv(session) -> None:
    rows = (
        session.query(SourceDocument)
        .filter(
            SourceDocument.dataset == "notices",
            SourceDocument.status.in_(["active", "updated"]),
            SourceDocument.normalized_path.isnot(None),
        )
        .order_by(SourceDocument.published_at.desc(), SourceDocument.id.desc())
        .all()
    )

    records: list[dict[str, Any]] = []
    for row in rows:
        normalized = _load_normalized_notice(row.normalized_path)
        if not normalized:
            continue
        records.append(
            {
                "게시판": normalized.get("board_name", ""),
                "게시판코드": normalized.get("board_code", ""),
                "원문글ID": normalized.get("article_id", ""),
                "제목": normalized.get("title", ""),
                "카테고리": normalized.get("category", ""),
                "게시일": normalized.get("published_at", ""),
                "상단고정": normalized.get("is_pinned", False),
                "상세URL": normalized.get("detail_url", ""),
                "본문": normalized.get("content_text", ""),
                "본문HTML": normalized.get("content_html", ""),
                "첨부파일": json.dumps(normalized.get("attachments", []), ensure_ascii=False),
            }
        )

    output_path = DATA_SOURCES["notices"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(records).to_csv(output_path, index=False, encoding="utf-8-sig")


def _normalized_notice_to_notice_row(normalized: dict[str, Any], *, db_id: int | None = None) -> dict[str, Any]:
    return {
        "게시판": normalized.get("board_name", ""),
        "게시판코드": normalized.get("board_code", ""),
        "원문글ID": normalized.get("article_id", ""),
        "원문ID": normalized.get("source_id", ""),
        "문서키": normalized.get("document_key", ""),
        "제목": normalized.get("title", ""),
        "카테고리": normalized.get("category", ""),
        "게시일": normalized.get("published_at", ""),
        "상단고정": normalized.get("is_pinned", False),
        "상세URL": normalized.get("detail_url", ""),
        "본문": normalized.get("content_text", ""),
        "본문HTML": normalized.get("content_html", ""),
        "첨부파일": normalized.get("attachments", []),
        "db_id": db_id,
    }


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

    session.commit()
    chunk_df[["chunk_id", "chunk_text", "notice_id"]].to_sql("chunks", con=session.bind, if_exists="append", index=False)

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
    session = SessionLocal()
    run = IngestionRun(dataset="notices", status="running")
    session.add(run)
    session.commit()
    session.refresh(run)

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
            raw_path = _raw_notice_path(normalized["document_key"], normalized["published_at"])
            normalized_path = _normalized_notice_path(normalized["document_key"])
            _write_json(raw_path, raw_payload)
            _write_json(normalized_path, normalized)

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
            elif existing.content_hash != normalized["content_hash"] or existing.status in {"hidden", "deleted", "parse_failed"}:
                status = "updated"
                documents_updated += 1
                should_index = True

            if existing is None:
                existing = SourceDocument(
                    dataset="notices",
                    source_type="html_notice",
                    source_id=normalized["source_id"],
                    document_key=normalized["document_key"],
                )
                session.add(existing)
                existing_docs[normalized["source_id"]] = existing

            existing.source_url = normalized["detail_url"]
            existing.title = normalized["title"]
            existing.category = normalized["category"]
            existing.published_at = normalized["published_at"]
            existing.status = status
            existing.content_hash = normalized["content_hash"]
            existing.schema_version = NOTICE_SCHEMA_VERSION
            existing.raw_path = str(raw_path)
            existing.normalized_path = str(normalized_path)
            existing.collected_at = kst_now()
            existing.last_parsed_at = kst_now()
            existing.parse_error = parse_error
            existing.miss_count = 0

            if should_index and status != "parse_failed":
                changed_keys.append(normalized["document_key"])

        if allow_missing_detection:
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
                normalized = _load_normalized_notice(doc.normalized_path)
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
        run.documents_seen = documents_seen
        run.documents_new = documents_new
        run.documents_updated = documents_updated
        run.documents_deleted = documents_deleted
        run.documents_failed = documents_failed
        run.finished_at = kst_now()
        session.commit()

        return NoticeCollectResult(
            changed_keys=changed_keys,
            hidden_keys=hidden_keys,
            documents_seen=documents_seen,
            documents_new=documents_new,
            documents_updated=documents_updated,
            documents_deleted=documents_deleted,
            documents_failed=documents_failed,
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
        active_docs = [doc for doc in documents if doc.status in {"active", "updated"}]
        hidden_docs = [doc for doc in documents if doc.status in {"hidden", "deleted"}]

        normalized_rows: list[dict[str, Any]] = []
        for doc in active_docs:
            normalized = _load_normalized_notice(doc.normalized_path)
            if not normalized:
                doc.status = "parse_failed"
                doc.parse_error = "normalized JSON을 읽지 못했습니다."
                continue
            normalized_rows.append(normalized)

        notice_rows = _upsert_notice_domain_rows(session, normalized_rows)
        _apply_hidden_notices(session, hidden_docs)

        if apply_index:
            _upsert_notice_chunks(session, notice_rows, source_docs_by_source_id)

        session.commit()
        _export_active_notices_csv(session)
    finally:
        session.close()


def refresh_notice_artifacts() -> None:
    """DB의 notice chunks를 기준으로 parquet, TF-IDF, Chroma를 함께 재생성합니다."""
    frame = build_notice_index_frame_from_db()
    reset_collection(NOTICE_COLLECTION)
    if frame.empty:
        return
    _persist_chunks("notices", NOTICE_COLLECTION, frame)


def sync_notices(
    incoming_df: pd.DataFrame,
    *,
    allow_missing_detection: bool = False,
    mode: str = "full-sync",
) -> dict[str, int]:
    """공지 수집 결과를 raw/normalized/indexed 계층에 반영합니다."""
    collect_result = collect_notice_documents(
        incoming_df,
        allow_missing_detection=allow_missing_detection,
    )

    if mode == "collect-only":
        return {
            "seen": collect_result.documents_seen,
            "new": collect_result.documents_new,
            "updated": collect_result.documents_updated,
            "deleted": collect_result.documents_deleted,
            "failed": collect_result.documents_failed,
        }

    target_keys = list(dict.fromkeys(collect_result.changed_keys + collect_result.hidden_keys))
    if mode == "normalize-only":
        apply_notice_normalized_documents(document_keys=target_keys, apply_index=False)

    if mode == "index-only":
        apply_notice_normalized_documents(document_keys=target_keys, apply_index=True)
        refresh_notice_artifacts()
        return {
            "seen": collect_result.documents_seen,
            "new": collect_result.documents_new,
            "updated": collect_result.documents_updated,
            "deleted": collect_result.documents_deleted,
            "failed": collect_result.documents_failed,
        }

    if mode == "full-sync":
        apply_notice_normalized_documents(document_keys=target_keys, apply_index=True)
        refresh_notice_artifacts()

    return {
        "seen": collect_result.documents_seen,
        "new": collect_result.documents_new,
        "updated": collect_result.documents_updated,
        "deleted": collect_result.documents_deleted,
        "failed": collect_result.documents_failed,
    }


def normalize_existing_notice_documents() -> None:
    session = SessionLocal()
    try:
        docs = session.query(SourceDocument).filter(
            SourceDocument.dataset == "notices",
            SourceDocument.normalized_path.isnot(None),
        )
        keys = [doc.document_key for doc in docs]
    finally:
        session.close()
    apply_notice_normalized_documents(document_keys=keys, apply_index=False)


__all__ = [
    "apply_notice_normalized_documents",
    "collect_notice_documents",
    "normalize_existing_notice_documents",
    "refresh_notice_artifacts",
    "sync_notices",
]
