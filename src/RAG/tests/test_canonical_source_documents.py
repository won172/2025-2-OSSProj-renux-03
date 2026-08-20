from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src import database as db
from src.pipelines import ingest


def test_static_projection_is_backfilled_and_reloaded_from_source_documents(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", factory)
    monkeypatch.setattr(ingest, "engine", engine)
    monkeypatch.setattr(ingest, "SessionLocal", factory)
    db.Base.metadata.create_all(bind=engine)

    session = factory()
    session.add(db.Schedule(
        title="개강",
        start_date="2026-09-01",
        end_date="2026-09-01",
        category="학사",
        department="교무",
        content="개강",
    ))
    session.add(db.Course(
        course_code="C1",
        title="자료구조",
        description="자료 구조 기초",
        source_table="catalog",
        raw_data=json.dumps({
            "department_name": "컴퓨터학과",
            "course_name": "자료구조",
            "credit": "3",
            "record_type": "table_row",
        }, ensure_ascii=False),
    ))
    session.commit()

    summary = ingest.backfill_static_source_documents(("schedule", "courses"))
    assert summary == {"schedule": 1, "courses": 1}

    schedule_frame = ingest.load_canonical_source_frame(session, "schedule")
    course_frame = ingest.load_canonical_source_frame(session, "courses")
    assert schedule_frame.iloc[0]["title"] == "개강"
    assert course_frame.iloc[0]["course_code"] == "C1"
    assert session.query(db.SourceDocument).count() == 2
    session.close()
