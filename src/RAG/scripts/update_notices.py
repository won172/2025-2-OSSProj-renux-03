"""동국대 공지를 크롤링해 CSV/Chroma 인덱스에 신규 행을 추가합니다."""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.crawlers.dongguk_notices import TARGET_BOARDS, crawl_notices
from src.pipelines.notices_sync import (
    apply_notice_normalized_documents,
    load_known_article_ids_by_board,
    normalize_existing_notice_documents,
    refresh_notice_artifacts,
    sync_notices,
)
from src.pipelines.ingest import reindex_from_db
from src.database import init_db


def _run_once(
    boards: list[str],
    max_pages: int | None,
    delay: float,
    earliest_year: int | None,
    mode: str,
    full_backfill: bool = False,
) -> None:
    start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{start_ts}] 🕐 notices 작업 시작 (mode={mode})")

    if mode == "normalize-only":
        try:
            normalize_existing_notice_documents()
            print("✅ normalized 문서 기준으로 notices 도메인 테이블을 갱신했습니다.")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ normalize-only 실패: {exc}")
        return

    if mode == "index-only":
        try:
            reindex_from_db("notices")
            refresh_notice_artifacts()
            print("✅ notices 인덱스와 TF-IDF 아티팩트를 다시 생성했습니다.")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ index-only 실패: {exc}")
        return

    try:
        known_ids_by_board = None
        if not full_backfill:
            try:
                known_ids_by_board = load_known_article_ids_by_board()
            except Exception as exc:  # noqa: BLE001 — known-id 로드는 조기 중단 최적화이므로 실패해도 수집한다.
                print(f"⚠️ 기존 공지 ID 로드 실패: {exc}")
        notices_df = crawl_notices(
            boards=boards,
            max_pages=max_pages,
            delay=delay,
            earliest_year=earliest_year,
            known_ids_by_board=known_ids_by_board,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 크롤링 실패: {exc}")
        return

    try:
        summary = sync_notices(
            notices_df,
            allow_missing_detection=max_pages is None,
            mode=mode,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 동기화 실패: {exc}")
        return
    end_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(
        f"[{end_ts}] ✅ notices 반영 완료. "
        f"seen={summary['seen']} new={summary['new']} updated={summary['updated']} "
        f"deleted={summary['deleted']} failed={summary['failed']}"
    )


def main() -> None:
    init_db() # DB 초기화
    parser = argparse.ArgumentParser(description="Dongguk notice crawler + incremental index updater")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="Number of pages per board to fetch (default: 5)",
    )
    parser.add_argument("--delay", type=float, default=0.2, help="Delay between detail requests in seconds")
    parser.add_argument(
        "--boards",
        nargs="*",
        default=None,
        help="Specific board names to crawl (default: all configured boards)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Ignore --max-pages and crawl the entire board (could take long).",
    )
    parser.add_argument(
        "--earliest-year",
        type=int,
        default=2023,
        help="Earliest year to crawl (default: 2023).",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        help="Repeat crawl every N minutes (0 = run once).",
    )
    parser.add_argument(
        "--mode",
        choices=["collect-only", "normalize-only", "index-only", "full-sync"],
        default="full-sync",
        help="Execution mode for notices pipeline (default: full-sync).",
    )

    args = parser.parse_args()

    boards = args.boards or TARGET_BOARDS
    max_pages = None if args.full else args.max_pages
    earliest_year = args.earliest_year

    if args.interval <= 0:
        _run_once(boards, max_pages, args.delay, earliest_year, args.mode, full_backfill=args.full)
        return

    interval_seconds = args.interval * 60
    try:
        while True:
            _run_once(boards, max_pages, args.delay, earliest_year, args.mode, full_backfill=args.full)
            print(f"⏳ {args.interval}분 후 다음 작업을 실행합니다. (종료: Ctrl+C)")
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("🛑 스케줄 반복을 종료합니다.")


if __name__ == "__main__":
    main()
