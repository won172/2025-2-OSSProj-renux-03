#!/usr/bin/env python3
"""Emit a PII-free source quality report and optionally enforce strict gates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.database import SessionLocal  # noqa: E402
from src.services.data_quality import (  # noqa: E402
    DataQualityThresholds,
    build_source_document_quality_report,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        choices=("notices",),
        default="notices",
        help="Dataset with source-document linkage checks (currently notices only)",
    )
    parser.add_argument("--mode", choices=("observe", "strict"), default="observe")
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    parser.add_argument("--retry-output", type=Path, help="Optional retry-manifest JSON path")
    parser.add_argument("--max-parse-error-ratio", type=float, default=0.05)
    parser.add_argument("--max-category-unknown-ratio", type=float, default=0.20)
    parser.add_argument("--max-inactive-ratio", type=float, default=0.30)
    parser.add_argument("--max-index-mismatch-ratio", type=float, default=0.01)
    return parser


def main() -> int:
    args = _parser().parse_args()
    thresholds = DataQualityThresholds(
        max_parse_error_ratio=args.max_parse_error_ratio,
        max_category_unknown_ratio=args.max_category_unknown_ratio,
        max_inactive_ratio=args.max_inactive_ratio,
        max_index_mismatch_ratio=args.max_index_mismatch_ratio,
    )
    session = SessionLocal()
    try:
        report = build_source_document_quality_report(
            session,
            dataset=args.dataset,
            thresholds=thresholds,
        )
    finally:
        session.close()

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if args.retry_output:
        args.retry_output.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": report["schema_version"],
            "dataset": report["dataset"],
            "documents": report["retry_documents"],
        }
        args.retry_output.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 1 if args.mode == "strict" and not report["gate_passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
