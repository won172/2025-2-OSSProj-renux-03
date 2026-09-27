from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base, OntologyBuildRun, OntologyEntity, SourceDocument
from src.services import ontology_build


def _factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _revisions(suffix: str = "1") -> dict[str, str]:
    return {
        dataset: f"{dataset}:{suffix}"
        for dataset in sorted(ontology_build.DETERMINISTIC_DATASETS)
    }


def test_ontology_build_is_revision_bound_and_idempotent(tmp_path):
    factory = _factory()
    aliases = tmp_path / "aliases.csv"
    aliases.write_text("alias,canonical_department_name\n", encoding="utf-8")
    with factory() as session:
        session.add(
            SourceDocument(
                dataset="schedule",
                source_type="academic_schedule",
                source_id="1",
                document_key="schedule:1",
                status="active",
                normalized_payload_json=json.dumps(
                    {"academic_year": "2026", "title": "수강신청", "start_date": "2026-08-01", "end_date": "2026-08-02"}
                ),
            )
        )
        session.commit()

    first = ontology_build.rebuild_ontology_projection(
        session_factory=factory,
        corpus_revisions=_revisions(),
        alias_path=aliases,
    )
    second = ontology_build.rebuild_ontology_projection(
        session_factory=factory,
        corpus_revisions=_revisions(),
        alias_path=aliases,
    )

    assert first["status"] == "success"
    assert second["status"] == "skipped"
    assert second["build_revision"] == first["build_revision"]
    with factory() as session:
        run = session.query(OntologyBuildRun).filter_by(status="success").one()
        assert json.loads(run.corpus_revisions_json) == _revisions()
        assert session.query(OntologyEntity).count() > 0
        assert ontology_build.ontology_projection_is_current(
            session, corpus_revisions=_revisions()
        ) == (True, "current")
        assert ontology_build.ontology_projection_is_current(
            session, corpus_revisions=_revisions("other")
        ) == (False, "corpus_revision_mismatch")


def test_failed_publish_rolls_back_graph_and_records_failure(monkeypatch, tmp_path):
    factory = _factory()
    aliases = tmp_path / "aliases.csv"
    aliases.write_text("alias,canonical_department_name\n", encoding="utf-8")
    with factory() as session:
        session.add(
            SourceDocument(
                dataset="schedule",
                source_type="academic_schedule",
                source_id="1",
                document_key="schedule:1",
                status="active",
                normalized_payload_json=json.dumps(
                    {"academic_year": "2026", "title": "수강신청", "start_date": "2026-08-01", "end_date": "2026-08-02"}
                ),
            )
        )
        session.commit()
    ontology_build.rebuild_ontology_projection(
        session_factory=factory,
        corpus_revisions=_revisions(),
        alias_path=aliases,
    )
    with factory() as session:
        before = session.query(OntologyEntity).count()

    def explode(session, projection):
        session.query(OntologyEntity).delete()
        session.flush()
        raise RuntimeError("publish exploded")

    monkeypatch.setattr(ontology_build, "persist_projection", explode)
    try:
        ontology_build.rebuild_ontology_projection(
            session_factory=factory,
            corpus_revisions=_revisions("2"),
            alias_path=aliases,
        )
    except ontology_build.OntologyBuildFailed as exc:
        assert exc.report["stage"] == "publish"
    else:
        raise AssertionError("expected ontology build failure")

    with factory() as session:
        assert session.query(OntologyEntity).count() == before
        assert session.query(OntologyBuildRun).filter_by(status="failed").count() == 1
