"""Offline stage latency aggregation reads only timing and route columns."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from scripts.report_stage_latency import summarize


def test_stage_report_separates_known_startup_window_and_routes():
    rows = [
        ('["rules"]', '{"retrieval_and_fusion": 100, "total": 200}', '2026-09-28 10:00:00'),
        ('["rules","courses"]', '{"retrieval_and_fusion": 80, "total": 180}', '2026-09-28 10:00:01'),
        ('["rules","courses"]', '{"retrieval_and_fusion": 60, "total": 160}', '2026-09-28 10:00:02'),
        ('["rules","courses"]', '{"retrieval_and_fusion": 40, "total": 140, "retrieval": {"searches": []}}', '2026-09-28 10:00:03'),
    ]
    report = summarize(rows, process_start=datetime(2026, 9, 28, 10))
    assert report["requests"] == 4
    assert report["cold_warm_basis"] == "assumed_single_process_window"
    by_group = {(item["temperature"], item["route"]): item["stages"] for item in report["groups"]}
    assert by_group[("cold", "all_routes")]["retrieval_and_fusion"]["count"] == 2
    assert by_group[("warm", '["rules","courses"]')]["retrieval_and_fusion"] == {
        "count": 2, "p50_ms": 50.0, "p95_ms": 59.0, "p99_ms": 59.8,
    }
    assert "retrieval" not in by_group[("warm", "all_routes")]
    unknown = summarize(rows)
    assert {item["temperature"] for item in unknown["groups"]} == {"unknown"}


def test_stage_report_cli_omits_raw_question_and_keeps_database_unchanged(tmp_path):
    database = tmp_path / "query_logs.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE rag_query_logs (id INTEGER, route TEXT, stage_timings_json TEXT, "
            "created_at TEXT, question TEXT)"
        )
        connection.execute(
            "INSERT INTO rag_query_logs VALUES (?, ?, ?, ?, ?)",
            (1, '["rules"]', '{"total":123.0}', '2026-09-28 10:00:00', 'PRIVATE QUESTION'),
        )
    before = database.read_bytes()
    script = Path(__file__).resolve().parents[1] / "scripts" / "report_stage_latency.py"
    result = subprocess.run(
        [sys.executable, str(script), "--database", str(database)],
        check=True, capture_output=True, text=True,
    )
    assert "PRIVATE QUESTION" not in result.stdout
    assert json.loads(result.stdout)["groups"][0]["stages"]["total"]["p95_ms"] == 123.0
    assert database.read_bytes() == before
