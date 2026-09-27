"""Post-ingestion derivative DAG: lineage gate, then ontology publication."""
from __future__ import annotations

from typing import Any

from src.pipelines.ingest import DATASET_ARTIFACTS
from src.services.canonical_lineage import build_canonical_lineage_report
from src.services.ontology import DETERMINISTIC_DATASETS
from src.services.ontology_build import OntologyBuildFailed, rebuild_ontology_projection


def run_post_ingestion_dag(
    dataset: str,
    *,
    ingestion_run_id: int | None,
) -> dict[str, Any]:
    """Run ordered, fail-safe derivatives after canonical indexing completes."""
    if dataset not in DATASET_ARTIFACTS:
        raise ValueError(f"unsupported dataset: {dataset}")
    lineage = build_canonical_lineage_report(
        artifacts={dataset: DATASET_ARTIFACTS[dataset]}
    )
    stages: list[dict[str, Any]] = [
        {
            "name": "canonical_lineage",
            "status": "success" if lineage["gate_passed"] else "failed",
            "violations": lineage["violations"],
        }
    ]
    if not lineage["gate_passed"]:
        return {"status": "failed", "failed_stage": "canonical_lineage", "stages": stages}

    if dataset not in DETERMINISTIC_DATASETS:
        stages.append({"name": "ontology", "status": "skipped", "reason": "dataset_out_of_scope"})
        return {"status": "success", "stages": stages}

    try:
        ontology = rebuild_ontology_projection(
            trigger_dataset=dataset,
            trigger_ingestion_run_id=ingestion_run_id,
        )
        stages.append({"name": "ontology", **ontology})
        return {"status": "success", "stages": stages}
    except OntologyBuildFailed as exc:
        stages.append({"name": "ontology", **exc.report})
        return {"status": "failed", "failed_stage": "ontology", "stages": stages}


__all__ = ["run_post_ingestion_dag"]
