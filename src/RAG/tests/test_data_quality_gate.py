from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.database import Base, Chunk, Notice, SourceDocument  # noqa: E402
from src.services.data_quality import (  # noqa: E402
    DataQualityThresholds,
    build_source_document_quality_report,
)
from api import rag_service  # noqa: E402


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _document(index: int, **overrides) -> SourceDocument:
    values = {
        "dataset": "notices",
        "source_type": "html_notice",
        "source_id": f"notice-{index}",
        "source_url": f"https://www.dongguk.edu/notice/{index}",
        "document_key": f"notices:{index}",
        "title": f"민감하지 않은 테스트 제목 {index}",
        "category": "학사",
        "status": "active",
        "last_indexed_at": None,
        "parse_error": None,
    }
    values.update(overrides)
    return SourceDocument(**values)


def test_quality_report_aggregates_failures_and_emits_retry_manifest_without_content():
    session = _session()
    try:
        linked_notice = Notice(
            board="학사",
            title="연결된 공지",
            category="학사",
            detail_url="https://www.dongguk.edu/notice/1",
            content="공지 본문",
        )
        session.add(linked_notice)
        session.flush()
        session.add(
            Chunk(
                chunk_id="notice-1-chunk",
                chunk_text="공지 본문",
                notice_id=linked_notice.id,
                doc_id="notices:1",
            )
        )
        session.add_all([
            _document(1, last_indexed_at=None),
            _document(2, status="parse_failed", category="", parse_error="parser timeout"),
            _document(3, status="hidden", category="일반"),
            _document(4, category="", last_indexed_at=datetime(2026, 7, 20)),
        ])
        session.commit()
        report = build_source_document_quality_report(
            session,
            thresholds=DataQualityThresholds(
                max_parse_error_ratio=0.10,
                max_category_unknown_ratio=0.20,
                max_inactive_ratio=0.40,
                max_index_mismatch_ratio=0.20,
            ),
        )
    finally:
        session.close()

    assert report["contains_user_content"] is False
    assert report["counts"] == {
        "total": 4,
        "active": 2,
        "updated": 0,
        "active_chunk_linked": 1,
        "indexable_chunk_linked": 1,
        "parse_error": 1,
        "category_unknown": 2,
        "inactive": 2,
        "index_mismatch": 1,
    }
    assert report["gate_passed"] is False
    assert {item["metric"] for item in report["violations"]} == {
        "parse_error", "category_unknown", "inactive", "index_mismatch",
    }
    assert report["retry_document_count"] == 2
    for retry in report["retry_documents"]:
        assert set(retry) == {"document_key", "source_id", "source_url", "status", "reasons"}
        assert "title" not in retry
        assert "question" not in retry
        assert "answer" not in retry
    retries = {row["source_id"]: row["reasons"] for row in report["retry_documents"]}
    assert "index_mismatch" not in retries.get("notice-1", [])  # timestamp 없음 + 실제 chunk 있음
    assert "index_mismatch" in retries["notice-4"]  # timestamp 있음 + 실제 chunk 없음


def test_quality_gate_passes_with_configurable_thresholds():
    session = _session()
    try:
        session.add(_document(1, category="", last_indexed_at=None))
        session.commit()
        report = build_source_document_quality_report(
            session,
            thresholds=DataQualityThresholds(
                max_parse_error_ratio=1.0,
                max_category_unknown_ratio=1.0,
                max_inactive_ratio=1.0,
                max_index_mismatch_ratio=1.0,
            ),
        )
    finally:
        session.close()
    assert report["gate_passed"] is True


def test_quality_report_rejects_url_only_notice_linkage_without_canonical_doc_id():
    session = _session()
    try:
        document = _document(1, last_indexed_at=datetime(2026, 8, 10))
        notice = Notice(
            board="학사",
            title="레거시 공지",
            category="학사",
            detail_url=document.source_url,
            content="공지 본문",
        )
        session.add_all([document, notice])
        session.flush()
        session.add(
            Chunk(
                chunk_id="legacy-url-linked-chunk",
                chunk_text="공지 본문",
                notice_id=notice.id,
                doc_id=None,
            )
        )
        session.commit()

        report = build_source_document_quality_report(session)
    finally:
        session.close()

    assert report["counts"]["active_chunk_linked"] == 0
    assert report["counts"]["indexable_chunk_linked"] == 0
    assert report["counts"]["index_mismatch"] == 1
    assert report["retry_documents"][0]["reasons"] == ["index_mismatch"]


def test_strict_mode_turns_quality_threshold_violation_into_readiness_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("RAG_DATA_QUALITY_MODE", "strict")
    monkeypatch.setattr(rag_service, "_validate_required_configuration", lambda: "ok")
    monkeypatch.setattr(rag_service, "init_db", lambda: None)
    monkeypatch.setattr(
        rag_service,
        "_ensure_dataset",
        lambda key: (pd.DataFrame({"chunk_id": [f"{key}-1"]}), object(), object(), [f"{key}-1"]),
    )
    monkeypatch.setattr(rag_service, "get_embedder", object)
    monkeypatch.setattr(
        rag_service,
        "build_canonical_lineage_report",
        lambda: {"gate_passed": True, "datasets": [], "violations": []},
    )
    monkeypatch.setattr(
        rag_service,
        "build_source_document_quality_report",
        lambda session, retry_limit=0: {
            "gate_passed": False,
            "counts": {"total": 10, "parse_error": 2},
            "ratios": {"parse_error": 0.2},
            "violations": [{"metric": "parse_error", "actual": 0.2, "maximum": 0.05}],
        },
    )
    rag_service._reset_startup_readiness()
    try:
        rag_service._run_required_startup_checks()
        response = rag_service.ready()
        assert response.status_code == 503
        payload = __import__("json").loads(response.body.decode("utf-8"))
        assert payload["checks"]["data_quality"]["required"] is True
        assert payload["checks"]["data_quality"]["ready"] is False
        assert payload["checks"]["data_quality"]["detail"] == "strict_threshold_exceeded"
    finally:
        rag_service._reset_startup_readiness()
