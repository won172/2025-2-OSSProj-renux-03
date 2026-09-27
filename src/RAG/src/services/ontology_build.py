"""Transactional ontology builds tied to the current corpus revisions."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from src.config import DATA_SOURCES
from src.database import OntologyBuildRun, SessionLocal, kst_now
from src.pipelines.ingest import DATASET_ARTIFACTS
from src.services.corpus_revision import frame_corpus_revision
from src.services.ontology import (
    DETERMINISTIC_DATASETS,
    ONTOLOGY_SCHEMA_VERSION,
    build_deterministic_projection,
    load_canonical_documents,
    load_department_aliases,
    persist_projection,
    validate_projection,
    validate_source_lineage,
)


class OntologyBuildFailed(RuntimeError):
    def __init__(self, report: dict[str, Any]):
        self.report = report
        super().__init__(str(report.get("error") or "ontology build failed"))


@lru_cache(maxsize=16)
def _revisions_for_artifact_signature(
    signature: tuple[tuple[str, str, int, int], ...],
) -> tuple[tuple[str, str], ...]:
    revisions: list[tuple[str, str]] = []
    for dataset, raw_path, _mtime_ns, _size in signature:
        frame = pd.read_parquet(Path(raw_path), columns=["corpus_revision"])
        revision = frame_corpus_revision(frame)
        if revision is None:
            raise ValueError(f"{dataset} artifact has no single corpus_revision")
        revisions.append((dataset, revision))
    return tuple(revisions)


def current_corpus_revisions(
    datasets: Iterable[str] = tuple(sorted(DETERMINISTIC_DATASETS)),
) -> dict[str, str]:
    signature: list[tuple[str, str, int, int]] = []
    for dataset in sorted(set(datasets)):
        path = DATASET_ARTIFACTS[dataset].chunk_path
        if not path.exists():
            raise FileNotFoundError(f"missing chunk artifact: {path}")
        stat = path.stat()
        signature.append((dataset, str(path.resolve()), stat.st_mtime_ns, stat.st_size))
    return dict(_revisions_for_artifact_signature(tuple(signature)))


def ontology_build_revision(
    corpus_revisions: dict[str, str],
    *,
    alias_sha256: str,
) -> str:
    payload = {
        "schema_version": ONTOLOGY_SCHEMA_VERSION,
        "corpus_revisions": dict(sorted(corpus_revisions.items())),
        "department_aliases_sha256": alias_sha256,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"ontology:{digest}"


def _alias_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes() if path.exists() else b"").hexdigest()


def _record_failed_run(
    session_factory: Callable,
    *,
    datasets: list[str],
    revisions: dict[str, str],
    build_revision: str,
    trigger_dataset: str | None,
    trigger_ingestion_run_id: int | None,
    errors: list[str],
    error: str,
) -> int | None:
    session = session_factory()
    try:
        row = OntologyBuildRun(
            schema_version=ONTOLOGY_SCHEMA_VERSION,
            datasets_json=json.dumps(datasets, ensure_ascii=False),
            status="failed",
            finished_at=kst_now(),
            validation_errors_json=json.dumps(errors, ensure_ascii=False),
            build_revision=build_revision,
            corpus_revisions_json=json.dumps(revisions, ensure_ascii=False, sort_keys=True),
            trigger_dataset=trigger_dataset,
            trigger_ingestion_run_id=trigger_ingestion_run_id,
            error_summary=error,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return int(row.id)
    except Exception:
        session.rollback()
        return None
    finally:
        session.close()


def rebuild_ontology_projection(
    *,
    trigger_dataset: str | None = None,
    trigger_ingestion_run_id: int | None = None,
    force: bool = False,
    session_factory: Callable = SessionLocal,
    corpus_revisions: dict[str, str] | None = None,
    alias_path: Path | None = None,
) -> dict[str, Any]:
    """Build and atomically publish the full deterministic projection."""
    datasets = sorted(DETERMINISTIC_DATASETS)
    revisions = corpus_revisions or current_corpus_revisions(datasets)
    missing = sorted(set(datasets) - set(revisions))
    if missing:
        raise ValueError(f"missing ontology corpus revisions: {missing}")
    aliases_path = alias_path or Path(DATA_SOURCES["courses_all"]).with_name(
        "dongguk_department_aliases.csv"
    )
    build_revision = ontology_build_revision(
        revisions,
        alias_sha256=_alias_sha256(aliases_path),
    )

    session = session_factory()
    try:
        latest = (
            session.query(OntologyBuildRun)
            .filter(OntologyBuildRun.status == "success")
            .order_by(OntologyBuildRun.id.desc())
            .first()
        )
        if not force and latest is not None and latest.build_revision == build_revision:
            return {
                "status": "skipped",
                "reason": "already_current",
                "build_run_id": int(latest.id),
                "build_revision": build_revision,
                "corpus_revisions": revisions,
            }

        aliases = load_department_aliases(aliases_path)
        documents = load_canonical_documents(session, datasets)
        projection = build_deterministic_projection(
            documents,
            department_aliases=aliases,
            source_datasets=datasets,
        )
        errors = validate_projection(projection) + validate_source_lineage(session, projection)
        if errors:
            session.rollback()
            report = {
                "status": "failed",
                "stage": "validation",
                "error": "; ".join(errors[:10]),
                "validation_errors": errors,
                "build_revision": build_revision,
                "corpus_revisions": revisions,
            }
            report["build_run_id"] = _record_failed_run(
                session_factory,
                datasets=datasets,
                revisions=revisions,
                build_revision=build_revision,
                trigger_dataset=trigger_dataset,
                trigger_ingestion_run_id=trigger_ingestion_run_id,
                errors=errors,
                error=report["error"],
            )
            raise OntologyBuildFailed(report)

        run = OntologyBuildRun(
            schema_version=ONTOLOGY_SCHEMA_VERSION,
            datasets_json=json.dumps(datasets, ensure_ascii=False),
            documents_seen=projection.documents_seen,
            status="running",
            build_revision=build_revision,
            corpus_revisions_json=json.dumps(revisions, ensure_ascii=False, sort_keys=True),
            trigger_dataset=trigger_dataset,
            trigger_ingestion_run_id=trigger_ingestion_run_id,
        )
        session.add(run)
        counts = persist_projection(session, projection)
        run.entities_emitted = counts.entities
        run.aliases_emitted = counts.aliases
        run.relations_emitted = counts.relations
        run.evidence_emitted = counts.evidence
        run.validation_errors_json = "[]"
        run.status = "success"
        run.finished_at = kst_now()
        session.commit()
        session.refresh(run)
        return {
            "status": "success",
            "build_run_id": int(run.id),
            "build_revision": build_revision,
            "corpus_revisions": revisions,
            "documents_seen": projection.documents_seen,
            "entities": counts.entities,
            "aliases": counts.aliases,
            "relations": counts.relations,
            "evidence": counts.evidence,
            "stale_relations_deleted": counts.stale_relations_deleted,
            "stale_entities_deleted": counts.stale_entities_deleted,
        }
    except OntologyBuildFailed:
        raise
    except Exception as exc:
        session.rollback()
        report = {
            "status": "failed",
            "stage": "publish",
            "error": f"{type(exc).__name__}: {exc}",
            "validation_errors": [],
            "build_revision": build_revision,
            "corpus_revisions": revisions,
        }
        report["build_run_id"] = _record_failed_run(
            session_factory,
            datasets=datasets,
            revisions=revisions,
            build_revision=build_revision,
            trigger_dataset=trigger_dataset,
            trigger_ingestion_run_id=trigger_ingestion_run_id,
            errors=[],
            error=report["error"],
        )
        raise OntologyBuildFailed(report) from exc
    finally:
        session.close()


def ontology_projection_is_current(
    session,
    *,
    corpus_revisions: dict[str, str] | None = None,
) -> tuple[bool, str]:
    latest = (
        session.query(OntologyBuildRun)
        .filter(OntologyBuildRun.status == "success")
        .order_by(OntologyBuildRun.id.desc())
        .first()
    )
    if latest is None or not latest.build_revision:
        return False, "no_successful_build"
    try:
        recorded = json.loads(latest.corpus_revisions_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return False, "invalid_build_manifest"
    current = corpus_revisions or current_corpus_revisions()
    if recorded != current:
        return False, "corpus_revision_mismatch"
    return True, "current"


__all__ = [
    "OntologyBuildFailed",
    "current_corpus_revisions",
    "ontology_build_revision",
    "ontology_projection_is_current",
    "rebuild_ontology_projection",
]
