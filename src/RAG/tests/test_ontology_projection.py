from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src import database as db
from src.services.ontology import (
    EntityType,
    Predicate,
    build_deterministic_projection,
    identity_token,
    persist_projection,
    validate_source_lineage,
    validate_projection,
)


def _document(
    dataset: str,
    source_id: str,
    payload: dict,
    *,
    status: str = "active",
) -> db.SourceDocument:
    return db.SourceDocument(
        dataset=dataset,
        source_type=f"{dataset}_fixture",
        source_id=source_id,
        source_url=f"https://www.dongguk.edu/{dataset}/{source_id}",
        document_key=f"{dataset}:{source_id}",
        title=str(payload.get("title") or payload.get("course_name") or source_id),
        status=status,
        normalized_payload_json=json.dumps(payload, ensure_ascii=False),
    )


def _fixture_documents() -> list[db.SourceDocument]:
    return [
        _document(
            "courses",
            "course-1",
            {
                "college_name": "첨단융합대학",
                "department_name": "컴퓨터공학과",
                "course_code": "CSE1001",
                "course_name": "자료구조",
                "record_type": "table_row",
                "availability_status": "curriculum_only",
            },
        ),
        _document(
            "staff",
            "staff-1",
            {
                "부서경로": "동국대학교 > 첨단융합대학 > 컴퓨터·AI학부",
                "조직(트리)": "컴퓨터·AI학부",
                "성명": "김**",
                "직위": "팀원",
                "담당업무": "학사 안내",
                "전화번호": "02-0000-0000",
            },
        ),
        _document(
            "notices",
            "notice-ignored",
            {"title": "자유 텍스트 공지", "content_text": "컴퓨터·AI학부"},
        ),
        _document(
            "courses",
            "hidden-course",
            {
                "college_name": "숨김대학",
                "department_name": "숨김학과",
                "course_name": "숨김과목",
            },
            status="hidden",
        ),
    ]


def test_identity_token_normalizes_spacing_case_and_middle_dot():
    assert identity_token(" 컴퓨터·AI 학부 ") == "컴퓨터ai학부"


def test_projection_links_course_staff_and_academic_hierarchy_with_evidence():
    projection = build_deterministic_projection(
        _fixture_documents(),
        department_aliases={"컴퓨터공학과": "컴퓨터·AI학부"},
        source_datasets=("courses", "staff"),
    )

    assert projection.documents_seen == 2
    assert validate_projection(projection) == []
    assert len(projection.entities) == 5
    assert len(projection.aliases) == 2
    assert len(projection.relations) == 4
    assert len(projection.evidence) == 5

    entity_types = {entity.entity_type for entity in projection.entities.values()}
    assert entity_types == {
        EntityType.COLLEGE,
        EntityType.DEPARTMENT,
        EntityType.ORGANIZATION,
        EntityType.COURSE,
        EntityType.PERSON,
    }
    assert not any(
        entity.entity_type == EntityType.ORGANIZATION
        and entity.canonical_name == "첨단융합대학"
        for entity in projection.entities.values()
    )

    predicates = [relation.predicate for relation in projection.relations.values()]
    assert predicates.count(Predicate.PART_OF) == 2
    assert predicates.count(Predicate.OFFERED_BY) == 1
    assert predicates.count(Predicate.WORKS_AT) == 1
    assert all(evidence.document_key for evidence in projection.evidence.values())
    assert all(evidence.evidence_locator for evidence in projection.evidence.values())


def test_projection_validation_rejects_a_relation_without_canonical_evidence():
    projection = build_deterministic_projection(
        _fixture_documents(),
        department_aliases={"컴퓨터공학과": "컴퓨터·AI학부"},
        source_datasets=("courses", "staff"),
    )
    target_relation = next(iter(projection.relations))
    projection.evidence = {
        key: evidence
        for key, evidence in projection.evidence.items()
        if evidence.relation_key != target_relation
    }

    errors = validate_projection(projection)
    assert f"relation evidence missing: {target_relation}" in errors


def test_source_document_identity_is_preserved_without_unicode_normalization():
    document = _document(
        "courses",
        "별표＊와ᆞ자모",
        {
            "college_name": "공과대학",
            "department_name": "건축공학부",
            "course_name": "건축설계",
        },
    )
    projection = build_deterministic_projection(
        [document],
        source_datasets=("courses",),
    )

    assert {
        evidence.document_key for evidence in projection.evidence.values()
    } == {document.document_key}


def test_staff_member_may_work_directly_at_a_college_node():
    documents = [
        _fixture_documents()[0],
        _document(
            "staff",
            "college-staff",
            {
                "부서경로": "동국대학교 > 첨단융합대학",
                "조직(트리)": "첨단융합대학",
                "성명": "이**",
                "직위": "학사운영실 직원",
            },
        ),
    ]
    projection = build_deterministic_projection(
        documents,
        source_datasets=("courses", "staff"),
    )

    assert validate_projection(projection) == []
    works_at = next(
        relation
        for relation in projection.relations.values()
        if relation.predicate == Predicate.WORKS_AT
    )
    assert projection.entities[works_at.object_key].entity_type == EntityType.COLLEGE


def test_projection_links_entry_year_guide_to_cohort_college_and_departments():
    document = db.SourceDocument(
        dataset="rules",
        source_type="entry_year_guide_pdf",
        source_id="2022-edu-page-42",
        source_url="https://www.dongguk.edu/guide/2022_edu.pdf",
        document_key=(
            "rules:entry_year_guides:2022_edu.pdf:2022:"
            "단과대학별 졸업기준#3"
        ),
        title=(
            "2022학년도 학업이수가이드 단과대학별 졸업기준 - 이과대학 "
            "(소속 학과: 물리학과, 수학과, 통계학과, 화학과)"
        ),
        status="active",
        normalized_payload_json=json.dumps(
            {
                "title": (
                    "2022학년도 학업이수가이드 단과대학별 졸업기준 - 이과대학 "
                    "(소속 학과: 물리학과, 수학과, 통계학과, 화학과)"
                ),
                "entry_year": "2022",
                "section": "단과대학별 졸업기준",
                "college_name": "이과대학",
                "page_start": 42,
                "page_end": 43,
                "text": "OCR 표 원문에는 졸업 학점 정보가 있지만 아직 구조화하지 않는다.",
            },
            ensure_ascii=False,
        ),
    )

    projection = build_deterministic_projection(
        [document],
        source_datasets=("rules",),
    )

    assert validate_projection(projection) == []
    assert projection.documents_seen == 1
    requirement = next(
        entity
        for entity in projection.entities.values()
        if entity.entity_type == EntityType.ACADEMIC_REQUIREMENT
    )
    assert requirement.properties["entry_year"] == "2022"
    assert requirement.properties["page_start"] == "42"
    assert requirement.properties["page_end"] == "43"
    assert requirement.properties["structured_credit_values"] is False
    assert not any(
        "credit" in key and key != "structured_credit_values"
        for key in requirement.properties
    )

    assert any(
        entity.entity_type == EntityType.ENTRY_COHORT
        and entity.canonical_name == "2022학번"
        for entity in projection.entities.values()
    )
    assert any(alias.alias == "22학번" for alias in projection.aliases.values())
    governed_names = {
        projection.entities[relation.object_key].canonical_name
        for relation in projection.relations.values()
        if relation.predicate == Predicate.GOVERNS
    }
    assert governed_names == {
        "이과대학",
        "물리학과",
        "수학과",
        "통계학과",
        "화학과",
    }
    assert sum(
        relation.predicate == Predicate.VALID_FOR_ENTRY
        for relation in projection.relations.values()
    ) == 1
    assert all(
        evidence.document_key == document.document_key
        for evidence in projection.evidence.values()
    )
    assert all(evidence.evidence_locator for evidence in projection.evidence.values())


def test_projection_does_not_infer_rules_from_free_text_or_unscoped_header():
    free_text = _document(
        "rules",
        "free-text",
        {
            "title": "2022학번 통계학과 졸업학점 공지",
            "entry_year": "2022",
            "section": "단과대학별 졸업기준",
            "college_name": "이과대학",
        },
    )
    header = db.SourceDocument(
        dataset="rules",
        source_type="entry_year_guide_pdf",
        source_id="header",
        document_key="rules:guide:header",
        title="2022학년도 단과대학별 졸업기준",
        status="active",
        normalized_payload_json=json.dumps(
            {
                "entry_year": "2022",
                "section": "단과대학별 졸업기준",
                "college_name": "",
            },
            ensure_ascii=False,
        ),
    )

    projection = build_deterministic_projection(
        [free_text, header],
        source_datasets=("rules",),
    )

    assert projection.documents_seen == 2
    assert projection.entities == {}
    assert projection.relations == {}
    assert projection.evidence == {}


def test_projection_links_academic_event_to_date_range_and_public_manager():
    staff = _document(
        "staff",
        "academic-support",
        {
            "부서경로": "동국대학교 > 교무처 > 학사지원팀",
            "성명": "김**",
            "직위": "팀원",
        },
    )
    schedule = db.SourceDocument(
        dataset="schedule",
        source_type="academic_schedule",
        source_id="2026-fall-registration",
        document_key=(
            "schedule:2026-08-03:2026-08-07:학사일정:"
            "학사지원팀:2026학년도 2학기 학부 수강 신청"
        ),
        title="2026학년도 2학기 학부 수강 신청",
        status="active",
        normalized_payload_json=json.dumps(
            {
                "academic_year": "2026",
                "category": "학사일정",
                "content": "2026학년도 2학기 학부 수강 신청",
                "department": "학사지원팀",
                "start_date": "2026-08-03",
                "end_date": "2026-08-07",
                "title": "2026학년도 2학기 학부 수강 신청",
            },
            ensure_ascii=False,
        ),
    )

    projection = build_deterministic_projection(
        [staff, schedule],
        source_datasets=("schedule", "staff"),
    )

    assert validate_projection(projection) == []
    event = next(
        entity
        for entity in projection.entities.values()
        if entity.entity_type == EntityType.ACADEMIC_EVENT
    )
    assert event.properties["start_date"] == "2026-08-03"
    assert event.properties["end_date"] == "2026-08-07"
    assert any(
        entity.entity_type == EntityType.DATE_RANGE
        and entity.canonical_name == "2026-08-03~2026-08-07"
        for entity in projection.entities.values()
    )
    assert {relation.predicate for relation in projection.relations.values()} >= {
        Predicate.OCCURS_DURING,
        Predicate.MANAGED_BY,
    }
    manager_relation = next(
        relation
        for relation in projection.relations.values()
        if relation.predicate == Predicate.MANAGED_BY
    )
    assert projection.entities[manager_relation.object_key].canonical_name == "학사지원팀"
    assert {
        alias.alias
        for alias in projection.aliases.values()
        if alias.entity_key == event.entity_key
    } >= {"2학기 수강신청", "수강신청"}
    event_evidence = {
        evidence.document_key
        for evidence in projection.evidence.values()
        if evidence.relation_key
        in {
            relation.relation_key
            for relation in projection.relations.values()
            if relation.subject_key == event.entity_key
        }
    }
    assert event_evidence == {schedule.document_key}


def test_projection_keeps_generic_schedule_scope_as_property_not_manager():
    schedule = db.SourceDocument(
        dataset="schedule",
        source_type="academic_schedule",
        source_id="semester-start",
        document_key="schedule:2026-03-01:semester-start",
        title="학기 개시일",
        status="active",
        normalized_payload_json=json.dumps(
            {
                "academic_year": "2026",
                "category": "학사일정",
                "department": "각 학과별",
                "start_date": "2026-03-01",
                "end_date": "2026-03-01",
                "title": "학기 개시일",
            },
            ensure_ascii=False,
        ),
    )

    projection = build_deterministic_projection(
        [schedule],
        source_datasets=("schedule",),
    )

    assert validate_projection(projection) == []
    assert {relation.predicate for relation in projection.relations.values()} == {
        Predicate.OCCURS_DURING
    }
    event = next(
        entity
        for entity in projection.entities.values()
        if entity.entity_type == EntityType.ACADEMIC_EVENT
    )
    assert event.properties["department"] == "각 학과별"


def test_projection_links_only_unambiguous_explicit_notice_organization_labels():
    staff = _document(
        "staff",
        "digital-information",
        {
            "부서경로": "동국대학교 > 디지털정보처",
            "성명": "김**",
        },
    )
    explicit = _document(
        "notices",
        "explicit",
        {
            "title": "[디지털정보처] 정보서비스 점검 안내",
            "board_name": "일반공지",
            "category": "일반",
            "content_text": "서비스 점검 내용",
        },
    )
    free_text_only = _document(
        "notices",
        "free-text-only",
        {
            "title": "정보서비스 점검 안내",
            "content_text": "문의는 디지털정보처로 해 주세요.",
        },
    )

    projection = build_deterministic_projection(
        [staff, explicit, free_text_only],
        source_datasets=("notices", "staff"),
    )

    assert validate_projection(projection) == []
    notices = [
        entity
        for entity in projection.entities.values()
        if entity.entity_type == EntityType.NOTICE
    ]
    assert [entity.canonical_name for entity in notices] == [
        "[디지털정보처] 정보서비스 점검 안내"
    ]
    relation = next(
        relation
        for relation in projection.relations.values()
        if relation.predicate == Predicate.MENTIONS_ORGANIZATION
    )
    assert projection.entities[relation.object_key].canonical_name == "디지털정보처"
    evidence = next(
        evidence
        for evidence in projection.evidence.values()
        if evidence.relation_key == relation.relation_key
    )
    assert evidence.document_key == explicit.document_key
    assert evidence.evidence_locator == "$.title[leading_bracket]"


def test_projection_skips_ambiguous_notice_organization_label():
    staff_rows = [
        _document(
            "staff",
            f"ambiguous-{index}",
            {
                "부서경로": f"동국대학교 > 본부{index} > 공용센터",
                "성명": f"김{index}**",
            },
        )
        for index in (1, 2)
    ]
    notice = _document(
        "notices",
        "ambiguous",
        {"title": "[공용센터] 프로그램 안내"},
    )

    projection = build_deterministic_projection(
        [*staff_rows, notice],
        source_datasets=("notices", "staff"),
    )

    assert validate_projection(projection) == []
    assert not any(
        entity.entity_type == EntityType.NOTICE
        for entity in projection.entities.values()
    )
    assert not any(
        relation.predicate == Predicate.MENTIONS_ORGANIZATION
        for relation in projection.relations.values()
    )


def test_persistence_replaces_only_scoped_deterministic_projection():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine)
    db.Base.metadata.create_all(bind=engine)
    session = factory()
    try:
        documents = _fixture_documents()
        session.add_all(documents)
        session.commit()
        first = build_deterministic_projection(
            documents,
            department_aliases={"컴퓨터공학과": "컴퓨터·AI학부"},
            source_datasets=("courses", "staff"),
        )
        counts = persist_projection(session, first)
        session.commit()

        assert counts.stale_relations_deleted == 0
        assert session.query(db.OntologyEntity).count() == 5
        assert session.query(db.OntologyAlias).count() == 2
        assert session.query(db.OntologyRelation).count() == 4
        assert session.query(db.OntologyEvidence).count() == 5

        course_only = build_deterministic_projection(
            [documents[0]],
            department_aliases={"컴퓨터공학과": "컴퓨터·AI학부"},
            # A full courses+staff rebuild where the staff canonical set is now empty.
            source_datasets=("courses", "staff"),
        )
        second_counts = persist_projection(session, course_only)
        session.commit()

        assert second_counts.stale_relations_deleted == 2
        assert second_counts.stale_entities_deleted == 2
        assert session.query(db.OntologyEntity).count() == 3
        assert session.query(db.OntologyRelation).count() == 2
        assert session.query(db.OntologyEvidence).count() == 2
        assert {
            row.predicate for row in session.query(db.OntologyRelation).all()
        } == {Predicate.PART_OF.value, Predicate.OFFERED_BY.value}
    finally:
        session.close()


def test_source_lineage_validation_rejects_nonexistent_document_key():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine)
    db.Base.metadata.create_all(bind=engine)
    session = factory()
    try:
        projection = build_deterministic_projection(
            [_fixture_documents()[0]],
            source_datasets=("courses",),
        )
        errors = validate_source_lineage(session, projection)
        assert errors and errors[0].startswith("canonical evidence documents missing: 1")
    finally:
        session.close()


def test_rebuild_preserves_reviewed_evidence_in_the_same_source_dataset():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine)
    db.Base.metadata.create_all(bind=engine)
    session = factory()
    try:
        session.add_all(
            [
                db.OntologyEntity(
                    entity_key="manual:subject",
                    entity_type="Notice",
                    canonical_name="검수 공지",
                    extraction_method="reviewed",
                ),
                db.OntologyEntity(
                    entity_key="manual:object",
                    entity_type="Department",
                    canonical_name="검수 학과",
                    extraction_method="reviewed",
                ),
            ]
        )
        relation = db.OntologyRelation(
            relation_key="manual:relation",
            subject_key="manual:subject",
            predicate="APPLIES_TO",
            object_key="manual:object",
            extraction_method="reviewed",
            review_status="approved",
        )
        session.add(relation)
        session.flush()
        session.add(
            db.OntologyEvidence(
                relation_id=relation.id,
                source_dataset="courses",
                document_key="courses:reviewed-source",
                extraction_method="reviewed",
                evidence_locator="$.reviewed_span",
                evidence_text="관리자가 검수한 관계",
            )
        )
        session.commit()

        empty_rebuild = build_deterministic_projection(
            [],
            source_datasets=("courses",),
        )
        persist_projection(session, empty_rebuild)
        session.commit()

        assert session.query(db.OntologyEntity).count() == 2
        assert session.query(db.OntologyRelation).count() == 1
        assert session.query(db.OntologyEvidence).count() == 1
        assert session.query(db.OntologyEvidence).one().extraction_method == "reviewed"
    finally:
        session.close()
