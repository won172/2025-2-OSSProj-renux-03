"""Read-only p50/p95/p99 report from query-log stage timings (milliseconds).

Cold/warm labels require a known, single-process startup window. Without
--process-start, requests remain unclassified because the query log has no
process identity or restart marker. No question, answer, or source text is read.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 2)


def summarize(rows, *, process_start: datetime | None = None, process_end: datetime | None = None):
    samples = defaultdict(lambda: defaultdict(list))
    seen_datasets: set[str] = set()
    request_count = 0
    for route_raw, timings_raw, created_raw in rows:
        try:
            created = datetime.fromisoformat(created_raw)
            route = json.loads(route_raw or "[]")
            timings = json.loads(timings_raw or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if process_start is not None and created < process_start:
            continue
        if process_end is not None and created >= process_end:
            continue
        if not isinstance(route, list) or not all(isinstance(item, str) for item in route):
            continue
        if not isinstance(timings, dict):
            continue
        request_count += 1
        if process_start is None or not route:
            temperature = "unknown"
        else:
            temperature = "cold" if any(item not in seen_datasets for item in route) else "warm"
            seen_datasets.update(route)
        route_label = json.dumps(route, ensure_ascii=False, separators=(",", ":"))
        for stage, value in timings.items():
            if isinstance(value, bool) or not isinstance(value, (float, int)):
                continue
            if not math.isfinite(value) or value < 0:
                continue
            for group in ("all_routes", route_label):
                samples[(temperature, group)][stage].append(float(value))

    groups = []
    for (temperature, route), stages in sorted(samples.items()):
        groups.append({
            "temperature": temperature,
            "route": route,
            "stages": {
                name: {"count": len(values), "p50_ms": percentile(values, 0.5),
                       "p95_ms": percentile(values, 0.95), "p99_ms": percentile(values, 0.99)}
                for name, values in sorted(stages.items())
            },
        })
    return {
        "requests": request_count,
        "cold_warm_basis": "assumed_single_process_window" if process_start else "unknown_no_start_marker",
        "groups": groups,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True, help="SQLite query-log database")
    parser.add_argument("--process-start", type=datetime.fromisoformat,
                        help="known startup timestamp in the query log's local time zone")
    parser.add_argument("--process-end", type=datetime.fromisoformat,
                        help="exclusive end of that single-process window")
    args = parser.parse_args()
    if args.process_end and not args.process_start:
        parser.error("--process-end requires --process-start")
    if args.process_end and args.process_end <= args.process_start:
        parser.error("--process-end must follow --process-start")
    if not args.database.is_file():
        parser.error("database does not exist")

    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT route, stage_timings_json, created_at FROM rag_query_logs ORDER BY created_at, id"
        )
        report = summarize(rows, process_start=args.process_start, process_end=args.process_end)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
