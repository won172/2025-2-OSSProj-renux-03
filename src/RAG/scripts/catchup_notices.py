"""Audit a date-bounded notice crawl, then optionally apply it without missing detection.

Dry run is the default. It reads official pages but does not open or modify the
canonical database and search indexes. Apply should be run against an isolated
copy first; a failed post-publish lineage gate does not roll back the writes.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.crawlers.dongguk_notices import (  # noqa: E402
    TARGET_BOARDS,
    NoticeCrawlError,
    crawl_notices,
)


class CatchupRejected(RuntimeError):
    """The official crawl cannot prove complete coverage for this cutoff."""

    def __init__(self, message: str, *, report: dict | None = None) -> None:
        super().__init__(message)
        self.report = report


def _since(value: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise argparse.ArgumentTypeError("--since must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--since must be YYYY-MM-DD") from exc


def validate_crawl(frame, *, since: date, boards: list[str]) -> dict:
    diagnostics = frame.attrs.get("crawl_diagnostics")
    if not isinstance(diagnostics, list):
        raise CatchupRejected("missing per-board crawl diagnostics")
    by_board = {item.get("board_name"): item for item in diagnostics if isinstance(item, dict)}
    if len(by_board) != len(boards) or set(by_board) != set(boards) or len(diagnostics) != len(boards):
        raise CatchupRejected("one or more configured boards were not crawled")

    report = []
    failures = []
    for board in boards:
        item = by_board[board]
        row = {
            "board": board,
            "status": item.get("status"),
            "pages": int(item.get("list_pages_succeeded") or 0),
            "list_rows": int(item.get("list_rows_seen") or 0),
            "records_since": int(item.get("records_collected") or 0),
            "detail_failures": int(item.get("detail_failures") or 0),
            "oldest_list_date": item.get("oldest_list_date"),
            "termination": item.get("termination_reason"),
            "coverage_complete": item.get("coverage_complete") is True,
        }
        report.append(row)
        if (
            item.get("since") != since.isoformat()
            or row["status"] != "success"
            or row["pages"] == 0
            or row["list_rows"] == 0
            or row["detail_failures"] != 0
            or not row["coverage_complete"]
        ):
            failures.append(board)
    safe_report = {"since": since.isoformat(), "records_since": int(len(frame)), "boards": report}
    if failures or frame.attrs.get("crawl_incomplete_boards"):
        raise CatchupRejected(
            "coverage or source failures on boards: " + ", ".join(failures or boards),
            report=safe_report,
        )
    if not frame.empty:
        required = {"게시판", "게시일", "상세URL", "원문글ID"}
        if not required.issubset(frame.columns):
            raise CatchupRejected("crawl rows lack required identity or date fields", report=safe_report)
        dates = frame["게시일"].map(
            lambda value: value.date() if hasattr(value, "date") else value
        )
        if any(not isinstance(value, date) or value < since for value in dates):
            raise CatchupRejected("crawl rows contain missing or out-of-range dates", report=safe_report)
        if not set(frame["게시판"]).issubset(boards):
            raise CatchupRejected("crawl rows contain an unconfigured board", report=safe_report)
        if frame["상세URL"].isna().any() or frame["상세URL"].astype(str).str.strip().eq("").any():
            raise CatchupRejected("crawl rows contain missing source URLs", report=safe_report)
        if frame["상세URL"].duplicated().any():
            raise CatchupRejected("crawl rows contain duplicate source URLs", report=safe_report)
    return safe_report


def apply_crawl(frame) -> dict:
    from src.database import IngestionRun, SessionLocal, init_db
    from src.pipelines.notices_sync import sync_notices
    from src.services.ingestion_freshness import build_ingestion_freshness_report
    from scripts.report_canonical_lineage import main as report_lineage

    configured_pointer = os.getenv("RAG_COLLECTION_POINTER_FILE")
    if configured_pointer and not Path(configured_pointer).is_file():
        raise CatchupRejected("configured collection pointer file does not exist")
    if report_lineage(["--mode", "strict", "--datasets", "notices"]) != 0:
        raise CatchupRejected("pre-apply strict notices lineage failed; existing corpus was not changed")
    init_db()
    summary = sync_notices(
        frame,
        mode="full-sync",
        allow_missing_detection=False,
        deletion_check=False,
    )
    if summary.get("failed") or summary.get("incomplete_boards") or summary.get("deleted"):
        raise CatchupRejected("notice sync reported failed documents, incomplete boards, or deletions")
    if report_lineage(["--mode", "strict", "--datasets", "notices"]) != 0:
        raise CatchupRejected("post-apply strict notices lineage failed; inspect and restore the isolated copy")
    run_id = int(summary.get("run_id") or 0)
    session = SessionLocal()
    try:
        run = session.get(IngestionRun, run_id) if run_id else None
        freshness = build_ingestion_freshness_report(session, datasets=("notices",))["datasets"][0]
        latest_run = freshness.get("latest_run") or {}
        if (
            run is None
            or run.dataset != "notices"
            or run.status != "success"
            or run.finished_at is None
            or not run.corpus_revision
            or run.documents_failed
            or freshness["state"] != "healthy"
            or latest_run.get("id") != run_id
        ):
            raise CatchupRejected("notices ingestion run or freshness did not complete successfully")
    finally:
        session.close()
    return {
        **{key: int(summary.get(key, 0)) for key in ("seen", "new", "updated", "deleted", "failed")},
        "run_id": run_id,
        "freshness": "healthy",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", "--since-date", dest="since", required=True, type=_since,
                        help="Inclusive official posting date (YYYY-MM-DD)")
    parser.add_argument("--max-pages", type=int, default=100, help="Hard page cap per board (default: 100)")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay between detail requests")
    parser.add_argument("--apply", action="store_true", help="Write canonical notices and indexes after crawl validation")
    args = parser.parse_args(argv)
    if args.max_pages < 1 or args.delay < 0:
        parser.error("--max-pages must be positive and --delay must be nonnegative")

    try:
        frame = crawl_notices(
            boards=TARGET_BOARDS,
            since=args.since,
            max_pages=args.max_pages,
            delay=args.delay,
            known_ids_by_board=None,
        )
        report = validate_crawl(frame, since=args.since, boards=TARGET_BOARDS)
        report["mode"] = "apply" if args.apply else "dry_run"
        if args.apply:
            report["sync"] = apply_crawl(frame)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    except (CatchupRejected, NoticeCrawlError) as exc:
        result = {"status": "rejected", "reason": str(exc)}
        if isinstance(exc, CatchupRejected) and exc.report is not None:
            result["report"] = exc.report
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
