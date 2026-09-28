"""Report aggregate telemetry; --alert enables an external all-replicas-down check."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import (  # noqa: E402
    RAG_TELEMETRY_EXPECTED_TRAFFIC_END_HOUR,
    RAG_TELEMETRY_EXPECTED_TRAFFIC_START_HOUR,
    RAG_TELEMETRY_HEARTBEAT_INTERVAL_SECONDS,
    RAG_TELEMETRY_HEARTBEAT_STALE_INTERVALS,
    RAG_TELEMETRY_NO_TRAFFIC_SECONDS,
)
from src.database import SessionLocal  # noqa: E402
from src.services.telemetry_heartbeat import latest_heartbeats, read_verdict  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10, help="number of recent heartbeat rows")
    parser.add_argument("--alert", action="store_true", help="send non-healthy verdict to the configured webhook")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")

    session = SessionLocal()
    try:
        verdict = read_verdict(
            session,
            interval_seconds=RAG_TELEMETRY_HEARTBEAT_INTERVAL_SECONDS,
            stale_intervals=RAG_TELEMETRY_HEARTBEAT_STALE_INTERVALS,
            no_traffic_seconds=RAG_TELEMETRY_NO_TRAFFIC_SECONDS,
            expected_start_hour=RAG_TELEMETRY_EXPECTED_TRAFFIC_START_HOUR,
            expected_end_hour=RAG_TELEMETRY_EXPECTED_TRAFFIC_END_HOUR,
        )
        rows = latest_heartbeats(session, args.limit)
    finally:
        session.close()

    print(json.dumps({
        "verdict": verdict.status,
        "latest_heartbeat_at_utc": verdict.latest_heartbeat_at.isoformat() if verdict.latest_heartbeat_at else None,
        "latest_query_log_at_utc": verdict.latest_query_log_at.isoformat() if verdict.latest_query_log_at else None,
        "heartbeat_age_seconds": verdict.age_seconds,
        "heartbeats": [
            {
                "host_id": row.host_id,
                "process_id": row.process_id,
                "recorded_at_utc": row.recorded_at.isoformat(),
                "interval_started_at_utc": row.interval_started_at.isoformat(),
                "queries_logged": row.queries_logged,
                "latest_query_log_at_utc": row.latest_query_log_at.isoformat() if row.latest_query_log_at else None,
                "scheduler_alive": row.scheduler_alive,
            }
            for row in rows
        ],
    }, ensure_ascii=False, indent=2))
    if args.alert and verdict.status != "healthy":
        from src.services.scheduler import send_telemetry_alert

        send_telemetry_alert(verdict.status, verdict)
    return {"healthy": 0, "no_traffic_or_logging_broken": 1}.get(verdict.status, 2)


if __name__ == "__main__":
    raise SystemExit(main())
