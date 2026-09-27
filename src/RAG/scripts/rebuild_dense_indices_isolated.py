#!/usr/bin/env python3
"""Build/verify six RAG dense datasets in an explicit isolated Chroma path.

After a verified `build` or `verify`, the strict canonical lineage gate runs
against the *isolated* collections (SourceDocument vs configured Parquet vs the
staged Chroma store).  This script never activates anything, so a failure means
"do not promote this staged store"; the exit code is 1.  `--skip-lineage-gate`
is an emergency-only opt-out and is recorded in the JSON output.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pipelines.ingest import DATASET_ARTIFACTS  # noqa: E402
from src.services.staged_dense_rebuild import (  # noqa: E402
    DATASETS,
    GracefulStop,
    MAX_BATCH_SIZE,
    MIN_BATCH_SIZE,
    PathChromaDenseStore,
    _configured_artifact_path,
    build_staged_datasets,
    staged_build_status,
    validate_isolated_chroma_dir,
    verify_staged_datasets,
)
from scripts import _lineage_gate  # noqa: E402


def _staged_lineage_inputs(chroma_dir: Path, datasets: list[str]):
    """Artifacts + snapshot loader that inspect the isolated store, not the live one."""
    artifacts = {
        dataset: SimpleNamespace(
            chunk_path=_configured_artifact_path(dataset),
            collection=DATASET_ARTIFACTS[dataset].collection,
        )
        for dataset in datasets
    }
    target = validate_isolated_chroma_dir(chroma_dir)
    loader = _lineage_gate.client_snapshot_loader(lambda: PathChromaDenseStore(target).client)
    return artifacts, loader


def _lineage_gate_for(*, chroma_dir: Path, selection: str, skip: bool, stage: str) -> dict:
    datasets = list(DATASETS) if selection == "all" else [selection]
    if skip:
        return _lineage_gate.run_lineage_gate(
            datasets, skip=True, phase=_lineage_gate.PRE_PUBLISH, stage=stage
        )
    artifacts, loader = _staged_lineage_inputs(chroma_dir, datasets)
    return _lineage_gate.run_lineage_gate(
        datasets,
        skip=False,
        phase=_lineage_gate.PRE_PUBLISH,
        stage=stage,
        artifacts=artifacts,
        collection_snapshot_loader=loader,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chroma-dir",
        type=Path,
        required=True,
        help="New isolated Chroma path; live/corrupt/artifacts paths are rejected",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build/resume and verify without activation")
    build.add_argument("--dataset", required=True, choices=(*DATASETS, "all"))
    _lineage_gate.add_skip_argument(build)
    build.add_argument(
        "--batch-size",
        type=int,
        default=MIN_BATCH_SIZE,
        choices=range(MIN_BATCH_SIZE, MAX_BATCH_SIZE + 1),
        metavar=f"{MIN_BATCH_SIZE}..{MAX_BATCH_SIZE}",
    )

    verify = subparsers.add_parser("verify", help="Verify source/IDs/count/dimension/searches")
    verify.add_argument("--dataset", required=True, choices=(*DATASETS, "all"))
    _lineage_gate.add_skip_argument(verify)

    subparsers.add_parser("status", help="Read checkpoint state without opening Chroma")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        if args.command == "status":
            result = staged_build_status(args.chroma_dir)
        elif args.command == "verify":
            result = verify_staged_datasets(
                chroma_dir=args.chroma_dir,
                selection=args.dataset,
            )
        else:
            with GracefulStop() as stop:
                result = build_staged_datasets(
                    chroma_dir=args.chroma_dir,
                    selection=args.dataset,
                    batch_size=args.batch_size,
                    should_stop=lambda: stop.requested,
                )
        gate = None
        if args.command in {"build", "verify"} and result.get("status") == "verified":
            gate = _lineage_gate_for(
                chroma_dir=args.chroma_dir,
                selection=args.dataset,
                skip=args.skip_lineage_gate,
                stage=f"rebuild_dense_indices_isolated {args.command} (staged, not activated)",
            )
            result = {**result, "lineage_gate": gate}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if gate is not None and gate["exit_code"]:
            return int(gate["exit_code"])
        if result.get("status") == "paused":
            return 75
        if result.get("status") == "failed":
            return 1
        return 0
    except Exception as exc:
        logging.error("%s: %s", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
