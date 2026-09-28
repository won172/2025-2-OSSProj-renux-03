"""Catch up official meal menus from the last successful run through today.

Dry run by default. Use --apply only after reviewing the coverage summary.
The D-Flex board crawler reads its three newest posts, so older windows may
be incomplete; --coop-only explicitly limits the run to the co-op HTML source.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.crawlers.dongguk_meals import DFLEX_RESTAURANT, KST, crawl_meals
from src.database import DATABASE_FILE, IngestionRun, SessionLocal
from src.pipelines.ingest import ingest_meals, load_meals_from_db

MEAL_COLUMNS = ("date", "weekday", "restaurant", "menu_text", "is_closed")


def last_success_date() -> date:
    """Use the latest fully successful collection, not a partial run."""
    session = SessionLocal()
    try:
        run = (
            session.query(IngestionRun)
            .filter(IngestionRun.dataset == "meals", IngestionRun.status == "success")
            .order_by(IngestionRun.finished_at.desc(), IngestionRun.id.desc())
            .first()
        )
        if run is None or (run.finished_at or run.started_at) is None:
            raise ValueError("No fully successful meals run; pass --since YYYY-MM-DD")
        return (run.finished_at or run.started_at).date()
    finally:
        session.close()


def _meal_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=MEAL_COLUMNS)
    missing = set(MEAL_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Meal frame missing columns: {', '.join(sorted(missing))}")
    return frame.loc[:, list(MEAL_COLUMNS)].fillna("").astype(str).reset_index(drop=True)


def validate_crawl(frame: pd.DataFrame, since: date, today: date, *, coop_only: bool) -> dict:
    """Reject incomplete or unprovable coverage before any canonical write."""
    diagnostics = dict(frame.attrs.get("crawl_diagnostics") or {})
    requested = (today - since).days + 1
    if requested <= 0:
        raise ValueError("--since must not be after today")
    expected = {
        "requested_days": requested,
        "fetched_days": requested,
        "fetch_failed_days": 0,
        "parsed_days_with_rows": requested,
        "parse_empty_days": 0,
    }
    if any(diagnostics.get(key) != value for key, value in expected.items()):
        observed = " ".join(
            f"{key}={int(diagnostics.get(key) or 0)}" for key in expected
        )
        raise ValueError(f"Co-op source coverage incomplete ({observed}); no data written")
    rows = _meal_rows(frame)
    if rows.empty:
        raise ValueError("Meal crawl returned no rows; no data written")
    if rows[["date", "restaurant", "menu_text"]].eq("").any().any():
        raise ValueError("Meal crawl has blank required fields; no data written")
    if rows.duplicated(["date", "restaurant"]).any():
        raise ValueError("Meal crawl has duplicate meal identities; no data written")
    dates = pd.to_datetime(rows["date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any() or not dates.dt.date.between(since, today).all():
        raise ValueError("Meal crawl has out-of-window dates; no data written")
    if not coop_only:
        dflex_dates = set(rows.loc[rows["restaurant"] == DFLEX_RESTAURANT, "date"])
        weekdays = {
            (since + timedelta(days=offset)).isoformat()
            for offset in range(requested)
            if (since + timedelta(days=offset)).weekday() < 5
        }
        if not weekdays <= dflex_dates:
            raise ValueError(
                "D-Flex newest-three-post limit does not prove historical weekday coverage "
                f"(missing_weekdays={len(weekdays - dflex_dates)}); "
                "no data written (use --coop-only for an explicitly partial catch-up)"
            )
    return diagnostics


def merge_active_meals(active: pd.DataFrame, crawled: pd.DataFrame) -> pd.DataFrame:
    """New rows win by (date, restaurant); absent active rows remain active."""
    current = _meal_rows(active)
    incoming = _meal_rows(crawled)
    merged = pd.concat([current, incoming], ignore_index=True)
    merged.drop_duplicates(subset=["date", "restaurant"], keep="last", inplace=True)
    return merged.sort_values(["date", "restaurant"]).reset_index(drop=True)


def catch_up(since: date, today: date, *, apply: bool, coop_only: bool, delay: float) -> dict:
    if since > today:
        raise ValueError("--since must not be after today")
    frame = crawl_meals(
        days_back=(today - since).days,
        days_ahead=0,
        today=today,
        delay=delay,
        include_dflex=not coop_only,
    )
    diagnostics = validate_crawl(frame, since, today, coop_only=coop_only)
    # Read as late as possible. The ingest below receives one complete frame;
    # split ingests would hide each other's dates in store_meals_in_db().
    active = load_meals_from_db()
    merged = merge_active_meals(active, frame)
    summary = {
        "since": since.isoformat(),
        "through": today.isoformat(),
        "source": "co-op only" if coop_only else "co-op and D-Flex",
        "requested_days": diagnostics["requested_days"],
        "fetched_days": diagnostics["fetched_days"],
        "fetch_failed_days": diagnostics["fetch_failed_days"],
        "parse_empty_days": diagnostics["parse_empty_days"],
        "crawled_rows": len(frame),
        "preserved_active_rows": len(merged) - len(_meal_rows(frame)),
        "merged_rows": len(merged),
        "mode": "apply" if apply else "dry-run",
    }
    if apply:
        chunks, _, _ = ingest_meals(merged)
        if chunks.empty:
            raise RuntimeError("Meals ingest produced no chunks")
        summary["indexed_chunks"] = len(chunks)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=date.fromisoformat, help="inclusive YYYY-MM-DD; default: last full success")
    parser.add_argument("--apply", action="store_true", help="persist canonical rows and rebuild meals index")
    parser.add_argument("--coop-only", action="store_true", help="exclude D-Flex and report a partial catch-up")
    parser.add_argument("--delay", type=float, default=0.4, help="seconds between requests")
    args = parser.parse_args()
    if args.delay < 0:
        parser.error("--delay must be nonnegative")
    try:
        if not DATABASE_FILE.is_file():
            raise ValueError("RAG database file does not exist")
        since = args.since or last_success_date()
        summary = catch_up(
            since,
            datetime.now(KST).date(),
            apply=args.apply,
            coop_only=args.coop_only,
            delay=args.delay,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"Catch-up blocked: {exc}", file=sys.stderr)
        return 1
    print(" ".join(f"{key}={value}" for key, value in summary.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
