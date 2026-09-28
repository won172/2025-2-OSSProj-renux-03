from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scripts import catch_up_meals
from src.database import Base, IngestionRun, SourceDocument
from src.pipelines import ingest


def meal(day: date, restaurant: str = "상록원", menu: str = "메뉴") -> dict:
    return {
        "date": day.isoformat(),
        "weekday": "월",
        "restaurant": restaurant,
        "menu_text": menu,
        "is_closed": False,
    }


def crawl_frame(since: date, today: date, *, dflex: bool = False) -> pd.DataFrame:
    days = (today - since).days + 1
    rows = [meal(since + timedelta(days=offset)) for offset in range(days)]
    if dflex:
        rows += [
            meal(since + timedelta(days=offset), catch_up_meals.DFLEX_RESTAURANT)
            for offset in range(days)
            if (since + timedelta(days=offset)).weekday() < 5
        ]
    frame = pd.DataFrame(rows)
    frame.attrs["crawl_diagnostics"] = {
        "requested_days": days,
        "fetched_days": days,
        "fetch_failed_days": 0,
        "parsed_days_with_rows": days,
        "parse_empty_days": 0,
    }
    return frame


def test_catch_up_crawls_one_inclusive_window_and_ingests_one_merged_frame(monkeypatch):
    since, today = date(2026, 8, 29), date(2026, 9, 1)
    calls: list[dict] = []
    ingested: list[pd.DataFrame] = []
    frame = crawl_frame(since, today, dflex=True)

    def fake_crawl(**kwargs):
        calls.append(kwargs)
        return frame

    monkeypatch.setattr(catch_up_meals, "crawl_meals", fake_crawl)
    monkeypatch.setattr(
        catch_up_meals,
        "load_meals_from_db",
        lambda: pd.DataFrame([meal(date(2026, 8, 20), menu="기존")]),
    )
    monkeypatch.setattr(
        catch_up_meals,
        "ingest_meals",
        lambda rows: (ingested.append(rows.copy()) or pd.DataFrame([{"chunk": 1}]), None, None),
    )

    summary = catch_up_meals.catch_up(since, today, apply=True, coop_only=False, delay=0)

    assert len(calls) == 1
    assert calls[0]["days_back"] == 3
    assert calls[0]["days_ahead"] == 0
    assert calls[0]["today"] == today
    assert len(ingested) == 1
    assert len(ingested[0]) == len(frame) + 1
    assert summary["preserved_active_rows"] == 1


def test_catch_up_preserves_unavailable_old_rows_and_is_idempotent(monkeypatch):
    since, today = date(2026, 9, 1), date(2026, 9, 2)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(ingest, "SessionLocal", session_factory)
    ingest.store_meals_in_db(pd.DataFrame([
        meal(date(2026, 8, 20), menu="기존"),
        meal(since, restaurant="기존식당", menu="기존메뉴"),
    ]))
    active = ingest.load_meals_from_db()
    incoming = crawl_frame(since, today)
    monkeypatch.setattr(catch_up_meals, "crawl_meals", lambda **_kwargs: incoming)
    monkeypatch.setattr(catch_up_meals, "load_meals_from_db", ingest.load_meals_from_db)

    def persist_without_index(rows):
        ingest.store_meals_in_db(rows)
        return pd.DataFrame([{"chunk": 1}]), None, None

    monkeypatch.setattr(catch_up_meals, "ingest_meals", persist_without_index)
    first = catch_up_meals.catch_up(since, today, apply=True, coop_only=True, delay=0)
    second = catch_up_meals.catch_up(since, today, apply=True, coop_only=True, delay=0)
    final = ingest.load_meals_from_db()

    assert len(active) == 2
    assert first["merged_rows"] == second["merged_rows"] == 4
    assert len(final) == 4
    assert final.loc[final["date"] == "2026-08-20", "menu_text"].iloc[0] == "기존"
    assert final.loc[final["restaurant"] == "기존식당", "menu_text"].iloc[0] == "기존메뉴"
    with session_factory() as session:
        assert session.query(SourceDocument).filter_by(dataset="meals", status="active").count() == 4
        assert session.query(SourceDocument).filter_by(dataset="meals", status="hidden").count() == 0


def test_last_success_date_ignores_newer_partial_run(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        session.add_all([
            IngestionRun(dataset="meals", status="success", finished_at=datetime(2026, 8, 29)),
            IngestionRun(dataset="meals", status="partial_success", finished_at=datetime(2026, 9, 1)),
        ])
        session.commit()
    monkeypatch.setattr(catch_up_meals, "SessionLocal", session_factory)
    assert catch_up_meals.last_success_date() == date(2026, 8, 29)


@pytest.mark.parametrize("diagnostic, value", [
    ("fetch_failed_days", 1),
    ("parse_empty_days", 1),
    ("fetched_days", 0),
])
def test_catch_up_blocks_partial_coop_crawl_before_ingest(monkeypatch, diagnostic, value):
    day = date(2026, 9, 1)
    frame = crawl_frame(day, day)
    frame.attrs["crawl_diagnostics"][diagnostic] = value
    monkeypatch.setattr(catch_up_meals, "crawl_meals", lambda **_kwargs: frame)
    monkeypatch.setattr(catch_up_meals, "load_meals_from_db", lambda: pytest.fail("read after invalid crawl"))
    monkeypatch.setattr(catch_up_meals, "ingest_meals", lambda _rows: pytest.fail("wrote partial crawl"))
    with pytest.raises(ValueError, match="coverage incomplete"):
        catch_up_meals.catch_up(day, day, apply=True, coop_only=True, delay=0)


def test_catch_up_blocks_dflex_history_not_covered_by_three_newest_posts(monkeypatch):
    since, today = date(2026, 8, 31), date(2026, 9, 2)
    frame = crawl_frame(since, today)
    monkeypatch.setattr(catch_up_meals, "crawl_meals", lambda **_kwargs: frame)
    monkeypatch.setattr(catch_up_meals, "ingest_meals", lambda _rows: pytest.fail("wrote partial D-Flex"))
    with pytest.raises(ValueError, match="D-Flex newest-three-post limit"):
        catch_up_meals.catch_up(since, today, apply=True, coop_only=False, delay=0)


def test_catch_up_dry_run_never_writes(monkeypatch):
    day = date(2026, 9, 1)
    monkeypatch.setattr(catch_up_meals, "crawl_meals", lambda **_kwargs: crawl_frame(day, day))
    monkeypatch.setattr(catch_up_meals, "load_meals_from_db", lambda: pd.DataFrame())
    monkeypatch.setattr(catch_up_meals, "ingest_meals", lambda _rows: pytest.fail("dry-run wrote"))
    assert catch_up_meals.catch_up(day, day, apply=False, coop_only=True, delay=0)["mode"] == "dry-run"
