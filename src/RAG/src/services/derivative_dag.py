"""Post-ingestion derivative DAG: lineage gate, then ontology publication."""
from __future__ import annotations

from typing import Any

from src.pipelines.ingest import DATASET_ARTIFACTS
from src.services.canonical_lineage import build_canonical_lineage_report
from src.services.ontology import DETERMINISTIC_DATASETS
from src.services.ontology_build import OntologyBuildFailed, rebuild_ontology_projection


def _finish_connected_stage(dataset,stages):
    from src import config
    if not config.RAG_CONNECTED_MAINTENANCE_ENABLED:
        return {'status':'success','stages':stages}
    # This is reached only after the ordinary canonical/index lineage gate.
    # Reuse the same cross-process publication lock as the canonical writer.
    from src.database import DATABASE_FILE
    from src.services.ingest_runtime import serialized_ingest_write
    from src.services.connected_knowledge import rebuild_connected_projection
    import sqlite3
    try:
        with serialized_ingest_write(dataset=dataset,operation='connected_information_projection'):
            with sqlite3.connect(DATABASE_FILE) as db:
                report=rebuild_connected_projection(db)
        stages.append({'name':'connected_information','status':'success','revision':report['revision'],'information':report['information']})
        return {'status':'success','stages':stages}
    except Exception as exc:
        stages.append({'name':'connected_information','status':'failed','error':f'{type(exc).__name__}: {exc}'})
        return {'status':'failed','failed_stage':'connected_information','stages':stages}


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
        return _finish_connected_stage(dataset,stages)

    try:
        ontology = rebuild_ontology_projection(
            trigger_dataset=dataset,
            trigger_ingestion_run_id=ingestion_run_id,
        )
        stages.append({"name": "ontology", **ontology})
        return _finish_connected_stage(dataset,stages)
    except OntologyBuildFailed as exc:
        stages.append({"name": "ontology", **exc.report})
        return {"status": "failed", "failed_stage": "ontology", "stages": stages}


__all__ = ["run_post_ingestion_dag"]
