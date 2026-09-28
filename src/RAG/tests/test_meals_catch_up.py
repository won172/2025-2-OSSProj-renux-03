from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scripts import catch_up_meals
from src.crawlers.dongguk_meals import DflexRangeResult
from src.database import Base, IngestionRun, SourceDocument
from src.pipelines import ingest
from src.services.ingestion_freshness import build_ingestion_freshness_report


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
    finished_runs: list[dict] = []
    frame = crawl_frame(since, today)
    dflex_rows = [
        meal(since + timedelta(days=offset), catch_up_meals.DFLEX_RESTAURANT)
        for offset in range((today - since).days + 1)
        if (since + timedelta(days=offset)).weekday() < 5
    ]

    def fake_crawl(**kwargs):
        calls.append(kwargs)
        return frame

    monkeypatch.setattr(catch_up_meals, "crawl_meals", fake_crawl)
    monkeypatch.setattr(catch_up_meals, "crawl_dflex_meals_range", lambda *_args, **_kwargs: DflexRangeResult(
        dflex_rows, frozenset(row["date"] for row in dflex_rows), frozenset(),
    ))
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
    monkeypatch.setattr(catch_up_meals, "_start_run", lambda: 42)
    monkeypatch.setattr(catch_up_meals, "_finish_run", lambda _id, **kwargs: finished_runs.append(kwargs))

    summary = catch_up_meals.catch_up(since, today, apply=True, coop_only=False, delay=0)

    assert len(calls) == 1
    assert calls[0]["days_back"] == 3
    assert calls[0]["days_ahead"] == 0
    assert calls[0]["today"] == today
    assert calls[0]["include_dflex"] is False
    assert len(ingested) == 1
    assert len(ingested[0]) == len(frame) + len(dflex_rows) + 1
    assert summary["preserved_active_rows"] == 1
    assert summary["run_status"] == "success"
    assert finished_runs[0]["status"] == "success"
    assert finished_runs[0]["outcome_code"] == "success"


def test_catch_up_preserves_unavailable_old_rows_and_is_idempotent(monkeypatch):
    since, today = date(2026, 9, 1), date(2026, 9, 2)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(ingest, "SessionLocal", session_factory)
    monkeypatch.setattr(catch_up_meals, "SessionLocal", session_factory)
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
        runs = session.query(IngestionRun).order_by(IngestionRun.id).all()
        assert [run.status for run in runs] == ["partial", "partial"]
        assert all(run.outcome_code == "partial_source" for run in runs)
        assert all(json.loads(run.diagnostics_json)["source_scope"] == "coop_only" for run in runs)


def test_coop_only_run_is_warning_even_with_recent_canonical_rows():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    now = datetime(2026, 9, 21, 12, tzinfo=timezone(timedelta(hours=9)))
    with session_factory() as session:
        session.add(SourceDocument(
            dataset="meals", source_type="html_meal", source_id="one",
            document_key="meals:one", status="active", collected_at=now,
        ))
        session.add(IngestionRun(
            dataset="meals", status="partial", outcome_code="partial_source",
            started_at=now, finished_at=now,
        ))
        session.commit()
        report = build_ingestion_freshness_report(session, datasets=("meals",), now=now)
    assert report["gate_passed"] is False
    assert report["warning_datasets"] == ["meals"]
    assert report["datasets"][0]["latest_run"]["outcome_code"] == "partial_source"


def test_full_source_apply_persists_success_run(monkeypatch):
    day = date(2026, 9, 1)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(catch_up_meals, "SessionLocal", session_factory)
    monkeypatch.setattr(catch_up_meals, "crawl_meals", lambda **_kwargs: crawl_frame(day, day))
    monkeypatch.setattr(catch_up_meals, "crawl_dflex_meals_range", lambda *_args, **_kwargs: DflexRangeResult(
        [meal(day, catch_up_meals.DFLEX_RESTAURANT)], frozenset({day.isoformat()}), frozenset(),
    ))
    monkeypatch.setattr(catch_up_meals, "load_meals_from_db", lambda: pd.DataFrame())
    monkeypatch.setattr(catch_up_meals, "ingest_meals", lambda _rows: (pd.DataFrame([{"chunk": 1}]), None, None))
    summary = catch_up_meals.catch_up(day, day, apply=True, coop_only=False, delay=0)
    with session_factory() as session:
        run = session.query(IngestionRun).one()
        assert run.status == "success"
        assert run.outcome_code == "success"
        assert run.documents_seen == 1
        assert json.loads(run.diagnostics_json)["source_scope"] == "coop_and_dflex"
    assert summary["run_status"] == "success"


def test_blank_published_dflex_day_is_covered_without_inventing_meal(monkeypatch):
    since, today = date(2026, 9, 24), date(2026, 9, 25)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    ingested: list[pd.DataFrame] = []
    monkeypatch.setattr(catch_up_meals, "SessionLocal", session_factory)
    monkeypatch.setattr(catch_up_meals, "crawl_meals", lambda **_kwargs: crawl_frame(since, today))
    monkeypatch.setattr(catch_up_meals, "crawl_dflex_meals_range", lambda *_args, **_kwargs: DflexRangeResult(
        [meal(since, catch_up_meals.DFLEX_RESTAURANT)],
        frozenset({since.isoformat(), today.isoformat()}),
        frozenset({today.isoformat()}),
    ))
    monkeypatch.setattr(catch_up_meals, "load_meals_from_db", lambda: pd.DataFrame())
    monkeypatch.setattr(catch_up_meals, "ingest_meals", lambda rows: (
        ingested.append(rows.copy()) or pd.DataFrame([{"chunk": 1}]), None, None,
    ))
    summary = catch_up_meals.catch_up(since, today, apply=True, coop_only=False, delay=0)
    assert summary["run_status"] == "success"
    assert summary["dflex_blank_days"] == 1
    assert ingested[0].loc[ingested[0]["restaurant"] == catch_up_meals.DFLEX_RESTAURANT, "date"].tolist() == [since.isoformat()]
    with session_factory() as session:
        run = session.query(IngestionRun).one()
        assert json.loads(run.diagnostics_json)["dflex_blank_dates"] == [today.isoformat()]


def test_ingest_failure_records_failed_run(monkeypatch):
    day = date(2026, 9, 1)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(catch_up_meals, "SessionLocal", session_factory)
    monkeypatch.setattr(catch_up_meals, "crawl_meals", lambda **_kwargs: crawl_frame(day, day))
    monkeypatch.setattr(catch_up_meals, "load_meals_from_db", lambda: pd.DataFrame())
    monkeypatch.setattr(catch_up_meals, "ingest_meals", lambda _rows: (_ for _ in ()).throw(RuntimeError("sensitive menu")))
    with pytest.raises(RuntimeError, match="no success recorded") as error:
        catch_up_meals.catch_up(day, day, apply=True, coop_only=True, delay=0)
    assert "sensitive menu" not in str(error.value)
    with session_factory() as session:
        run = session.query(IngestionRun).one()
        assert run.status == "failed"
        assert run.outcome_code == "pipeline_failure"


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


def test_catch_up_blocks_missing_dflex_history(monkeypatch):
    since, today = date(2026, 8, 31), date(2026, 9, 2)
    frame = crawl_frame(since, today)
    monkeypatch.setattr(catch_up_meals, "crawl_meals", lambda **_kwargs: frame)
    monkeypatch.setattr(catch_up_meals, "crawl_dflex_meals_range", lambda *_args, **_kwargs: DflexRangeResult(
        [], frozenset(), frozenset(),
    ))
    monkeypatch.setattr(catch_up_meals, "ingest_meals", lambda _rows: pytest.fail("wrote partial D-Flex"))
    with pytest.raises(ValueError, match="D-Flex historical retrieval"):
        catch_up_meals.catch_up(since, today, apply=True, coop_only=False, delay=0)


def test_catch_up_dry_run_never_writes(monkeypatch):
    day = date(2026, 9, 1)
    monkeypatch.setattr(catch_up_meals, "crawl_meals", lambda **_kwargs: crawl_frame(day, day))
    monkeypatch.setattr(catch_up_meals, "load_meals_from_db", lambda: pd.DataFrame())
    monkeypatch.setattr(catch_up_meals, "ingest_meals", lambda _rows: pytest.fail("dry-run wrote"))
    assert catch_up_meals.catch_up(day, day, apply=False, coop_only=True, delay=0)["mode"] == "dry-run"
