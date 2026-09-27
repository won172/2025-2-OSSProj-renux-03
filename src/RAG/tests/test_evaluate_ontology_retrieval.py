from __future__ import annotations

from pathlib import Path
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scripts.evaluate_ontology_retrieval import (
    CompetencyCase,
    QUESTIONS_PATH,
    QRELS_PATH,
    build_document_identity_map,
    evaluate,
    load_cases,
    load_document_qrels,
    validate_fixture,
)
from src import database as db
from src.services.ontology_retrieval import (
    LinkedEntity,
    OntologyShadowResult,
    TraversedRelation,
)
from src.utils.preprocess import make_doc_id


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db.Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _case(case_id: str = "OR-T01", *, route: str = "courses") -> CompetencyCase:
    return CompetencyCase(
        id=case_id,
        question="자료구조 과목 정보",
        route=route,
        case_type="course_to_department",
        expected_entity_type="Course",
        expected_entity_name="자료구조",
        expected_predicates=("OFFERED_BY",),
    )


def test_committed_competency_fixture_has_required_coverage():
    cases = load_cases(QUESTIONS_PATH)
    qrels = load_document_qrels(QRELS_PATH)

    assert len(cases) == 75
    assert len({case.id for case in cases}) == 75
    assert sum(case.route == "courses" for case in cases) == 25
    assert sum(case.route == "staff" for case in cases) == 10
    assert sum(case.route == "rules" for case in cases) == 15
    assert sum(case.route == "schedule" for case in cases) == 15
    assert sum(case.route == "notices" for case in cases) == 10
    assert sum(case.case_type == "alias_to_department" for case in cases) == 5
    assert len(qrels) == 70
    assert sum(len(documents) for documents in qrels.values()) == 109
    assert {question_id for question_id, _ in qrels}.issubset(
        {case.id for case in cases}
    )


def test_loaders_reject_missing_required_columns(tmp_path: Path):
    bad_questions = tmp_path / "questions.csv"
    bad_questions.write_text("id,question\nOR-1,test\n", encoding="utf-8")
    with pytest.raises(ValueError, match="columns missing"):
        load_cases(bad_questions)

    bad_qrels = tmp_path / "qrels.csv"
    bad_qrels.write_text("question_id,dataset\nOR-1,courses\n", encoding="utf-8")
    with pytest.raises(ValueError, match="columns missing"):
        load_document_qrels(bad_qrels)


def test_fixture_validation_requires_published_canonical_document_and_matching_route():
    session = _session()
    try:
        session.add(
            db.SourceDocument(
                dataset="courses",
                source_type="fixture",
                source_id="course-1",
                document_key="courses:course-1",
                title="자료구조",
                status="active",
            )
        )
        session.commit()

        assert validate_fixture(
            session,
            [_case()],
            {("OR-T01", "courses"): {"courses:course-1": 2}},
            min_questions=1,
        ) == []

        errors = validate_fixture(
            session,
            [_case(), _case("OR-T02")],
            {
                ("OR-T01", "staff"): {"courses:course-1": 2},
                ("OR-T02", "courses"): {"courses:missing": 2},
                ("OR-MISSING", "courses"): {"courses:other-missing": 2},
            },
            min_questions=1,
        )
        assert any("route mismatch" in error for error in errors)
        assert any("unknown question" in error for error in errors)
        assert any("document missing" in error for error in errors)
    finally:
        session.close()


def test_identity_map_resolves_legacy_course_hash_without_reindexing():
    session = _session()
    try:
        payload = {
            "_source_table": "official_curriculum_pdf",
            "department_name": "가정교육과",
            "major": "가정교육과",
            "학수번호": "HOM4057",
            "course_name": "AI를활용한가정과논리및논술",
            "curriculum_url": "https://example.edu/curriculum.pdf",
            "section_title": "교과 교육과정",
        }
        document_key = "courses:가정교육과:HOM4057:fixture"
        session.add(
            db.SourceDocument(
                dataset="courses",
                source_type="official_curriculum_pdf",
                source_id="course-fixture",
                document_key=document_key,
                title=payload["course_name"],
                status="active",
                normalized_payload_json=json.dumps(payload, ensure_ascii=False),
            )
        )
        session.commit()

        legacy_id = make_doc_id(
            "courses",
            "가정교육과",
            "HOM4057",
            "https://example.edu/curriculum.pdf",
            "교과 교육과정",
            "official_curriculum_pdf",
        )
        identity_map = build_document_identity_map(session)

        assert identity_map[legacy_id] == document_key
        assert identity_map[document_key] == document_key
    finally:
        session.close()


def test_evaluator_compares_same_document_qrels_and_counts_graph_additions():
    session = _session()
    try:
        case = _case()

        def fake_baseline(dataset: str, question: str, top_k: int) -> list[str]:
            assert dataset == "courses"
            return ["courses:noise"]

        def fake_ontology(*args, **kwargs) -> OntologyShadowResult:
            return OntologyShadowResult(
                linked_entities=(
                    LinkedEntity(
                        entity_key="course:자료구조",
                        entity_type="Course",
                        canonical_name="자료구조",
                        matched_text="자료구조",
                        match_method="canonical",
                    ),
                ),
                traversed_relations=(
                    TraversedRelation(
                        relation_key="relation:course-department",
                        subject_key="course:자료구조",
                        predicate="OFFERED_BY",
                        object_key="department:컴퓨터ai학부",
                        depth=1,
                    ),
                ),
                document_keys=("courses:answer",),
                max_hops=2,
            )

        report = evaluate(
            session,
            [case],
            {("OR-T01", "courses"): {"courses:answer": 2}},
            baseline_retriever=fake_baseline,
            ontology_runner=fake_ontology,
        )

        assert report["contract"]["link_accuracy"] == 1.0
        assert report["contract"]["predicate_path_accuracy"] == 1.0
        assert report["baseline"]["recall@10"] == 0.0
        assert report["ontology"]["recall@10"] == 1.0
        assert report["ontology"]["mrr"] == 1.0
        assert report["candidate_comparison"]["union_recall_at_10"] == 1.0
        assert report["candidate_comparison"]["graph_added_relevant_at_10"] == 1
    finally:
        session.close()
