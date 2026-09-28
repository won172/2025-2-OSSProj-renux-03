"""Catch up official meal menus from the last successful run through today.

Dry run by default. Use --apply only after reviewing the coverage summary.
The D-Flex catch-up path pages through official weekly PDFs; --coop-only
explicitly limits the run to the co-op HTML source.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.crawlers.dongguk_meals import (
    DFLEX_RESTAURANT,
    KST,
    crawl_dflex_meals_range,
    crawl_meals,
)
from src.database import DATABASE_FILE, IngestionRun, SessionLocal, kst_now
from src.pipelines.ingest import ingest_meals, load_meals_from_db

MEAL_COLUMNS = ("date", "weekday", "restaurant", "menu_text", "is_closed")


def _start_run() -> int:
    """A catch-up must not write canonical data without a durable run record."""
    session = SessionLocal()
    try:
        run = IngestionRun(dataset="meals", status="running", started_at=kst_now())
        session.add(run)
        session.commit()
        return int(run.id)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _finish_run(
    run_id: int,
    *,
    status: str,
    outcome_code: str,
    diagnostics: dict,
    seen: int = 0,
    failed: int = 0,
) -> None:
    session = SessionLocal()
    try:
        run = session.get(IngestionRun, run_id)
        if run is None:
            raise RuntimeError("Meals ingestion run disappeared")
        run.status = status
        run.finished_at = kst_now()
        run.documents_seen = seen
        run.documents_failed = failed
        run.outcome_code = outcome_code
        run.diagnostics_json = json.dumps(diagnostics, sort_keys=True)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


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
        blank_dates = set(diagnostics.get("dflex_blank_dates") or [])
        weekdays = {
            (since + timedelta(days=offset)).isoformat()
            for offset in range(requested)
            if (since + timedelta(days=offset)).weekday() < 5
        }
        if not weekdays <= dflex_dates | blank_dates:
            raise ValueError(
                "D-Flex historical retrieval does not prove weekday coverage "
                f"(missing_weekdays={len(weekdays - dflex_dates - blank_dates)}); "
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
        include_dflex=False,
    )
    validate_crawl(frame, since, today, coop_only=True)
    if not coop_only:
        try:
            dflex_result = crawl_dflex_meals_range(since, today, delay=delay)
        except Exception as exc:
            raise ValueError(f"D-Flex catch-up failed ({type(exc).__name__}); no data written") from exc
        source_diagnostics = dict(frame.attrs.get("crawl_diagnostics") or {})
        if dflex_result.records:
            frame = pd.concat([frame, pd.DataFrame(dflex_result.records)], ignore_index=True)
        frame.attrs["crawl_diagnostics"] = {
            **source_diagnostics,
            "dflex_record_count": len(dflex_result.records),
            "dflex_blank_dates": sorted(dflex_result.blank_dates),
        }
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
        "dflex_blank_days": len(diagnostics.get("dflex_blank_dates") or []),
        "crawled_rows": len(frame),
        "preserved_active_rows": len(merged) - len(_meal_rows(frame)),
        "merged_rows": len(merged),
        "mode": "apply" if apply else "dry-run",
    }
    if apply:
        run_diagnostics = {
            "since": since.isoformat(),
            "through": today.isoformat(),
            "source_scope": "coop_only" if coop_only else "coop_and_dflex",
            "requested_days": int(diagnostics["requested_days"]),
            "fetched_days": int(diagnostics["fetched_days"]),
            "fetch_failed_days": int(diagnostics["fetch_failed_days"]),
            "parsed_days_with_rows": int(diagnostics["parsed_days_with_rows"]),
            "parse_empty_days": int(diagnostics["parse_empty_days"]),
            "dflex_blank_dates": diagnostics.get("dflex_blank_dates") or [],
            "crawled_rows": len(frame),
            "preserved_active_rows": summary["preserved_active_rows"],
        }
        try:
            run_id = _start_run()
        except Exception as exc:
            raise RuntimeError(f"Cannot record meals run ({type(exc).__name__}); no data written") from exc
        try:
            chunks, _, _ = ingest_meals(merged)
            if chunks.empty:
                raise RuntimeError("Meals ingest produced no chunks")
        except Exception as exc:
            try:
                _finish_run(
                    run_id,
                    status="failed",
                    outcome_code="pipeline_failure",
                    diagnostics=run_diagnostics,
                    failed=1,
                )
            except Exception:
                pass  # The existing running row cannot be mistaken for success.
            raise RuntimeError(f"Meals ingest failed ({type(exc).__name__}); no success recorded") from exc
        status = "partial" if coop_only else "success"
        outcome_code = "partial_source" if coop_only else "success"
        try:
            _finish_run(
                run_id,
                status=status,
                outcome_code=outcome_code,
                diagnostics=run_diagnostics,
                seen=len(chunks),
            )
        except Exception as exc:
            raise RuntimeError(f"Meals run completion failed ({type(exc).__name__})") from exc
        summary["indexed_chunks"] = len(chunks)
        summary["run_status"] = status
        summary["outcome_code"] = outcome_code
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
