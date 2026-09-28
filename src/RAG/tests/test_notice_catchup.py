"""Fail-closed catch-up coverage and publication contracts."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scripts import catchup_notices
from src.crawlers import dongguk_notices
from src.database import Base, IngestionRun, kst_now


BOARD = "일반공지"
CUTOFF = date(2026, 9, 27)


def _list_row(article_id: int, day: int) -> dict:
    return {
        "article_id": article_id,
        "title": f"private title {article_id}",
        "category": "general",
        "posted_at": date(2026, 9, day),
        "views": 1,
        "is_pinned": False,
    }


def _crawl(monkeypatch, pages: dict[int, list[dict]], *, cap: int, fail_detail: bool = False):
    def list_page(_board_code, page=1, **_kwargs):
        return pages.get(page, [])

    def detail(_board_code, article_id, **_kwargs):
        if fail_detail:
            raise TimeoutError("detail unavailable")
        item = next(row for rows in pages.values() for row in rows if row["article_id"] == article_id)
        return {
            "posted_at": item["posted_at"],
            "views": 1,
            "detail_url": f"https://www.dongguk.edu/article/GENERALNOTICES/detail/{article_id}",
            "content_html": "<p>private body</p>",
            "content_text": "private body",
            "attachments": [],
        }

    monkeypatch.setattr(dongguk_notices, "fetch_notice_list", list_page)
    monkeypatch.setattr(dongguk_notices, "fetch_notice_detail", detail)
    return dongguk_notices.crawl_notices(
        boards=[BOARD], since=CUTOFF, max_pages=cap, delay=0, known_ids_by_board=None,
    )


def test_catchup_includes_cutoff_and_passes_only_after_whole_older_page(monkeypatch):
    frame = _crawl(
        monkeypatch,
        {1: [_list_row(3, 28), _list_row(2, 27)], 2: [_list_row(1, 26)]},
        cap=2,
    )

    report = catchup_notices.validate_crawl(frame, since=CUTOFF, boards=[BOARD])
    assert set(frame["원문글ID"]) == {2, 3}
    assert report["records_since"] == 2
    assert report["boards"][0]["termination"] == "before_since_boundary"
    assert report["boards"][0]["oldest_list_date"] == "2026-09-26"
    assert "private title" not in str(report)
    assert "private body" not in str(report)


def test_catchup_rejects_page_cap_before_boundary(monkeypatch):
    frame = _crawl(monkeypatch, {1: [_list_row(3, 28), _list_row(2, 27)]}, cap=1)
    with pytest.raises(catchup_notices.CatchupRejected, match="coverage") as error:
        catchup_notices.validate_crawl(frame, since=CUTOFF, boards=[BOARD])
    assert error.value.report["boards"][0]["termination"] == "page_cap"
    assert "private title" not in str(error.value.report)


def test_catchup_rejects_list_failure_after_first_page(monkeypatch):
    def list_page(_board_code, page=1, **_kwargs):
        if page == 1:
            return [_list_row(2, 27)]
        raise TimeoutError("list unavailable")

    monkeypatch.setattr(dongguk_notices, "fetch_notice_list", list_page)
    monkeypatch.setattr(dongguk_notices, "fetch_notice_detail", lambda *_args, **_kwargs: {
        "posted_at": CUTOFF, "detail_url": "https://www.dongguk.edu/article/GENERALNOTICES/detail/2",
        "content_html": "", "content_text": "", "attachments": [],
    })
    frame = dongguk_notices.crawl_notices(
        boards=[BOARD], since=CUTOFF, max_pages=2, delay=0,
    )
    with pytest.raises(catchup_notices.CatchupRejected):
        catchup_notices.validate_crawl(frame, since=CUTOFF, boards=[BOARD])


@pytest.mark.parametrize(
    "pages,fail_detail",
    [
        ({1: []}, False),
        ({1: [_list_row(2, 27)], 2: [_list_row(3, 28)]}, False),
        ({1: [_list_row(2, 27)], 2: [_list_row(1, 26)]}, True),
    ],
)
def test_catchup_rejects_empty_unordered_or_detail_failure(monkeypatch, pages, fail_detail):
    if pages == {1: []}:
        with pytest.raises(dongguk_notices.NoticeCrawlError):
            _crawl(monkeypatch, pages, cap=2)
        return
    frame = _crawl(monkeypatch, pages, cap=2, fail_detail=fail_detail)
    with pytest.raises(catchup_notices.CatchupRejected):
        catchup_notices.validate_crawl(frame, since=CUTOFF, boards=[BOARD])


def test_apply_never_enables_missing_detection_or_deletion(monkeypatch):
    from scripts import report_canonical_lineage
    from src import database
    from src.pipelines import notices_sync

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    calls = []
    monkeypatch.delenv("RAG_COLLECTION_POINTER_FILE", raising=False)
    monkeypatch.setattr(database, "init_db", lambda: calls.append("init"))
    monkeypatch.setattr(database, "SessionLocal", session_factory)

    def sync(frame, **kwargs):
        calls.append(kwargs)
        session = session_factory()
        try:
            run = IngestionRun(
                dataset="notices", status="success", finished_at=kst_now(),
                corpus_revision="revision", documents_seen=len(frame),
            )
            session.add(run)
            session.commit()
            return {
                "run_id": run.id, "seen": len(frame), "new": 1,
                "updated": 0, "deleted": 0, "failed": 0,
            }
        finally:
            session.close()

    monkeypatch.setattr(notices_sync, "sync_notices", sync)
    monkeypatch.setattr(report_canonical_lineage, "main", lambda args: calls.append(args) or 0)
    result = catchup_notices.apply_crawl(pd.DataFrame([{"id": 1}]))

    assert result["new"] == 1
    assert result["freshness"] == "healthy"
    assert calls[0] == ["--mode", "strict", "--datasets", "notices"]
    assert calls[1] == "init"
    assert calls[2] == {
        "mode": "full-sync", "allow_missing_detection": False, "deletion_check": False,
    }
    assert calls[3] == ["--mode", "strict", "--datasets", "notices"]


def test_sync_notices_records_durable_success_and_refreshes_freshness(monkeypatch):
    from src.pipelines import notices_sync
    from src.services.ingestion_freshness import build_ingestion_freshness_report

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(notices_sync, "SessionLocal", session_factory)
    monkeypatch.setattr(notices_sync, "apply_notice_normalized_documents", lambda **_kwargs: None)
    monkeypatch.setattr(notices_sync, "refresh_notice_artifacts", lambda: None)
    monkeypatch.setattr(notices_sync, "_finalize_notice_derivatives", lambda _result: None)
    frame = pd.DataFrame([{
        "게시판": BOARD, "게시판코드": "GENERALNOTICES", "원문글ID": 2,
        "게시일": CUTOFF, "제목": "official notice", "본문": "Official source text",
        "상세URL": "https://www.dongguk.edu/article/GENERALNOTICES/detail/2",
        "첨부파일": [],
    }])

    summary = notices_sync.sync_notices(
        frame, mode="full-sync", allow_missing_detection=False, deletion_check=False,
    )
    session = session_factory()
    try:
        run = session.get(IngestionRun, summary["run_id"])
        freshness = build_ingestion_freshness_report(session, datasets=("notices",))["datasets"][0]
        assert run is not None
        assert run.status == "success"
        assert run.finished_at is not None
        assert run.documents_seen == 1
        assert freshness["state"] == "healthy"
        assert freshness["latest_run"]["id"] == run.id
    finally:
        session.close()


def test_apply_rejects_missing_configured_pointer_before_writes(monkeypatch, tmp_path):
    from src import database

    monkeypatch.setenv("RAG_COLLECTION_POINTER_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(database, "init_db", lambda: pytest.fail("database was opened"))
    with pytest.raises(catchup_notices.CatchupRejected, match="pointer"):
        catchup_notices.apply_crawl(pd.DataFrame([{"id": 1}]))


def test_apply_rejects_partial_derivative_run_despite_fresh_timestamp(monkeypatch):
    from scripts import report_canonical_lineage
    from src import database
    from src.pipelines import notices_sync

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.delenv("RAG_COLLECTION_POINTER_FILE", raising=False)
    monkeypatch.setattr(database, "SessionLocal", session_factory)
    monkeypatch.setattr(database, "init_db", lambda: None)
    monkeypatch.setattr(report_canonical_lineage, "main", lambda _args: 0)

    def partial_sync(_frame, **_kwargs):
        session = session_factory()
        try:
            run = IngestionRun(
                dataset="notices", status="partial_success", finished_at=kst_now(),
                corpus_revision="revision", outcome_code="derivative_failure",
            )
            session.add(run)
            session.commit()
            return {"run_id": run.id, "failed": 0, "deleted": 0, "incomplete_boards": 0}
        finally:
            session.close()

    monkeypatch.setattr(notices_sync, "sync_notices", partial_sync)
    with pytest.raises(catchup_notices.CatchupRejected, match="freshness"):
        catchup_notices.apply_crawl(pd.DataFrame([{"id": 1}]))


def test_default_cli_dry_run_never_applies(monkeypatch, capsys):
    frame = pd.DataFrame([{
        "게시판": BOARD, "게시일": CUTOFF,
        "상세URL": "https://www.dongguk.edu/article/GENERALNOTICES/detail/2",
        "원문글ID": 2, "제목": "private title", "본문": "private body",
    }])
    frame.attrs["crawl_diagnostics"] = [{
        "board_name": BOARD, "status": "success", "list_pages_succeeded": 2,
        "list_rows_seen": 2, "records_collected": 1, "detail_failures": 0,
        "oldest_list_date": "2026-09-26", "termination_reason": "before_since_boundary",
        "coverage_complete": True, "since": CUTOFF.isoformat(),
    }]
    monkeypatch.setattr(catchup_notices, "TARGET_BOARDS", [BOARD])
    monkeypatch.setattr(catchup_notices, "crawl_notices", lambda **_kwargs: frame)
    monkeypatch.setattr(catchup_notices, "apply_crawl", lambda _frame: pytest.fail("dry run applied"))

    assert catchup_notices.main(["--since", CUTOFF.isoformat()]) == 0
    output = capsys.readouterr().out
    assert '"mode": "dry_run"' in output
    assert "private title" not in output
    assert "private body" not in output
