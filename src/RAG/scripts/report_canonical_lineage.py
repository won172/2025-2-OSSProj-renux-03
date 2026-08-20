#!/usr/bin/env python3
"""Report or strictly enforce canonical-document search lineage."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pipelines.ingest import DATASET_ARTIFACTS  # noqa: E402
from src.services.canonical_lineage import build_canonical_lineage_report  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("observe", "strict"), default="observe")
    parser.add_argument(
        "--datasets",
        nargs="+",
        choices=tuple(DATASET_ARTIFACTS),
        help="Datasets to inspect (default: all canonical datasets)",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    selected = args.datasets or list(DATASET_ARTIFACTS)
    artifacts = {key: DATASET_ARTIFACTS[key] for key in selected}
    report = build_canonical_lineage_report(artifacts=artifacts)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 1 if args.mode == "strict" and not report["gate_passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
