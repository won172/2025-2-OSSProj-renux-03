#!/usr/bin/env python3
"""Build, verify, activate, or roll back the notices dense index safely.

Canonical lineage gate (strict, notices only):
- `build`: after the staged collection is verified, lineage is checked against
  the staged *physical* collection and the checkpoint's source artifact.  The
  pointer is not touched by `build`; failure exits 1.
- `activate`: lineage is re-checked against the staged collection BEFORE the
  pointer switch; failure exits 1 and the pointer is left unchanged.
`--skip-lineage-gate` (build/activate) is an emergency-only opt-out; it prints a
loud warning and is recorded in the JSON output.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.notices_dense_rebuild import (  # noqa: E402
    GracefulStop,
    MAX_BATCH_SIZE,
    MIN_BATCH_SIZE,
    activate_notice_dense_build,
    build_notice_dense_index,
    load_checkpoint,
    load_notice_chunk_snapshot,
    rollback_notice_dense_pointer,
    verify_notice_dense_build,
)
from src.vectorstore.chroma_client import get_client  # noqa: E402
from src.vectorstore.collection_pointer import read_pointer_state  # noqa: E402
from scripts import _lineage_gate  # noqa: E402


def _build_snapshot_loader():
    """Read the staged physical collection directly (bypasses the logical pointer)."""
    return _lineage_gate.client_snapshot_loader(get_client)


def _staged_lineage_gate(
    *, build_id: str, checkpoint_dir: Path | None, skip: bool, stage: str
) -> dict:
    if skip:
        return _lineage_gate.run_lineage_gate(
            ["notices"], skip=True, phase=_lineage_gate.PRE_PUBLISH, stage=stage
        )
    checkpoint = load_checkpoint(build_id, checkpoint_dir)
    artifacts = {
        "notices": SimpleNamespace(
            chunk_path=Path(str(checkpoint["source_artifact"])),
            collection=str(checkpoint["build_collection"]),
        )
    }
    return _lineage_gate.run_lineage_gate(
        ["notices"],
        skip=False,
        phase=_lineage_gate.PRE_PUBLISH,
        stage=stage,
        artifacts=artifacts,
        collection_snapshot_loader=_build_snapshot_loader(),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-dir", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build/resume and verify a staged collection")
    build.add_argument("--build-id", help="Stable resume identifier; deterministic when omitted")
    build.add_argument("--artifact", type=Path, help="Notice parquet/CSV; defaults to the configured artifact")
    build.add_argument(
        "--batch-size",
        type=int,
        default=MIN_BATCH_SIZE,
        choices=range(MIN_BATCH_SIZE, MAX_BATCH_SIZE + 1),
        metavar=f"{MIN_BATCH_SIZE}..{MAX_BATCH_SIZE}",
    )
    _lineage_gate.add_skip_argument(build)

    verify = subparsers.add_parser("verify", help="Re-run ID/count/dimension/20-query verification")
    verify.add_argument("--build-id", required=True)

    activate = subparsers.add_parser("activate", help="Atomically switch the logical pointer")
    activate.add_argument("--build-id", required=True)
    activate.add_argument(
        "--confirm-build-id",
        required=True,
        help="Must exactly repeat --build-id; activation never happens from build alone",
    )
    activate.add_argument("--pointer-file", type=Path)
    activate.add_argument("--lock-file", type=Path)
    _lineage_gate.add_skip_argument(activate)

    rollback = subparsers.add_parser("rollback", help="Atomically return to the previous collection")
    rollback.add_argument("--confirm-active-collection", required=True)
    rollback.add_argument("--pointer-file", type=Path)
    rollback.add_argument("--lock-file", type=Path)

    status = subparsers.add_parser("status", help="Show pointer state and optional build checkpoint")
    status.add_argument("--build-id")
    status.add_argument("--pointer-file", type=Path)
    status.add_argument("--artifact", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    checkpoint_dir = args.checkpoint_dir
    try:
        if args.command == "build":
            with GracefulStop() as stop:
                result = build_notice_dense_index(
                    artifact_path=args.artifact,
                    build_id=args.build_id,
                    batch_size=args.batch_size,
                    checkpoint_dir=checkpoint_dir,
                    should_stop=lambda: stop.requested,
                )
            if result["status"] == "paused":
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 75
            gate = _staged_lineage_gate(
                build_id=str(result["build_id"]),
                checkpoint_dir=checkpoint_dir,
                skip=args.skip_lineage_gate,
                stage="rebuild_notices_dense build (staged, pointer unchanged)",
            )
            result = {**result, "lineage_gate": gate}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return int(gate["exit_code"])
        if args.command == "verify":
            result = verify_notice_dense_build(
                build_id=args.build_id,
                checkpoint_dir=checkpoint_dir,
            )
        elif args.command == "activate":
            gate = _staged_lineage_gate(
                build_id=args.build_id,
                checkpoint_dir=checkpoint_dir,
                skip=args.skip_lineage_gate,
                stage="rebuild_notices_dense activate (before pointer switch)",
            )
            if gate["exit_code"]:
                print(json.dumps({"status": "activation_refused", "lineage_gate": gate}, ensure_ascii=False, indent=2))
                return int(gate["exit_code"])
            result = activate_notice_dense_build(
                build_id=args.build_id,
                confirm_build_id=args.confirm_build_id,
                checkpoint_dir=checkpoint_dir,
                pointer_path=args.pointer_file,
                lock_path=args.lock_file,
            )
            result = {**result, "lineage_gate": gate}
        elif args.command == "rollback":
            result = rollback_notice_dense_pointer(
                confirm_active_collection=args.confirm_active_collection,
                pointer_path=args.pointer_file,
                lock_path=args.lock_file,
            )
        else:
            result = {
                "pointer": read_pointer_state(args.pointer_file),
                "artifact": (
                    {
                        "path": str(snapshot.path),
                        "sha256": snapshot.artifact_sha256,
                        "count": snapshot.count,
                        "ids_sha256": snapshot.expected_ids_sha256,
                    }
                    if (snapshot := load_notice_chunk_snapshot(args.artifact))
                    else None
                ),
                "checkpoint": (
                    load_checkpoint(args.build_id, checkpoint_dir)
                    if args.build_id
                    else None
                ),
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # CLI boundary: checkpoint already contains detail.
        logging.error("%s: %s", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
