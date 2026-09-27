from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src import database as db
from src.services.ontology import EntityType, Predicate
from src.services.ontology_retrieval import link_entities, run_ontology_shadow


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db.Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _add_relation(
    session,
    *,
    relation_key: str,
    subject_key: str,
    predicate: str,
    object_key: str,
    dataset: str,
    document_key: str,
    review_status: str = "approved",
    status: str = "active",
    published_at: str | None = None,
):
    relation = db.OntologyRelation(
        relation_key=relation_key,
        subject_key=subject_key,
        predicate=predicate,
        object_key=object_key,
        review_status=review_status,
        status=status,
    )
    session.add(relation)
    session.flush()
    session.add(
        db.OntologyEvidence(
            relation_id=relation.id,
            source_dataset=dataset,
            document_key=document_key,
            evidence_locator="normalized_payload.fixture",
            evidence_text="fixture evidence",
            published_at=published_at,
        )
    )


def _seed_graph(session):
    course_document_key = "courses:curriculum-1"
    staff_document_key = "staff:directory-1"
    session.add_all(
        [
            db.SourceDocument(
                dataset="courses",
                source_type="fixture",
                source_id="curriculum-1",
                document_key=course_document_key,
                title="자료구조",
                status="active",
            ),
            db.SourceDocument(
                dataset="staff",
                source_type="fixture",
                source_id="directory-1",
                document_key=staff_document_key,
                title="컴퓨터·AI학부 학사 담당",
                status="updated",
            ),
        ]
    )
    entities = [
        db.OntologyEntity(
            entity_key="college:첨단융합대학",
            entity_type=EntityType.COLLEGE.value,
            canonical_name="첨단융합대학",
        ),
        db.OntologyEntity(
            entity_key="department:컴퓨터ai학부",
            entity_type=EntityType.DEPARTMENT.value,
            canonical_name="컴퓨터·AI학부",
        ),
        db.OntologyEntity(
            entity_key="course:자료구조",
            entity_type=EntityType.COURSE.value,
            canonical_name="자료구조",
            properties_json=json.dumps({"course_code": "CSE1001"}),
        ),
        db.OntologyEntity(
            entity_key="person:김교직원",
            entity_type=EntityType.PERSON.value,
            canonical_name="김**",
        ),
    ]
    session.add_all(entities)
    session.add(
        db.OntologyAlias(
            alias_key="컴공",
            alias="컴공",
            entity_key="department:컴퓨터ai학부",
            source_dataset="department_aliases",
        )
    )
    session.flush()
    _add_relation(
        session,
        relation_key="relation:department-college",
        subject_key="department:컴퓨터ai학부",
        predicate=Predicate.PART_OF.value,
        object_key="college:첨단융합대학",
        dataset="courses",
        document_key=course_document_key,
    )
    _add_relation(
        session,
        relation_key="relation:course-department",
        subject_key="course:자료구조",
        predicate=Predicate.OFFERED_BY.value,
        object_key="department:컴퓨터ai학부",
        dataset="courses",
        document_key=course_document_key,
    )
    _add_relation(
        session,
        relation_key="relation:person-department",
        subject_key="person:김교직원",
        predicate=Predicate.WORKS_AT.value,
        object_key="department:컴퓨터ai학부",
        dataset="staff",
        document_key=staff_document_key,
    )
    session.commit()
    return course_document_key, staff_document_key


def test_course_code_alone_links_to_exact_course_relation():
    session = _session()
    try:
        course_document_key, _ = _seed_graph(session)
        result = run_ontology_shadow(
            session, "CSE1001 어느 학과에서 개설해?", ["courses"]
        )
        assert any(
            item.match_method == "course_code"
            for item in result.linked_entities
        )
        assert result.document_keys == (course_document_key,)
    finally:
        session.close()


def _seed_rules_graph(session):
    document_2021 = "rules:guide:2021:statistics"
    document_2022 = "rules:guide:2022:statistics"
    session.add_all(
        [
            db.SourceDocument(
                dataset="rules",
                source_type="entry_year_guide_pdf",
                source_id="2021-statistics",
                document_key=document_2021,
                title="2021학번 이과대학 졸업기준",
                status="active",
            ),
            db.SourceDocument(
                dataset="rules",
                source_type="entry_year_guide_pdf",
                source_id="2022-statistics",
                document_key=document_2022,
                title="2022학번 이과대학 졸업기준",
                status="active",
            ),
            db.OntologyEntity(
                entity_key="department:통계학과",
                entity_type=EntityType.DEPARTMENT.value,
                canonical_name="통계학과",
            ),
            db.OntologyEntity(
                entity_key="cohort:2021",
                entity_type=EntityType.ENTRY_COHORT.value,
                canonical_name="2021학번",
            ),
            db.OntologyEntity(
                entity_key="cohort:2022",
                entity_type=EntityType.ENTRY_COHORT.value,
                canonical_name="2022학번",
            ),
            db.OntologyEntity(
                entity_key="requirement:2021:statistics",
                entity_type=EntityType.ACADEMIC_REQUIREMENT.value,
                canonical_name="2021학번 이과대학 졸업기준",
                properties_json=json.dumps({"requirement_kind": "graduation"}),
            ),
            db.OntologyEntity(
                entity_key="requirement:2022:statistics",
                entity_type=EntityType.ACADEMIC_REQUIREMENT.value,
                canonical_name="2022학번 이과대학 졸업기준",
                properties_json=json.dumps({"requirement_kind": "graduation"}),
            ),
        ]
    )
    session.add(
        db.OntologyAlias(
            alias_key="22학번",
            alias="22학번",
            entity_key="cohort:2022",
            source_dataset="rules",
        )
    )
    session.flush()
    for year, document_key in (("2021", document_2021), ("2022", document_2022)):
        requirement_key = f"requirement:{year}:statistics"
        _add_relation(
            session,
            relation_key=f"relation:{year}:valid",
            subject_key=requirement_key,
            predicate=Predicate.VALID_FOR_ENTRY.value,
            object_key=f"cohort:{year}",
            dataset="rules",
            document_key=document_key,
        )
        _add_relation(
            session,
            relation_key=f"relation:{year}:governs",
            subject_key=requirement_key,
            predicate=Predicate.GOVERNS.value,
            object_key="department:통계학과",
            dataset="rules",
            document_key=document_key,
        )
    session.commit()
    return document_2021, document_2022


def _seed_schedule_graph(session):
    spring_document = "schedule:2027-spring-registration"
    fall_document = "schedule:2026-fall-registration"
    session.add_all(
        [
            db.SourceDocument(
                dataset="schedule",
                source_type="academic_schedule",
                source_id="2027-spring-registration",
                document_key=spring_document,
                title="2027학년도 1학기 학부 수강 신청",
                status="active",
            ),
            db.SourceDocument(
                dataset="schedule",
                source_type="academic_schedule",
                source_id="2026-fall-registration",
                document_key=fall_document,
                title="2026학년도 2학기 학부 수강 신청",
                status="active",
            ),
            db.OntologyEntity(
                entity_key="event:spring-registration",
                entity_type=EntityType.ACADEMIC_EVENT.value,
                canonical_name="2027학년도 1학기 학부 수강 신청",
            ),
            db.OntologyEntity(
                entity_key="event:fall-registration",
                entity_type=EntityType.ACADEMIC_EVENT.value,
                canonical_name="2026학년도 2학기 학부 수강 신청",
            ),
            db.OntologyEntity(
                entity_key="range:spring-registration",
                entity_type=EntityType.DATE_RANGE.value,
                canonical_name="2027-02-01~2027-02-05",
            ),
            db.OntologyEntity(
                entity_key="range:fall-registration",
                entity_type=EntityType.DATE_RANGE.value,
                canonical_name="2026-08-03~2026-08-07",
            ),
        ]
    )
    for event_key, semester, document_key in (
        ("event:spring-registration", "1", spring_document),
        ("event:fall-registration", "2", fall_document),
    ):
        session.add_all(
            [
                db.OntologyAlias(
                    alias_key=f"{semester}학기수강신청",
                    alias=f"{semester}학기 수강신청",
                    entity_key=event_key,
                    source_dataset="schedule",
                ),
                db.OntologyAlias(
                    alias_key="수강신청",
                    alias="수강신청",
                    entity_key=event_key,
                    source_dataset="schedule",
                ),
            ]
        )
        _add_relation(
            session,
            relation_key=f"relation:{semester}:registration:occurs",
            subject_key=event_key,
            predicate=Predicate.OCCURS_DURING.value,
            object_key=f"range:{'spring' if semester == '1' else 'fall'}-registration",
            dataset="schedule",
            document_key=document_key,
        )
    session.commit()
    return spring_document, fall_document


def test_linker_matches_reviewed_alias_without_linking_masked_person():
    session = _session()
    try:
        _seed_graph(session)
        linked = link_entities(session, "컴공 담당자 연락처", ["staff"])

        assert [item.entity_key for item in linked] == [
            "department:컴퓨터ai학부"
        ]
        assert linked[0].match_method == "alias"
        assert linked[0].matched_text == "컴공"
    finally:
        session.close()


def test_rules_shadow_ranks_document_matching_both_cohort_and_department_first():
    session = _session()
    try:
        document_2021, document_2022 = _seed_rules_graph(session)

        result = run_ontology_shadow(
            session,
            "2022학번 통계학과 졸업 최저학점",
            ["rules"],
            max_hops=2,
        )

        assert {
            (item.entity_type, item.canonical_name)
            for item in result.linked_entities
        } >= {
            (EntityType.ENTRY_COHORT.value, "2022학번"),
            (EntityType.DEPARTMENT.value, "통계학과"),
        }
        assert {item.predicate for item in result.traversed_relations} == {
            Predicate.GOVERNS.value,
            Predicate.VALID_FOR_ENTRY.value,
        }
        assert result.document_keys == (document_2022,)
        assert document_2021 not in result.document_keys
    finally:
        session.close()


def test_rules_shadow_links_short_cohort_alias():
    session = _session()
    try:
        _, document_2022 = _seed_rules_graph(session)

        result = run_ontology_shadow(
            session,
            "22학번 통계학과 졸업요건",
            ["rules"],
        )

        cohort = next(
            item
            for item in result.linked_entities
            if item.entity_type == EntityType.ENTRY_COHORT.value
        )
        assert cohort.match_method == "alias"
        assert cohort.matched_text == "22학번"
        assert result.document_keys[0] == document_2022
    finally:
        session.close()


def test_rules_shadow_does_not_choose_a_guide_year_without_explicit_cohort():
    session = _session()
    try:
        _seed_rules_graph(session)

        result = run_ontology_shadow(
            session,
            "통계학과 졸업요건",
            ["rules"],
        )

        assert any(
            item.entity_type == EntityType.DEPARTMENT.value
            for item in result.linked_entities
        )
        assert result.document_keys == ()
    finally:
        session.close()


def test_rules_shadow_requires_requirement_intent_and_target_for_graduation():
    session = _session()
    try:
        _seed_rules_graph(session)

        bare_cohort = run_ontology_shadow(session, "22학번은?", ["rules"])
        schedule = run_ontology_shadow(
            session,
            "2022학번 수강신청 기간",
            ["rules"],
        )
        missing_target = run_ontology_shadow(
            session,
            "2022학번 졸업하려면 몇 학점 들어야 해?",
            ["rules"],
        )
        early_graduation = run_ontology_shadow(
            session,
            "2022학번 통계학과 조기졸업 가능해?",
            ["rules"],
        )

        assert bare_cohort.document_keys == ()
        assert schedule.document_keys == ()
        assert missing_target.document_keys == ()
        assert early_graduation.document_keys == ()
    finally:
        session.close()


def test_rules_shadow_filters_same_cohort_documents_by_explicit_requirement_kind():
    session = _session()
    try:
        _, graduation_document = _seed_rules_graph(session)
        general_document = "rules:guide:2022:general-education"
        session.add_all(
            [
                db.SourceDocument(
                    dataset="rules",
                    source_type="entry_year_guide_pdf",
                    source_id="2022-general-education",
                    document_key=general_document,
                    title="2022학번 교양교육과정 이수 기준",
                    status="active",
                ),
                db.OntologyEntity(
                    entity_key="requirement:2022:general-education",
                    entity_type=EntityType.ACADEMIC_REQUIREMENT.value,
                    canonical_name="2022학번 교양교육과정 이수 기준",
                    properties_json=json.dumps(
                        {"requirement_kind": "general_education"}
                    ),
                ),
            ]
        )
        session.flush()
        _add_relation(
            session,
            relation_key="relation:2022:general-education:valid",
            subject_key="requirement:2022:general-education",
            predicate=Predicate.VALID_FOR_ENTRY.value,
            object_key="cohort:2022",
            dataset="rules",
            document_key=general_document,
        )
        session.commit()

        result = run_ontology_shadow(
            session,
            "2022학번 교양교육과정 이수 기준",
            ["rules"],
        )

        assert result.document_keys == (general_document,)
        assert graduation_document not in result.document_keys
    finally:
        session.close()


def test_schedule_shadow_prefers_specific_semester_alias_over_generic_event_alias():
    session = _session()
    try:
        spring_document, fall_document = _seed_schedule_graph(session)

        result = run_ontology_shadow(
            session,
            "2학기 수강신청 기간은 언제야?",
            ["schedule"],
        )

        assert [item.entity_key for item in result.linked_entities] == [
            "event:fall-registration"
        ]
        assert {item.predicate for item in result.traversed_relations} == {
            Predicate.OCCURS_DURING.value
        }
        assert result.document_keys == (fall_document,)
        assert spring_document not in result.document_keys
    finally:
        session.close()


def test_schedule_shadow_keeps_both_semesters_for_generic_topic_query():
    session = _session()
    try:
        spring_document, fall_document = _seed_schedule_graph(session)

        result = run_ontology_shadow(
            session,
            "수강신청 언제야?",
            ["schedule"],
        )

        assert {item.entity_key for item in result.linked_entities} == {
            "event:spring-registration",
            "event:fall-registration",
        }
        assert set(result.document_keys) == {spring_document, fall_document}
    finally:
        session.close()


def test_schedule_shadow_does_not_use_registration_dates_for_policy_question():
    session = _session()
    try:
        _seed_schedule_graph(session)

        result = run_ontology_shadow(
            session,
            "수강신청한 두 과목의 시험 시간이 겹치면 어떻게 해야 하나요?",
            ["schedule"],
        )

        assert result.document_keys == ()
    finally:
        session.close()


def test_notice_shadow_returns_explicit_organization_notices_newest_first():
    session = _session()
    try:
        organization_key = "organization:디지털정보처"
        older_document = "notices:digital-information:older"
        newer_document = "notices:digital-information:newer"
        session.add_all(
            [
                db.OntologyEntity(
                    entity_key=organization_key,
                    entity_type=EntityType.ORGANIZATION.value,
                    canonical_name="디지털정보처",
                ),
                db.OntologyEntity(
                    entity_key="notice:older",
                    entity_type=EntityType.NOTICE.value,
                    canonical_name="[디지털정보처] 이전 점검 안내",
                ),
                db.OntologyEntity(
                    entity_key="notice:newer",
                    entity_type=EntityType.NOTICE.value,
                    canonical_name="[디지털정보처] 최신 점검 안내",
                ),
                db.SourceDocument(
                    dataset="notices",
                    source_type="html_notice",
                    source_id="older",
                    document_key=older_document,
                    title="[디지털정보처] 이전 점검 안내",
                    published_at="2026-01-01",
                    status="active",
                ),
                db.SourceDocument(
                    dataset="notices",
                    source_type="html_notice",
                    source_id="newer",
                    document_key=newer_document,
                    title="[디지털정보처] 최신 점검 안내",
                    published_at="2026-08-01",
                    status="active",
                ),
            ]
        )
        session.flush()
        _add_relation(
            session,
            relation_key="relation:notice:older",
            subject_key="notice:older",
            predicate=Predicate.MENTIONS_ORGANIZATION.value,
            object_key=organization_key,
            dataset="notices",
            document_key=older_document,
            published_at="2026-01-01",
        )
        _add_relation(
            session,
            relation_key="relation:notice:newer",
            subject_key="notice:newer",
            predicate=Predicate.MENTIONS_ORGANIZATION.value,
            object_key=organization_key,
            dataset="notices",
            document_key=newer_document,
            published_at="2026-08-01",
        )
        session.commit()

        result = run_ontology_shadow(
            session,
            "디지털정보처 최근 공지 알려줘",
            ["notices"],
        )

        assert [item.predicate for item in result.traversed_relations] == [
            Predicate.MENTIONS_ORGANIZATION.value,
            Predicate.MENTIONS_ORGANIZATION.value,
        ]
        assert result.document_keys == (newer_document, older_document)

        graduation = run_ontology_shadow(
            session,
            "디지털정보처 졸업학점 알려줘",
            ["notices"],
        )
        contact = run_ontology_shadow(
            session,
            "디지털정보처 담당자 전화번호를 공지 발송용으로 알려줘",
            ["notices"],
        )
        assert graduation.document_keys == ()
        assert contact.document_keys == ()
    finally:
        session.close()


def test_course_shadow_reaches_course_in_two_hops_and_excludes_staff_edge():
    session = _session()
    try:
        course_document_key, _ = _seed_graph(session)
        result = run_ontology_shadow(
            session,
            "첨단융합대학 과목 알려줘",
            ["courses"],
            max_hops=2,
        )

        assert [item.entity_key for item in result.linked_entities] == [
            "college:첨단융합대학"
        ]
        assert {item.predicate for item in result.traversed_relations} == {
            Predicate.PART_OF.value,
            Predicate.OFFERED_BY.value,
        }
        assert any(
            item.predicate == Predicate.OFFERED_BY.value and item.depth == 2
            for item in result.traversed_relations
        )
        assert result.document_keys == (course_document_key,)
    finally:
        session.close()


def test_exact_course_and_department_seed_prioritizes_direct_evidence():
    session = _session()
    try:
        course_document_key, _ = _seed_graph(session)
        result = run_ontology_shadow(
            session,
            "컴퓨터·AI학부 자료구조 과목 정보",
            ["courses"],
            max_hops=2,
        )

        assert {item.entity_type for item in result.linked_entities} == {
            EntityType.COURSE.value,
            EntityType.DEPARTMENT.value,
        }
        assert result.traversed_relations[0].predicate == Predicate.OFFERED_BY.value
        assert result.document_keys[0] == course_document_key
    finally:
        session.close()


def test_linker_excludes_course_shaped_curriculum_heading_rows():
    session = _session()
    try:
        _seed_graph(session)
        session.add(
            db.OntologyEntity(
                entity_key="course:heading",
                entity_type=EntityType.COURSE.value,
                canonical_name="컴퓨터·AI학부",
                properties_json=json.dumps({"course_code": "CSC****, CSE****"}),
            )
        )
        session.commit()

        linked = link_entities(
            session,
            "컴퓨터·AI학부 과목 알려줘",
            ["courses"],
        )

        assert [item.entity_key for item in linked] == [
            "department:컴퓨터ai학부"
        ]
    finally:
        session.close()


def test_linker_prefers_longer_explicit_course_name_over_nested_course_name():
    session = _session()
    try:
        session.add_all(
            [
                db.OntologyEntity(
                    entity_key="course:경찰학",
                    entity_type=EntityType.COURSE.value,
                    canonical_name="경찰학",
                    properties_json=json.dumps({"course_code": "PAS2015"}),
                ),
                db.OntologyEntity(
                    entity_key="course:경찰학세미나",
                    entity_type=EntityType.COURSE.value,
                    canonical_name="경찰학세미나",
                    properties_json=json.dumps({"course_code": "PAS4044"}),
                ),
            ]
        )
        session.commit()

        linked = link_entities(
            session,
            "PAS4044 경찰학세미나 과목 정보",
            ["courses"],
        )

        assert [item.canonical_name for item in linked] == ["경찰학세미나"]
    finally:
        session.close()


def test_linker_prioritizes_explicit_course_code_for_same_named_courses():
    session = _session()
    try:
        session.add_all(
            [
                db.OntologyEntity(
                    entity_key="course:civ4008",
                    entity_type=EntityType.COURSE.value,
                    canonical_name="강구조공학",
                    properties_json=json.dumps({"course_code": "CIV4008"}),
                ),
                db.OntologyEntity(
                    entity_key="course:civ4079",
                    entity_type=EntityType.COURSE.value,
                    canonical_name="강구조공학",
                    properties_json=json.dumps({"course_code": "CIV4079"}),
                ),
            ]
        )
        session.commit()

        linked = link_entities(
            session,
            "CIV4079 강구조공학 과목 정보",
            ["courses"],
        )

        assert [item.entity_key for item in linked[:2]] == [
            "course:civ4079",
            "course:civ4008",
        ]
    finally:
        session.close()


def test_course_name_index_matches_titles_and_aliases_and_rotates_on_build(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ontology.db'}")
    db.Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add_all([
            db.OntologyEntity(
                entity_key="course:csc2007", entity_type=EntityType.COURSE.value,
                canonical_name="자료구조",
                properties_json=json.dumps({"course_code": "CSC2007"}),
            ),
            db.OntologyEntity(
                entity_key="course:csc3001", entity_type=EntityType.COURSE.value,
                canonical_name="운영체제",
                properties_json=json.dumps({"course_code": "CSC3001"}),
            ),
            db.OntologyAlias(
                alias_key="데이터구조", alias="데이터구조",
                entity_key="course:csc2007", source_dataset="courses",
            ),
            db.OntologyBuildRun(status="success", build_revision="ontology:one"),
        ])
        session.commit()

        assert [item.entity_key for item in link_entities(
            session, "데이터구조 어느 학과 과목이야?", ["courses"],
        )] == ["course:csc2007"]
        assert [item.entity_key for item in link_entities(
            session, "운영체제 어느 학과 과목이야?", ["courses"],
        )] == ["course:csc3001"]

        session.add_all([
            db.OntologyEntity(
                entity_key="course:csc4001", entity_type=EntityType.COURSE.value,
                canonical_name="컴파일러",
                properties_json=json.dumps({"course_code": "CSC4001"}),
            ),
            db.OntologyBuildRun(status="success", build_revision="ontology:two"),
        ])
        session.commit()
        assert [item.entity_key for item in link_entities(
            session, "컴파일러 어느 학과 과목이야?", ["courses"],
        )] == ["course:csc4001"]
    finally:
        session.close()
        engine.dispose()


def test_linker_deduplicates_same_type_and_canonical_name_and_ignores_scope_terms():
    session = _session()
    try:
        session.add_all(
            [
                db.OntologyEntity(
                    entity_key="organization:정각원:1",
                    entity_type=EntityType.ORGANIZATION.value,
                    canonical_name="정각원",
                ),
                db.OntologyEntity(
                    entity_key="organization:정각원:2",
                    entity_type=EntityType.ORGANIZATION.value,
                    canonical_name="정각원",
                ),
                db.OntologyEntity(
                    entity_key="organization:서울캠퍼스",
                    entity_type=EntityType.ORGANIZATION.value,
                    canonical_name="서울캠퍼스",
                ),
                db.OntologyEntity(
                    entity_key="course:교육과정",
                    entity_type=EntityType.COURSE.value,
                    canonical_name="교육과정",
                    properties_json=json.dumps({"course_code": "EDU0001"}),
                ),
            ]
        )
        session.commit()

        linked = link_entities(
            session,
            "서울캠퍼스 정각원 교육과정 담당자",
            ["courses", "staff"],
        )

        assert [(item.entity_type, item.canonical_name) for item in linked] == [
            (EntityType.ORGANIZATION.value, "정각원")
        ]
    finally:
        session.close()


def test_linker_uses_one_seed_for_same_named_college_and_department():
    session = _session()
    try:
        session.add_all(
            [
                db.OntologyEntity(
                    entity_key="college:공과대학",
                    entity_type=EntityType.COLLEGE.value,
                    canonical_name="공과대학",
                ),
                db.OntologyEntity(
                    entity_key="department:공과대학",
                    entity_type=EntityType.DEPARTMENT.value,
                    canonical_name="공과대학",
                ),
            ]
        )
        session.commit()

        linked = link_entities(session, "2022학번 공과대학 졸업기준", ["rules"])

        assert [(item.entity_type, item.canonical_name) for item in linked] == [
            (EntityType.COLLEGE.value, "공과대학")
        ]
    finally:
        session.close()


def test_department_seed_does_not_expand_upward_then_into_sibling_department():
    session = _session()
    try:
        course_document_key, staff_document_key = _seed_graph(session)
        sibling_document_key = "staff:sibling-directory"
        session.add_all(
            [
                db.SourceDocument(
                    dataset="staff",
                    source_type="fixture",
                    source_id="sibling-directory",
                    document_key=sibling_document_key,
                    title="통계학과 담당자",
                    status="active",
                ),
                db.OntologyEntity(
                    entity_key="department:통계학과",
                    entity_type=EntityType.DEPARTMENT.value,
                    canonical_name="통계학과",
                ),
                db.OntologyEntity(
                    entity_key="person:통계담당",
                    entity_type=EntityType.PERSON.value,
                    canonical_name="박**",
                ),
            ]
        )
        session.flush()
        _add_relation(
            session,
            relation_key="relation:sibling-college",
            subject_key="department:통계학과",
            predicate=Predicate.PART_OF.value,
            object_key="college:첨단융합대학",
            dataset="staff",
            document_key=sibling_document_key,
        )
        _add_relation(
            session,
            relation_key="relation:sibling-person",
            subject_key="person:통계담당",
            predicate=Predicate.WORKS_AT.value,
            object_key="department:통계학과",
            dataset="staff",
            document_key=sibling_document_key,
        )
        session.commit()

        result = run_ontology_shadow(
            session,
            "컴퓨터·AI학부 담당자",
            ["staff"],
            max_hops=2,
        )

        assert staff_document_key in result.document_keys
        assert sibling_document_key not in result.document_keys
        assert course_document_key not in result.document_keys
    finally:
        session.close()


def test_staff_shadow_traverses_inverse_works_at_and_filters_course_evidence():
    session = _session()
    try:
        _, staff_document_key = _seed_graph(session)
        result = run_ontology_shadow(
            session,
            "첨단융합대학 담당자",
            ["staff"],
            max_hops=2,
        )

        assert {item.predicate for item in result.traversed_relations} == {
            Predicate.PART_OF.value,
            Predicate.WORKS_AT.value,
        }
        assert any(
            item.predicate == Predicate.WORKS_AT.value and item.depth == 2
            for item in result.traversed_relations
        )
        assert result.document_keys == (staff_document_key,)
    finally:
        session.close()


def test_shadow_ignores_unapproved_relation_and_respects_relation_cap():
    session = _session()
    try:
        course_document_key, _ = _seed_graph(session)
        _add_relation(
            session,
            relation_key="relation:unapproved",
            subject_key="course:자료구조",
            predicate=Predicate.OFFERED_BY.value,
            object_key="department:컴퓨터ai학부",
            dataset="courses",
            document_key=course_document_key,
            review_status="pending",
        )
        session.commit()

        result = run_ontology_shadow(
            session,
            "컴퓨터·AI학부 과목",
            ["courses"],
            max_hops=2,
            max_relations=1,
        )

        assert len(result.traversed_relations) == 1
        assert all(
            item.relation_key != "relation:unapproved"
            for item in result.traversed_relations
        )
    finally:
        session.close()


def test_rag_log_correlates_retrieved_documents_with_shadow_candidates(monkeypatch):
    from api import rag_service

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine)
    db.Base.metadata.create_all(bind=engine)
    session = factory()
    try:
        session.add(
            db.OntologyShadowLog(
                request_id="shadow-overlap-request",
                session_id="shadow-overlap-session",
                query_hash="fixture-hash",
                route_json='["courses"]',
                document_keys_json='["courses:curriculum-1", "courses:other"]',
            )
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr(rag_service, "SessionLocal", factory)
    monkeypatch.setattr(rag_service.rag_config, "RAG_ONTOLOGY_SHADOW_ENABLED", True)
    source = rag_service.SourceChunk(
        source="courses",
        metadata={"document_key": "courses:curriculum-1"},
        snippet="자료구조 교과목",
    )
    rag_service._save_rag_evaluation_log(
        request_id="shadow-overlap-request",
        session_id="shadow-overlap-session",
        question="자료구조",
        expanded_question="자료구조",
        route=["courses"],
        answer="fixture answer",
        fallback_triggered=False,
        fallback_reason=None,
        date_filter_applied=False,
        date_filter_relaxed=False,
        analysis_intent="courses",
        analysis_entities_json=None,
        analysis_time_focus=None,
        analysis_search_queries_json=None,
        analysis_needs_clarification=False,
        analysis_clarification_reason=None,
        analysis_used=False,
        analysis_failed=False,
        matched_queries_json=None,
        top_hybrid_score=1.0,
        sources=[source],
    )

    verification = factory()
    try:
        shadow = verification.query(db.OntologyShadowLog).one()
        retrieval = verification.query(db.RagRetrievalLog).one()
        assert shadow.retrieved_document_count == 1
        assert shadow.overlap_document_count == 1
        assert json.loads(shadow.overlap_document_keys_json) == [
            "courses:curriculum-1"
        ]
        assert retrieval.document_key == "courses:curriculum-1"
    finally:
        verification.close()
