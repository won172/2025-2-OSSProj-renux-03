#!/usr/bin/env python3
"""Stamp one corpus revision across chunk, lexical, and dense derivatives."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pipelines.ingest import (  # noqa: E402
    DATASET_ARTIFACTS,
    persist_dataset_artifacts_only,
    update_collection_metadata_from_frame,
)
from src.search.hybrid import read_lexical_metadata  # noqa: E402
from src.services.canonical_lineage import build_canonical_lineage_report  # noqa: E402
from src.services.corpus_revision import frame_corpus_revision  # noqa: E402


def backfill(datasets: list[str]) -> dict[str, object]:
    results: dict[str, object] = {}
    for dataset in datasets:
        artifact = DATASET_ARTIFACTS[dataset]
        if not artifact.chunk_path.exists():
            raise FileNotFoundError(f"missing chunk artifact: {artifact.chunk_path}")
        frame = pd.read_parquet(artifact.chunk_path)
        persisted, _, _ = persist_dataset_artifacts_only(dataset, frame)
        revision = frame_corpus_revision(persisted)
        if revision is None:
            raise RuntimeError(f"{dataset} artifact does not have one corpus revision")
        update_collection_metadata_from_frame(dataset, persisted)
        lexical_revision = read_lexical_metadata(dataset).get("corpus_revision")
        if lexical_revision != revision:
            raise RuntimeError(
                f"{dataset} lexical revision mismatch: {lexical_revision} != {revision}"
            )
        results[dataset] = {"chunks": len(persisted), "corpus_revision": revision}

    lineage = build_canonical_lineage_report(
        artifacts={key: DATASET_ARTIFACTS[key] for key in datasets}
    )
    if not lineage["gate_passed"]:
        raise RuntimeError(f"canonical lineage failed: {lineage['violations']}")
    return {"datasets": results, "lineage_gate_passed": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--datasets",
        nargs="+",
        choices=tuple(DATASET_ARTIFACTS),
        default=list(DATASET_ARTIFACTS),
    )
    args = parser.parse_args()
    print(json.dumps(backfill(args.datasets), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
