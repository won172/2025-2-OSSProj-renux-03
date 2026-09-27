#!/usr/bin/env python3
"""Build the deterministic ontology projection from canonical source documents.

Dry-run is the default and does not create tables or write rows.  Use
``--apply`` only after reviewing the JSON summary and validation result.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from sqlalchemy import inspect as sqlalchemy_inspect, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import DATA_SOURCES  # noqa: E402
from src.database import (  # noqa: E402
    OntologyBuildRun,
    SessionLocal,
    init_db,
    kst_now,
)
from src.services.ontology import (  # noqa: E402
    DETERMINISTIC_DATASETS,
    ONTOLOGY_SCHEMA_VERSION,
    build_deterministic_projection,
    load_canonical_documents,
    load_department_aliases,
    persist_projection,
    validate_source_lineage,
    validate_projection,
)
from src.services.ontology_build import (  # noqa: E402
    OntologyBuildFailed,
    rebuild_ontology_projection,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--datasets",
        nargs="+",
        choices=tuple(sorted(DETERMINISTIC_DATASETS)),
        default=sorted(DETERMINISTIC_DATASETS),
        help=(
            "Canonical datasets to project "
            "(default: courses notices rules schedule staff)"
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create ontology tables and atomically replace deterministic rows",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON summary path")
    return parser


def _summary(projection, errors: list[str], *, applied: bool) -> dict:
    return {
        "schema_version": ONTOLOGY_SCHEMA_VERSION,
        "mode": "apply" if applied else "dry-run",
        "datasets": sorted(projection.source_datasets),
        "documents_seen": projection.documents_seen,
        "entities": len(projection.entities),
        "aliases": len(projection.aliases),
        "relations": len(projection.relations),
        "evidence": len(projection.evidence),
        "validation_errors": errors,
        "gate_passed": not errors,
    }


def _legacy_graph_summary(session) -> dict:
    """Detect the unowned v2 graph tables without reading them as canonical data."""
    table_names = set(sqlalchemy_inspect(session.bind).get_table_names())
    required = {"entities", "entity_relations"}
    if not required.issubset(table_names):
        return {"detected": False}
    entity_count = session.execute(text("SELECT COUNT(*) FROM entities")).scalar_one()
    relation_count = session.execute(
        text("SELECT COUNT(*) FROM entity_relations")
    ).scalar_one()
    distinct_relation_count = session.execute(
        text(
            "SELECT COUNT(*) FROM ("
            "SELECT DISTINCT source_entity_id, relation_type, target_entity_id "
            "FROM entity_relations)"
        )
    ).scalar_one()
    return {
        "detected": True,
        "entities": int(entity_count),
        "relation_rows": int(relation_count),
        "distinct_relations": int(distinct_relation_count),
        "policy": "preserved_not_read_or_modified",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.apply:
        init_db()

        if set(args.datasets) != set(DETERMINISTIC_DATASETS):
            report = {
                "schema_version": ONTOLOGY_SCHEMA_VERSION,
                "mode": "apply",
                "datasets": sorted(args.datasets),
                "gate_passed": False,
                "error": (
                    "revision-bound publication requires the complete deterministic "
                    "dataset set; omit --datasets"
                ),
            }
        else:
            try:
                report = rebuild_ontology_projection(
                    trigger_dataset="manual",
                    force=True,
                )
                report.update(
                    {
                        "schema_version": ONTOLOGY_SCHEMA_VERSION,
                        "mode": "apply",
                        "datasets": sorted(DETERMINISTIC_DATASETS),
                        "gate_passed": True,
                        "validation_errors": [],
                    }
                )
            except OntologyBuildFailed as exc:
                report = {
                    **exc.report,
                    "schema_version": ONTOLOGY_SCHEMA_VERSION,
                    "mode": "apply",
                    "datasets": sorted(DETERMINISTIC_DATASETS),
                    "gate_passed": False,
                }
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        print(rendered)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        return 0 if report.get("gate_passed") else 1

    alias_path = Path(DATA_SOURCES["courses_all"]).with_name(
        "dongguk_department_aliases.csv"
    )
    aliases = load_department_aliases(alias_path)
    session = SessionLocal()
    try:
        documents = load_canonical_documents(session, args.datasets)
        projection = build_deterministic_projection(
            documents,
            department_aliases=aliases,
            source_datasets=args.datasets,
        )
        errors = validate_projection(projection) + validate_source_lineage(
            session,
            projection,
        )
        report = _summary(projection, errors, applied=args.apply)
        report["legacy_graph"] = _legacy_graph_summary(session)
        if args.apply and not errors:
            run = OntologyBuildRun(
                schema_version=ONTOLOGY_SCHEMA_VERSION,
                datasets_json=json.dumps(
                    sorted(projection.source_datasets),
                    ensure_ascii=False,
                ),
                documents_seen=projection.documents_seen,
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
            report["build_run_id"] = run.id
            report["stale_relations_deleted"] = counts.stale_relations_deleted
            report["stale_entities_deleted"] = counts.stale_entities_deleted
        elif args.apply:
            report["applied"] = False
    except Exception as exc:
        session.rollback()
        report = {
            "schema_version": ONTOLOGY_SCHEMA_VERSION,
            "mode": "apply" if args.apply else "dry-run",
            "gate_passed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        session.close()

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report.get("gate_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
