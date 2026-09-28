"""Request retrieval diagnostics and informational readiness contracts."""
import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api import rag_service as service
from src.database import Base, IngestionRun, RagQueryLog, RagRetrievalLog, TelemetryHeartbeat
from src.services.telemetry_heartbeat import TelemetryVerdict


def _sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'trace.db'}")
    Base.metadata.create_all(engine, tables=[
        IngestionRun.__table__, RagQueryLog.__table__, RagRetrievalLog.__table__,
        TelemetryHeartbeat.__table__,
    ])
    return engine, sessionmaker(bind=engine)


def test_modes_accumulate_across_datasets_queries_and_empty_results(monkeypatch):
    frame = pd.DataFrame([{
        "chunk_id": "rules:1", "document_key": "rules:1", "dataset": "rules",
        "chunk_text": "공식 규정", "title": "공식 규정",
        "url": "https://www.dongguk.edu/rules/1", "hybrid_score": 0.9,
        "dense_rank": 1, "sparse_rank": 2, "fusion_rank": 1,
        "corpus_revision": "rules:revision",
    }])
    monkeypatch.setattr(service, "_ensure_dataset", lambda _key: (frame, None, None, None))
    calls = []

    def search(**kwargs):
        dataset = next(
            key for key, artifacts in service.DATASET_ARTIFACTS.items()
            if artifacts.collection == kwargs["collection_name"]
        )
        calls.append(dataset)
        hit = frame.copy() if dataset == "rules" else frame.iloc[:0].copy()
        hit.attrs.update(
            retrieval_mode="hybrid" if dataset == "rules" else "sparse_degraded",
            dense_error_type=None if dataset == "rules" else "InternalError",
        )
        return hit

    monkeypatch.setattr(service, "hybrid_search_with_meta", search)
    token = service._retrieval_observations.set([])
    try:
        asyncio.run(service._retrieve_frames_for_queries(
            route=["rules", "courses"], queries=["첫 질문", "확장 질문"],
            final_where_filter={}, notice_board_filter=None, date_filter=None,
            entry_year=None, request_id="trace-test",
        ))
        assert len(calls) == 4
        assert service._retrieval_summary() == {
            "retrieval_mode": "sparse_degraded", "degraded_datasets": ["courses"],
        }
        completion = service._completion_payload(
            request_id="trace-test", grounded=None, grounding_score=None,
            suggested_questions=[], fallback_reason=None, sources=[],
        )
        assert completion["retrieval_mode"] == "sparse_degraded"
        assert completion["degraded_datasets"] == ["courses"]
    finally:
        service._retrieval_observations.reset(token)


def test_query_log_persists_trace_without_exposing_it_in_sources(tmp_path, monkeypatch):
    engine, sessions = _sessions(tmp_path)
    monkeypatch.setattr(service, "SessionLocal", sessions)
    row = pd.Series({
        "dataset": "rules", "chunk_id": "rules:1", "chunk_text": "official",
        "title": "Official rule", "url": "https://www.dongguk.edu/rules/1",
        "citation_number": 1, "dense_rank": 2, "sparse_rank": 1,
        "fusion_rank": 1, "corpus_revision": "rules:revision",
        "dense_distance_raw": 0.2, "dense_similarity_raw": 0.8,
        "sparse_score_raw": 3.5,
    })
    source = service._source_chunk_from_row(row)
    for key in service._SOURCE_TRACE_FIELDS:
        assert key not in source.metadata
        assert key not in source.model_dump()
    token = service._retrieval_observations.set([{
        "dataset": "rules", "retrieval_mode": "sparse_degraded",
        "dense_error_type": "InternalError",
    }])
    try:
        save_args = (
            "trace-request", "session", "question", "question", ["rules"],
            "answer", False, None, False, False, None, None, None, None,
            False, None, False, False, None, 0.8, [source],
            {"total": 1.0}, [],
        )
        asyncio.run(service.run_in_threadpool(
            service._save_rag_evaluation_log, *save_args,
            source_traces=service._source_traces(pd.DataFrame([row])),
        ))
        service._update_observability_log("trace-request", {"total": 2.0}, [])
        with sessions() as session:
            logged = session.query(RagQueryLog).one()
            assert json.loads(logged.stage_timings_json)["total"] == 2.0
            retrieval = json.loads(logged.stage_timings_json)["retrieval"]
            assert retrieval["retrieval_mode"] == "sparse_degraded"
            assert retrieval["degraded_datasets"] == ["rules"]
            assert retrieval["searches"] == [{
                "dataset": "rules", "retrieval_mode": "sparse_degraded",
                "dense_error_type": "InternalError",
            }]
            assert retrieval["sources"] == [{
                "rank": 1, "dataset": "rules", "chunk_id": "rules:1",
                "dense_rank": 2, "sparse_rank": 1, "fusion_rank": 1,
                "corpus_revision": "rules:revision", "dense_distance_raw": 0.2,
                "dense_similarity_raw": 0.8, "sparse_score_raw": 3.5,
            }]
            assert session.query(RagRetrievalLog).one().chunk_id == "rules:1"
    finally:
        service._retrieval_observations.reset(token)
        engine.dispose()


def test_ready_adds_heartbeat_revision_ingestion_and_hints_without_changing_status(tmp_path, monkeypatch):
    engine, sessions = _sessions(tmp_path)
    monkeypatch.setattr(service, "SessionLocal", sessions)
    service._reset_startup_readiness()
    with sessions() as session:
        session.add(IngestionRun(
            dataset="rules", status="success", started_at=datetime(2026, 9, 27, 9),
            finished_at=datetime(2026, 9, 27, 10),
        ))
        session.commit()
    with service._datasets_lock:
        previous = service._datasets.get("rules")
        service._datasets["rules"] = SimpleNamespace(corpus_revision="rules:revision")
    try:
        for name, check in service._new_readiness_state()["checks"].items():
            if check["required"]:
                service._set_readiness_check(name, ready=True)
        service._set_startup_complete()
        monkeypatch.setattr(service, "read_verdict", lambda *_a, **_kw: TelemetryVerdict(
            "stale_heartbeat", datetime(2026, 9, 27, 0), None, 900.0,
        ))
        ready = service.ready()
        assert ready["status"] == "ready"
        heartbeat = ready["checks"]["telemetry_heartbeat"]
        assert heartbeat["required"] is False
        assert heartbeat["latest_heartbeat_age_seconds"] == 900.0
        assert heartbeat["stale"] is True
        assert heartbeat["query_log_stalled"] is False
        assert heartbeat["recovery_hint"]
        datasets = ready["checks"]["datasets"]
        assert datasets["corpus_revisions"]["rules"] == "rules:revision"
        assert datasets["last_successful_ingestion_at"]["rules"] == "2026-09-27T10:00:00"
        service._set_readiness_check("database", ready=False, detail="failed")
        failed = service.ready()
        assert failed.status_code == 503
        assert json.loads(failed.body)["checks"]["database"]["recovery_hint"]
        monkeypatch.setattr(service, "read_verdict", lambda *_a, **_kw: TelemetryVerdict(
            "no_traffic_or_logging_broken", datetime(2026, 9, 27, 0), None, 5.0,
        ))
        assert json.loads(service.ready().body)["checks"]["telemetry_heartbeat"]["query_log_stalled"] is True
    finally:
        service._reset_startup_readiness()
        with service._datasets_lock:
            if previous is None:
                service._datasets.pop("rules", None)
            else:
                service._datasets["rules"] = previous
        engine.dispose()
