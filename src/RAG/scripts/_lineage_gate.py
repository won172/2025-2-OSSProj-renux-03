"""Build-time canonical lineage gate shared by index build/rebuild scripts.

Audit 07 P0: "canonical lineage 검사를 artifact build와 배포 전 모두 필수로 한다".
Every build/rebuild CLI runs the strict lineage check
(`build_canonical_lineage_report`) for the datasets it touched and exits
non-zero when it fails, instead of leaving mismatched indexes published
silently.

Output is aggregate-only (dataset names, metric names, counts, error types).
It never prints titles, payloads, chunk text, or queries.

`--skip-lineage-gate` is an explicit emergency opt-out.  It prints a loud
warning and the returned summary records `status="skipped"` so the skip is
visible in logs and JSON outputs.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pipelines.ingest import DATASET_ARTIFACTS  # noqa: E402
from src.services.canonical_lineage import build_canonical_lineage_report  # noqa: E402

RUNBOOK = "src/RAG/docs/canonical-lineage-runbook.md"
SKIP_FLAG = "--skip-lineage-gate"

# phase -> meaning of a failure for the operator
PRE_PUBLISH = "pre_publish"  # nothing was published/activated yet
POST_PUBLISH = "post_publish"  # indexes are already live

SnapshotLoader = Callable[[str], tuple[list[str], list[dict[str, Any] | None]]]


def add_skip_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        SKIP_FLAG,
        dest="skip_lineage_gate",
        action="store_true",
        help=(
            "EMERGENCY ONLY: skip the strict canonical lineage gate after the build. "
            "The skip is printed loudly and recorded in the output."
        ),
    )


def client_snapshot_loader(client_factory: Callable[[], Any]) -> SnapshotLoader:
    """Snapshot loader that reads a *physical* collection name from a Chroma client.

    Used for staged/isolated builds, where the collection to check is not the
    pointer-resolved live collection.
    """
    holder: dict[str, Any] = {}

    def load(name: str) -> tuple[list[str], list[dict[str, Any] | None]]:
        if "client" not in holder:
            holder["client"] = client_factory()
        result = holder["client"].get_collection(name=name).get(include=["metadatas"], limit=None)
        ids = [str(value).strip() for value in (result.get("ids") or [])]
        return ids, list(result.get("metadatas") or [])

    return load


def summarize_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce a lineage report to aggregate-only fields."""
    datasets = []
    for item in report.get("datasets", []) or []:
        error = item.get("error") or None
        datasets.append(
            {
                "dataset": str(item.get("dataset")),
                "gate_passed": bool(item.get("gate_passed")),
                "violations": [
                    {"metric": str(v.get("metric")), "count": int(v.get("count", 0))}
                    for v in item.get("violations", []) or []
                ],
                "error_type": str(error.get("type")) if isinstance(error, Mapping) else None,
            }
        )
    return {"gate_passed": bool(report.get("gate_passed")), "datasets": datasets}


def run_lineage_gate(
    datasets: Iterable[str],
    *,
    skip: bool,
    phase: str,
    stage: str,
    artifacts: Mapping[str, Any] | None = None,
    collection_snapshot_loader: SnapshotLoader | None = None,
    stream: TextIO | None = None,
) -> dict[str, Any]:
    """Run the strict lineage gate for `datasets`.

    Returns a summary dict with `status` (passed|failed|skipped|not_applicable)
    and `exit_code` (0 or 1).  Callers must propagate a non-zero exit code.
    """
    out = stream or sys.stderr
    selected = list(dict.fromkeys(str(key) for key in datasets))
    base = {"stage": stage, "phase": phase, "datasets_checked": selected}

    if skip:
        banner = "!" * 72
        print(banner, file=out)
        print(
            f"!! WARNING: canonical lineage gate SKIPPED ({SKIP_FLAG}) at {stage}.",
            file=out,
        )
        print(
            "!! Indexes may not match canonical SourceDocument. Run "
            "`scripts/report_canonical_lineage.py --mode strict` before release.",
            file=out,
        )
        print(banner, file=out)
        return {**base, "status": "skipped", "exit_code": 0}

    if not selected:
        print(f"[lineage-gate] {stage}: no datasets touched; gate not applicable.", file=out)
        return {**base, "status": "not_applicable", "exit_code": 0}

    artifact_map = (
        {key: artifacts[key] for key in selected}
        if artifacts is not None
        else {key: DATASET_ARTIFACTS[key] for key in selected}
    )
    report = build_canonical_lineage_report(
        artifacts=artifact_map,
        collection_snapshot_loader=collection_snapshot_loader,
    )
    summary = summarize_report(report)
    if summary["gate_passed"]:
        print(f"[lineage-gate] PASSED at {stage}: {', '.join(selected)}", file=out)
        return {**base, "status": "passed", "exit_code": 0, "report": summary}

    print(f"[lineage-gate] FAILED at {stage}:", file=out)
    for item in summary["datasets"]:
        if item["gate_passed"]:
            continue
        metrics = ", ".join(f"{v['metric']}={v['count']}" for v in item["violations"])
        error = f" error_type={item['error_type']}" if item["error_type"] else ""
        print(f"  - {item['dataset']}: {metrics}{error}", file=out)
    if phase == PRE_PUBLISH:
        print(
            "[lineage-gate] Nothing was published/activated. Fix the canonical "
            f"source or artifact and rebuild (see {RUNBOOK}).",
            file=out,
        )
    else:
        print(
            "[lineage-gate] Indexes were PUBLISHED but lineage FAILED — roll back "
            f"per {RUNBOOK}.",
            file=out,
        )
    return {**base, "status": "failed", "exit_code": 1, "report": summary}


__all__ = [
    "POST_PUBLISH",
    "PRE_PUBLISH",
    "SKIP_FLAG",
    "add_skip_argument",
    "client_snapshot_loader",
    "run_lineage_gate",
    "summarize_report",
]
