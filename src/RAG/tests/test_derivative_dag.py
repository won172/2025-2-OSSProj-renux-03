from __future__ import annotations

import json

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base, IngestionRun, SourceSchemaFingerprint
from src.services import derivative_dag, scheduler
from src.services.source_schema import fingerprint_dataframe


def _factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_lineage_failure_stops_before_ontology(monkeypatch):
    called = False

    def rebuild(**_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(
        derivative_dag,
        "build_canonical_lineage_report",
        lambda **_kwargs: {"gate_passed": False, "violations": ["mismatch"]},
    )
    monkeypatch.setattr(derivative_dag, "rebuild_ontology_projection", rebuild)

    report = derivative_dag.run_post_ingestion_dag("schedule", ingestion_run_id=3)

    assert report["status"] == "failed"
    assert report["failed_stage"] == "canonical_lineage"
    assert called is False


def test_finish_run_persists_schema_observation_and_dag_result(monkeypatch):
    factory = _factory()
    monkeypatch.setattr(scheduler, "SessionLocal", factory)
    monkeypatch.setattr(
        derivative_dag,
        "run_post_ingestion_dag",
        lambda dataset, ingestion_run_id: {
            "status": "success",
            "stages": [
                {"name": "canonical_lineage", "status": "success"},
                {"name": "ontology", "status": "skipped"},
            ],
        },
    )
    run_id = scheduler._start_ingestion_run("meals")
    structure = fingerprint_dataframe(
        pd.DataFrame([{"menu": "비빔밥"}]),
        source_name="dining_api",
        source_format="json",
    )

    scheduler._finish_ingestion_run(
        run_id,
        status="success",
        seen=1,
        corpus_revision="meals:test",
        source_structures=[structure],
        run_derivatives=True,
    )

    with factory() as session:
        run = session.get(IngestionRun, run_id)
        diagnostics = json.loads(run.diagnostics_json)
        assert run.status == "success"
        assert diagnostics["source_schema"][0]["baseline_created"] is True
        assert diagnostics["derivative_dag"]["status"] == "success"
        observed = session.query(SourceSchemaFingerprint).one()
        assert observed.last_ingestion_run_id == run_id


def test_failed_derivative_marks_core_run_partial_but_keeps_revision(monkeypatch):
    factory = _factory()
    monkeypatch.setattr(scheduler, "SessionLocal", factory)
    monkeypatch.setattr(
        derivative_dag,
        "run_post_ingestion_dag",
        lambda dataset, ingestion_run_id: {
            "status": "failed",
            "failed_stage": "ontology",
            "stages": [],
        },
    )
    run_id = scheduler._start_ingestion_run("schedule")

    scheduler._finish_ingestion_run(
        run_id,
        status="success",
        seen=2,
        corpus_revision="schedule:test",
        run_derivatives=True,
    )

    with factory() as session:
        run = session.get(IngestionRun, run_id)
        assert run.status == "partial_success"
        assert run.outcome_code == "derivative_failure"
        assert run.corpus_revision == "schedule:test"
