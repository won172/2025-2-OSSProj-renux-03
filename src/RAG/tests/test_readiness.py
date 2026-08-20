from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import rag_service  # noqa: E402


@pytest.fixture(autouse=True)
def reset_readiness_state():
    rag_service._reset_startup_readiness()
    yield
    rag_service._reset_startup_readiness()


def _successful_dataset(key: str):
    chunks = pd.DataFrame({"chunk_id": [f"{key}-1"]})
    return chunks, object(), object(), chunks["chunk_id"].tolist()


def _prepare_successful_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rag_service, "_validate_required_configuration", lambda: "ok")
    monkeypatch.setattr(rag_service, "init_db", lambda: None)
    monkeypatch.setattr(rag_service, "verify_database_writable", lambda: None)
    monkeypatch.setattr(rag_service, "_ensure_dataset", _successful_dataset)
    monkeypatch.setattr(rag_service, "count_items", lambda _collection: 1)
    monkeypatch.setattr(rag_service, "get_embedder", object)


def _response_payload(response: JSONResponse) -> dict:
    return json.loads(response.body.decode("utf-8"))


def test_health_is_live_while_ready_is_503_before_startup():
    assert rag_service.health() == {"status": "ok"}

    response = rag_service.ready()

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    payload = _response_payload(response)
    assert payload["status"] == "not_ready"
    assert payload["startup_complete"] is False


def test_ready_succeeds_after_all_required_components_load(monkeypatch: pytest.MonkeyPatch):
    _prepare_successful_checks(monkeypatch)

    rag_service._run_required_startup_checks()
    payload = rag_service.ready()

    assert isinstance(payload, dict)
    assert payload["status"] == "ready"
    assert payload["startup_complete"] is True
    assert payload["checks"]["database"]["ready"] is True
    assert payload["checks"]["datasets"]["counts"] == {
        key: 1 for key in rag_service._REQUIRED_DATASETS
    }
    assert payload["checks"]["datasets"]["dense_errors"] == {}
    assert payload["checks"]["embedder"]["ready"] is True
    assert payload["checks"]["scheduler"]["required"] is False


def test_scheduler_failure_does_not_change_ready_result(monkeypatch: pytest.MonkeyPatch):
    _prepare_successful_checks(monkeypatch)
    rag_service._run_required_startup_checks()
    rag_service._set_readiness_check(
        "scheduler",
        ready=False,
        detail="failed",
        error={"code": "scheduler_start_failed"},
    )

    payload = rag_service.ready()

    assert isinstance(payload, dict)
    assert payload["status"] == "ready"
    assert payload["checks"]["scheduler"]["ready"] is False


def test_ready_reports_database_initialization_failure(monkeypatch: pytest.MonkeyPatch):
    _prepare_successful_checks(monkeypatch)

    def fail_database():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(rag_service, "init_db", fail_database)
    rag_service._run_required_startup_checks()
    response = rag_service.ready()

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    check = _response_payload(response)["checks"]["database"]
    assert check["ready"] is False
    assert check["error"]["code"] == "database_initialization_failed"


def test_ready_reports_database_readonly_failure(monkeypatch: pytest.MonkeyPatch):
    _prepare_successful_checks(monkeypatch)

    def fail_write_probe():
        raise RuntimeError("attempt to write a readonly database")

    monkeypatch.setattr(rag_service, "verify_database_writable", fail_write_probe)
    rag_service._run_required_startup_checks()
    response = rag_service.ready()

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    check = _response_payload(response)["checks"]["database"]
    assert check["ready"] is False
    assert check["error"]["code"] == "database_initialization_failed"


def test_ready_reports_required_dataset_failure(monkeypatch: pytest.MonkeyPatch):
    _prepare_successful_checks(monkeypatch)
    failed_dataset = rag_service._REQUIRED_DATASETS[0]

    def load_dataset(key: str):
        if key == failed_dataset:
            raise FileNotFoundError("dataset artifact missing")
        return _successful_dataset(key)

    monkeypatch.setattr(rag_service, "_ensure_dataset", load_dataset)
    rag_service._run_required_startup_checks()
    response = rag_service.ready()

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    check = _response_payload(response)["checks"]["datasets"]
    assert check["ready"] is False
    assert check["errors"][failed_dataset]["code"] == "required_dataset_warmup_failed"


def test_ready_exposes_sparse_degraded_dataset_without_hiding_service_availability(monkeypatch):
    _prepare_successful_checks(monkeypatch)
    monkeypatch.setattr(rag_service, "count_items", lambda _collection: 0)

    rag_service._run_required_startup_checks()
    payload = rag_service.ready()

    assert isinstance(payload, dict)
    assert payload["status"] == "ready"
    datasets = payload["checks"]["datasets"]
    assert datasets["detail"] == "loaded_degraded"
    assert set(datasets["dense_errors"]) == set(rag_service._REQUIRED_DATASETS)


def test_runtime_refresh_reloads_requested_cache_and_updates_ready_snapshot(monkeypatch: pytest.MonkeyPatch):
    loaded: list[str] = []

    def load_locked(key: str):
        loaded.append(key)
        chunks = pd.DataFrame({"chunk_id": [f"{key}-updated"]})
        return chunks, object(), object(), chunks["chunk_id"].tolist()

    monkeypatch.setattr(rag_service, "_ensure_dataset_locked", load_locked)
    monkeypatch.setattr(rag_service, "count_items", lambda _collection: 1)
    monkeypatch.setattr(
        rag_service,
        "_refresh_data_quality_readiness",
        lambda: {"gate_passed": True, "counts": {"category_unknown": 0}},
    )
    with rag_service._datasets_lock:
        rag_service._datasets["notices"] = object()

    snapshot = rag_service.refresh_runtime_dataset_state(["notices"])
    ready_snapshot = rag_service._readiness_snapshot()

    assert loaded == list(rag_service._REQUIRED_DATASETS)
    assert snapshot["targets"] == ["notices"]
    assert snapshot["counts"] == {key: 1 for key in rag_service._REQUIRED_DATASETS}
    assert ready_snapshot["checks"]["datasets"]["detail"] == "refreshed"
    assert ready_snapshot["checks"]["datasets"]["counts"] == snapshot["counts"]
    assert snapshot["data_quality"]["counts"]["category_unknown"] == 0


def test_ready_reports_embedder_failure(monkeypatch: pytest.MonkeyPatch):
    _prepare_successful_checks(monkeypatch)

    def fail_embedder():
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(rag_service, "get_embedder", fail_embedder)
    rag_service._run_required_startup_checks()
    response = rag_service.ready()

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    check = _response_payload(response)["checks"]["embedder"]
    assert check["ready"] is False
    assert check["error"]["code"] == "embedder_warmup_failed"


def test_데이터베이스_경로를_환경변수로_바꿀_수_있다(monkeypatch, tmp_path):
    """경로가 고정이면 개발자는 늘 자기 로컬 DB로만 테스트하게 된다.

    스케줄러 실행 기록을 추가했을 때 로컬 596건이 전부 통과하고 CI에서만 깨졌다 —
    로컬에는 ingestion_runs 테이블이 있었기 때문이다. 빈 DB를 재현할 수단이 필요하다.
    """
    import importlib

    목표 = tmp_path / "empty.db"
    monkeypatch.setenv("RAG_DATABASE_FILE", str(목표))
    import src.database as database

    다시읽기 = importlib.reload(database)
    try:
        assert 다시읽기.DATABASE_FILE == 목표
        assert str(목표) in 다시읽기.DATABASE_URL
    finally:
        monkeypatch.delenv("RAG_DATABASE_FILE", raising=False)
        importlib.reload(database)
