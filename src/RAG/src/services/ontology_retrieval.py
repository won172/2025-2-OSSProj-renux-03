"""Read-only ontology retrieval used by the Phase 2 shadow path.

The functions in this module never mutate the canonical graph and never alter
the vector/sparse retrieval inputs.  They link explicit entity mentions, walk
only approved active relationships for at most two hops, and return canonical
``SourceDocument.document_key`` values attached as relation evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
import threading
from typing import Iterable, Sequence

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from src.database import (
    OntologyAlias,
    OntologyBuildRun,
    OntologyEntity,
    OntologyEvidence,
    OntologyRelation,
    SourceDocument,
)
from src.services.ontology import EntityType, Predicate, identity_token


PUBLISHED_SOURCE_STATUSES = frozenset({"active", "updated"})
_EXPLICIT_COURSE_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]{2,6}\d{3,5}(?![A-Za-z0-9])"
)
_GENERIC_LINK_TOKENS = frozenset(
    {
        "대학",
        "대학교",
        "동국대학교",
        "서울캠퍼스",
        "학부",
        "학과",
        "전공",
        "과목",
        "교과목",
        "교육과정",
        "전공과목",
        "강의",
        "부서",
        "조직",
        "센터",
        "팀",
    }
)
_course_index_lock = threading.Lock()
_course_indexes: dict[tuple[int, int], tuple[tuple[str, str], ...]] = {}


@dataclass(frozen=True)
class LinkedEntity:
    entity_key: str
    entity_type: str
    canonical_name: str
    matched_text: str
    match_method: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TraversedRelation:
    relation_key: str
    subject_key: str
    predicate: str
    object_key: str
    depth: int

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class OntologyShadowResult:
    linked_entities: tuple[LinkedEntity, ...]
    traversed_relations: tuple[TraversedRelation, ...]
    document_keys: tuple[str, ...]
    max_hops: int

    def as_dict(self) -> dict:
        return {
            "linked_entities": [item.as_dict() for item in self.linked_entities],
            "traversed_relations": [
                item.as_dict() for item in self.traversed_relations
            ],
            "document_keys": list(self.document_keys),
            "max_hops": self.max_hops,
        }


@dataclass(frozen=True)
class _LinkCandidate:
    entity_key: str
    entity_type: str
    canonical_name: str
    matched_text: str
    match_method: str
    matched_token: str
    course_code_match: bool = False


def _allowed_entity_types(route: Sequence[str]) -> frozenset[str]:
    route_set = set(route)
    allowed = {
        EntityType.DEPARTMENT.value,
        EntityType.COLLEGE.value,
        EntityType.ORGANIZATION.value,
    }
    if "courses" in route_set:
        allowed.add(EntityType.COURSE.value)
    if "rules" in route_set:
        allowed.update(
            {
                EntityType.ACADEMIC_REQUIREMENT.value,
                EntityType.ENTRY_COHORT.value,
            }
        )
    if "schedule" in route_set:
        allowed.add(EntityType.ACADEMIC_EVENT.value)
    # Masked names are not safe query identities, so Person is intentionally
    # excluded even on staff routes. A department/organization seed can reach
    # staff records through the inverse WORKS_AT edge instead.
    return frozenset(allowed)


def _allowed_predicates(route: Sequence[str]) -> frozenset[str]:
    route_set = set(route)
    predicates: set[str] = set()
    if route_set.intersection({"courses", "staff"}):
        predicates.add(Predicate.PART_OF.value)
    if "courses" in route_set:
        predicates.add(Predicate.OFFERED_BY.value)
    if "staff" in route_set:
        predicates.add(Predicate.WORKS_AT.value)
    if "rules" in route_set:
        predicates.update(
            {
                Predicate.GOVERNS.value,
                Predicate.VALID_FOR_ENTRY.value,
            }
        )
    if "notices" in route_set:
        predicates.add(Predicate.MENTIONS_ORGANIZATION.value)
    if "schedule" in route_set:
        predicates.update(
            {
                Predicate.OCCURS_DURING.value,
                Predicate.MANAGED_BY.value,
            }
        )
    return frozenset(predicates)


def _allowed_evidence_datasets(route: Sequence[str]) -> frozenset[str]:
    return frozenset(
        dataset
        for dataset in ("courses", "notices", "rules", "schedule", "staff")
        if dataset in route
    )


def _is_linkable_token(token: str, *, reviewed_alias: bool = False) -> bool:
    if token in _GENERIC_LINK_TOKENS:
        return False
    if len(token) >= 3:
        return True
    # Short forms such as "컴공" are useful only when they came from the
    # reviewed alias table. Two-character Latin fragments (for example "ai")
    # remain too ambiguous for substring matching.
    return reviewed_alias and len(token) == 2 and all("가" <= char <= "힣" for char in token)


def _entity_is_linkable(entity: OntologyEntity) -> bool:
    if entity.entity_type != EntityType.COURSE.value:
        return True
    try:
        properties = json.loads(entity.properties_json or "{}")
    except (TypeError, ValueError):
        return False
    course_code = str(properties.get("course_code") or "").strip()
    # Curriculum source files also contain heading/placeholder rows represented
    # as course-shaped records (for example course_name="컴퓨터·AI학부" with a
    # wildcard code). They remain in the projection for provenance but must not
    # become query-link candidates.
    return bool(course_code) and "*" not in course_code


def _course_code_token(entity: OntologyEntity) -> str:
    if entity.entity_type != EntityType.COURSE.value:
        return ""
    try:
        properties = json.loads(entity.properties_json or "{}")
    except (TypeError, ValueError):
        return ""
    return identity_token(properties.get("course_code"))


def _course_name_keys(session: Session, query_token: str) -> tuple[str, ...] | None:
    """Match titles/aliases in a revision-scoped, lightweight name index.

    A successful build is the publication boundary. Ad-hoc/in-memory graphs
    retain the uncached path so edits inside a test transaction stay visible.
    """
    bind = session.get_bind()
    engine = getattr(bind, "engine", bind)
    database = getattr(getattr(engine, "url", None), "database", None)
    if not database or database == ":memory:":
        return None
    build_id = (
        session.query(OntologyBuildRun.id)
        .filter(OntologyBuildRun.status == "success")
        .order_by(OntologyBuildRun.id.desc())
        .limit(1)
        .scalar()
    )
    if build_id is None:
        return None
    cache_key = (id(engine), int(build_id))
    with _course_index_lock:
        index = _course_indexes.get(cache_key)
    if index is None:
        course_type = EntityType.COURSE.value
        rows = (
            session.query(OntologyEntity.entity_key, OntologyEntity.canonical_name)
            .filter(
                OntologyEntity.status == "active",
                OntologyEntity.entity_type == course_type,
            )
            .all()
        )
        aliases = (
            session.query(OntologyAlias.entity_key, OntologyAlias.alias)
            .join(OntologyEntity, OntologyAlias.entity_key == OntologyEntity.entity_key)
            .filter(
                OntologyAlias.status == "active",
                OntologyEntity.status == "active",
                OntologyEntity.entity_type == course_type,
            )
            .all()
        )
        built = tuple(
            (token, key)
            for key, name in (*rows, *aliases)
            if (token := identity_token(name))
        )
        with _course_index_lock:
            index = _course_indexes.setdefault(cache_key, built)
            # Retain only the current and immediately previous publication.
            if len(_course_indexes) > 2:
                oldest = next(iter(_course_indexes))
                if oldest != cache_key:
                    _course_indexes.pop(oldest, None)
    return tuple(dict.fromkeys(
        key for token, key in index if token in query_token
    ))


def link_entities(
    session: Session,
    query: str,
    route: Sequence[str],
    *,
    max_entities: int = 8,
) -> tuple[LinkedEntity, ...]:
    """Link explicit canonical names and reviewed aliases in ``query``.

    Phase 2 intentionally has no fuzzy or embedding match.  A normalized
    lexeme must occur in the normalized query, and generic university words
    are excluded to keep traversal degree bounded.
    """

    query_token = identity_token(query)
    if not query_token or max_entities <= 0:
        return ()

    entity_types = _allowed_entity_types(route)
    explicit_codes = {
        match.group(0).upper()
        for match in _EXPLICIT_COURSE_CODE_RE.finditer(query)
    } if EntityType.COURSE.value in entity_types else set()
    entity_query = session.query(OntologyEntity).filter(
        OntologyEntity.status == "active",
        OntologyEntity.entity_type.in_(entity_types),
    )
    if explicit_codes:
        # Exact course-code lookup happens in SQLite. Hydrating every course
        # entity to parse its JSON on each request was the dominant cost for
        # code questions. Keep the other entity types for department scoping.
        course_rows = entity_query.filter(
            OntologyEntity.entity_type == EntityType.COURSE.value,
            func.upper(func.json_extract(
                OntologyEntity.properties_json, "$.course_code"
            )).in_(explicit_codes),
        ).all()
        other_rows = entity_query.filter(
            OntologyEntity.entity_type != EntityType.COURSE.value
        ).all()
        entity_rows = other_rows + course_rows
        named_courses = {
            row.canonical_name
            for row in course_rows
            if identity_token(row.canonical_name) in query_token
        }
        if named_courses:
            seen_course_keys = {row.entity_key for row in course_rows}
            entity_rows.extend(
                row for row in entity_query.filter(
                    OntologyEntity.entity_type == EntityType.COURSE.value,
                    OntologyEntity.canonical_name.in_(named_courses),
                ).all()
                if row.entity_key not in seen_course_keys
            )
        elif not course_rows:
            # A mistyped/unknown code may accompany a valid course name.
            title_keys = _course_name_keys(session, query_token)
            entity_rows.extend(entity_query.filter(
                OntologyEntity.entity_type == EntityType.COURSE.value,
                OntologyEntity.entity_key.in_(title_keys),
            ).all() if title_keys is not None else entity_query.filter(
                OntologyEntity.entity_type == EntityType.COURSE.value,
            ).all())
    else:
        title_keys = (
            _course_name_keys(session, query_token)
            if EntityType.COURSE.value in entity_types else None
        )
        if title_keys is None:
            entity_rows = entity_query.all()
        else:
            entity_rows = entity_query.filter(or_(
                OntologyEntity.entity_type != EntityType.COURSE.value,
                OntologyEntity.entity_key.in_(title_keys),
            )).all()
    candidates: list[_LinkCandidate] = []
    entities_by_key = {row.entity_key: row for row in entity_rows}

    for row in entity_rows:
        if not _entity_is_linkable(row):
            continue
        token = identity_token(row.canonical_name)
        course_code_token = _course_code_token(row)
        # An explicit course code is a stronger identity than a similar course
        # title, and must also work when the question contains no course name.
        code_match = bool(
            course_code_token
            and re.search(
                rf"(?<![A-Za-z0-9]){re.escape(course_code_token)}(?![A-Za-z0-9])",
                query,
                flags=re.IGNORECASE,
            )
        )
        title_match = _is_linkable_token(token) and token in query_token
        if code_match and not title_match:
            candidates.append(
                _LinkCandidate(
                    entity_key=row.entity_key,
                    entity_type=row.entity_type,
                    canonical_name=row.canonical_name,
                    matched_text=course_code_token,
                    match_method="course_code",
                    matched_token=course_code_token,
                    course_code_match=True,
                )
            )
        if title_match:
            candidates.append(
                _LinkCandidate(
                    entity_key=row.entity_key,
                    entity_type=row.entity_type,
                    canonical_name=row.canonical_name,
                    matched_text=row.canonical_name,
                    match_method="canonical",
                    matched_token=token,
                    course_code_match=code_match,
                )
            )

    if entities_by_key:
        alias_rows = (
            session.query(OntologyAlias)
            .filter(
                OntologyAlias.status == "active",
                OntologyAlias.entity_key.in_(tuple(entities_by_key)),
            )
            .all()
        )
        for alias in alias_rows:
            token = identity_token(alias.alias)
            entity = entities_by_key.get(alias.entity_key)
            if (
                entity is None
                or not _is_linkable_token(token, reviewed_alias=True)
                or token not in query_token
            ):
                continue
            candidates.append(
                _LinkCandidate(
                    entity_key=entity.entity_key,
                    entity_type=entity.entity_type,
                    canonical_name=entity.canonical_name,
                    matched_text=alias.alias,
                    match_method="alias",
                    matched_token=token,
                )
            )

    # Exact whole-query matches precede contained mentions. Longer mentions
    # precede shorter nested names. Reviewed aliases win ties over canonical
    # collisions, but distinct entity keys remain visible in shadow metrics.
    candidates.sort(
        key=lambda item: (
            0 if item.matched_token == query_token else 1,
            0 if item.course_code_match else 1,
            -len(item.matched_token),
            0 if item.match_method == "alias" else 1,
            item.entity_type,
            item.entity_key,
        )
    )

    linked: list[LinkedEntity] = []
    linked_tokens: list[tuple[str, str]] = []
    seen_entities: set[str] = set()
    seen_identities: set[tuple[str, str]] = set()
    seen_organization_names: set[str] = set()
    organization_types = {
        EntityType.COLLEGE.value,
        EntityType.DEPARTMENT.value,
        EntityType.ORGANIZATION.value,
    }
    for candidate in candidates:
        if candidate.entity_key in seen_entities:
            continue
        canonical_identity = (
            candidate.entity_type,
            identity_token(candidate.canonical_name),
        )
        if (
            candidate.entity_type
            not in {EntityType.COURSE.value, EntityType.ACADEMIC_EVENT.value}
            and canonical_identity in seen_identities
        ):
            # Multiple source projections can retain separate entity keys for
            # the same display identity (for example two OrganizationUnit rows
            # named "정각원"). Query linking should seed the semantic identity
            # once; provenance remains attached to all approved relations.
            continue
        organization_name = identity_token(candidate.canonical_name)
        if (
            candidate.entity_type in organization_types
            and organization_name in seen_organization_names
        ):
            # One display mention is one organization seed even when source
            # rows retained both a heading-shaped Department and a College.
            # Candidate sorting gives College, Department, OrganizationUnit
            # priority in that order.
            continue
        if candidate.entity_type in {
            EntityType.COURSE.value,
            EntityType.ACADEMIC_EVENT.value,
        } and any(
            entity_type == candidate.entity_type
            and candidate.matched_token != linked_token
            and candidate.matched_token in linked_token
            for entity_type, linked_token in linked_tokens
        ):
            # Prefer a longer explicit course/event mention over a nested
            # generic alias such as "수강신청" inside "2학기 수강신청".
            continue
        seen_entities.add(candidate.entity_key)
        seen_identities.add(canonical_identity)
        if candidate.entity_type in organization_types:
            seen_organization_names.add(organization_name)
        linked.append(
            LinkedEntity(
                entity_key=candidate.entity_key,
                entity_type=candidate.entity_type,
                canonical_name=candidate.canonical_name,
                matched_text=candidate.matched_text,
                match_method=candidate.match_method,
            )
        )
        linked_tokens.append((candidate.entity_type, candidate.matched_token))
        if len(linked) >= max_entities:
            break
    return tuple(linked)


def traverse_relations(
    session: Session,
    linked_entities: Iterable[LinkedEntity],
    route: Sequence[str],
    *,
    max_hops: int = 2,
    max_relations: int = 100,
) -> tuple[tuple[TraversedRelation, ...], tuple[int, ...]]:
    """Breadth-first traversal over approved active relations in both directions."""

    max_hops = min(2, max(1, max_hops))
    max_relations = max(1, max_relations)
    linked_sequence = tuple(linked_entities)
    seed_keys = {item.entity_key for item in linked_sequence}
    seed_rank = {
        item.entity_key: index for index, item in enumerate(linked_sequence)
    }
    if not seed_keys:
        return (), ()

    predicates = _allowed_predicates(route)
    frontier = set(seed_keys)
    visited_entities = set(seed_keys)
    seen_relations: set[str] = set()
    traversed: list[TraversedRelation] = []
    relation_ids: list[int] = []

    for depth in range(1, max_hops + 1):
        if not frontier or len(traversed) >= max_relations:
            break
        remaining = max_relations - len(traversed)
        rows = (
            session.query(OntologyRelation)
            .filter(
                OntologyRelation.status == "active",
                OntologyRelation.review_status == "approved",
                OntologyRelation.predicate.in_(predicates),
                or_(
                    OntologyRelation.subject_key.in_(tuple(frontier)),
                    OntologyRelation.object_key.in_(tuple(frontier)),
                ),
            )
            .all()
        )
        predicate_priority = {
            Predicate.OFFERED_BY.value: 0 if "courses" in route else 1,
            Predicate.WORKS_AT.value: 0 if "staff" in route else 1,
            Predicate.GOVERNS.value: 0 if "rules" in route else 1,
            Predicate.VALID_FOR_ENTRY.value: 0 if "rules" in route else 1,
            Predicate.OCCURS_DURING.value: 0 if "schedule" in route else 1,
            Predicate.MANAGED_BY.value: 0 if "schedule" in route else 1,
            Predicate.MENTIONS_ORGANIZATION.value: 0 if "notices" in route else 1,
            Predicate.PART_OF.value: 2,
        }
        rows.sort(
            key=lambda row: (
                -sum(
                    entity_key in seed_keys
                    for entity_key in (row.subject_key, row.object_key)
                ),
                min(
                    (
                        seed_rank[entity_key]
                        for entity_key in (row.subject_key, row.object_key)
                        if entity_key in seed_rank
                    ),
                    default=len(seed_rank),
                ),
                predicate_priority.get(row.predicate, 9),
                row.relation_key,
            )
        )
        if "notices" in route and rows:
            notice_dates = dict(
                session.query(
                    OntologyEvidence.relation_id,
                    func.max(OntologyEvidence.published_at),
                )
                .filter(
                    OntologyEvidence.relation_id.in_(tuple(row.id for row in rows)),
                    OntologyEvidence.source_dataset == "notices",
                )
                .group_by(OntologyEvidence.relation_id)
                .all()
            )
            # Python's sort is stable: publication date becomes the primary
            # key while the structural relevance order above breaks ties.
            rows.sort(
                key=lambda row: notice_dates.get(row.id) or "",
                reverse=True,
            )
        rows = rows[:remaining]

        next_frontier: set[str] = set()
        for row in rows:
            if row.relation_key in seen_relations:
                continue
            seen_relations.add(row.relation_key)
            relation_ids.append(row.id)
            traversed.append(
                TraversedRelation(
                    relation_key=row.relation_key,
                    subject_key=row.subject_key,
                    predicate=row.predicate,
                    object_key=row.object_key,
                    depth=depth,
                )
            )
            for entity_key in (row.subject_key, row.object_key):
                if entity_key not in visited_entities:
                    visited_entities.add(entity_key)

            # PART_OF is directional: child(subject) -> parent(object). Only a
            # query that starts at a parent may descend to its children for a
            # second hop. Moving upward from a department/leaf and then
            # expanding again would retrieve sibling departments and unrelated
            # staff/courses. OFFERED_BY and WORKS_AT are terminal evidence
            # edges and never need another expansion hop.
            if (
                row.predicate == Predicate.PART_OF.value
                and row.object_key in frontier
                and row.subject_key not in frontier
            ):
                next_frontier.add(row.subject_key)
        frontier = next_frontier

    return tuple(traversed), tuple(relation_ids)


def evidence_document_keys(
    session: Session,
    relation_ids: Sequence[int],
    route: Sequence[str],
    *,
    max_documents: int = 50,
    min_relation_support: int = 1,
) -> tuple[str, ...]:
    """Return only published canonical documents supporting traversed edges."""

    datasets = _allowed_evidence_datasets(route)
    if not relation_ids or not datasets or max_documents <= 0:
        return ()

    rows = (
        session.query(OntologyEvidence.relation_id, OntologyEvidence.document_key)
        .join(
            SourceDocument,
            (SourceDocument.document_key == OntologyEvidence.document_key)
            & (SourceDocument.dataset == OntologyEvidence.source_dataset),
        )
        .filter(
            OntologyEvidence.relation_id.in_(tuple(relation_ids)),
            OntologyEvidence.source_dataset.in_(datasets),
            SourceDocument.status.in_(PUBLISHED_SOURCE_STATUSES),
        )
        .all()
    )
    relation_rank = {
        relation_id: index for index, relation_id in enumerate(relation_ids)
    }
    relations_by_document: dict[str, set[int]] = {}
    for relation_id, document_key in rows:
        relations_by_document.setdefault(document_key, set()).add(relation_id)
    eligible_documents = {
        document_key
        for document_key, supporting_relations in relations_by_document.items()
        if len(supporting_relations) >= max(1, min_relation_support)
    }
    ranked_documents = sorted(
        eligible_documents,
        key=lambda document_key: (
            -len(relations_by_document[document_key]),
            min(
                relation_rank.get(relation_id, len(relation_rank))
                for relation_id in relations_by_document[document_key]
            ),
            document_key,
        ),
    )
    return tuple(ranked_documents[:max_documents])


def _filter_rule_relations_by_query(
    session: Session,
    query: str,
    traversed: Sequence[TraversedRelation],
    relation_ids: Sequence[int],
) -> tuple[tuple[TraversedRelation, ...], tuple[int, ...]]:
    """Keep only the explicit academic-requirement kind requested by the user."""

    query_token = identity_token(query)
    if "교양" in query_token:
        expected_kind = "general_education"
    elif "졸업" in query_token:
        expected_kind = "graduation"
    else:
        return tuple(traversed), tuple(relation_ids)

    requirement_keys = {
        item.subject_key
        for item in traversed
        if item.predicate
        in {Predicate.GOVERNS.value, Predicate.VALID_FOR_ENTRY.value}
    }
    if not requirement_keys:
        return tuple(traversed), tuple(relation_ids)
    entities = (
        session.query(OntologyEntity)
        .filter(OntologyEntity.entity_key.in_(tuple(requirement_keys)))
        .all()
    )
    kinds_by_key: dict[str, str] = {}
    for entity in entities:
        try:
            properties = json.loads(entity.properties_json or "{}")
        except (TypeError, ValueError):
            continue
        kind = str(properties.get("requirement_kind") or "").strip()
        if kind:
            kinds_by_key[entity.entity_key] = kind

    kept_relations: list[TraversedRelation] = []
    kept_ids: list[int] = []
    for item, relation_id in zip(traversed, relation_ids):
        relation_kind = kinds_by_key.get(item.subject_key)
        if relation_kind and relation_kind != expected_kind:
            continue
        kept_relations.append(item)
        kept_ids.append(relation_id)
    return tuple(kept_relations), tuple(kept_ids)


def _academic_requirement_query_kind(query: str) -> str:
    query_token = identity_token(query)
    if "교양" in query_token:
        return "general_education"
    if "조기졸업" in query_token:
        # Early-graduation eligibility is governed by academic regulations,
        # not the college credit-table slices currently projected here.
        return ""
    if "졸업" in query_token:
        return "graduation"
    if "학업이수가이드" in query_token or "학업이수안내" in query_token:
        return "guide"
    return ""


def _generic_schedule_query_is_scoped(
    query: str,
    linked: Sequence[LinkedEntity],
) -> bool:
    query_token = identity_token(query)
    if "종강총회" in query_token:
        return False
    generic_registration_links = [
        item
        for item in linked
        if item.entity_type == EntityType.ACADEMIC_EVENT.value
        and identity_token(item.matched_text) == "수강신청"
    ]
    if not generic_registration_links:
        return True
    schedule_intent_tokens = (
        "언제",
        "기간",
        "일정",
        "날짜",
        "시작",
        "종료",
        "마감",
        "며칠",
        "몇시",
        "부서",
        "담당",
        "문의",
        "연락처",
        "전화",
    )
    return any(token in query_token for token in schedule_intent_tokens)


def _notice_query_is_scoped(query: str) -> bool:
    """Use organization-to-notice edges only for an explicit notice request.

    Organization names also occur in graduation, staff-contact, and private
    data questions. The current graph knows that a notice bears an explicit
    organization label, but not that every question mentioning that
    organization is asking for its notices.
    """

    query_token = identity_token(query)
    if not any(token in query_token for token in ("공지", "공고", "게시글")):
        return False
    blocked_intents = (
        "연락처",
        "전화번호",
        "이메일",
        "메일주소",
        "담당자",
        "상담",
        "졸업요건",
        "졸업학점",
        "전공학점",
        "전체학점",
        "수강생",
    )
    if any(token in query_token for token in blocked_intents):
        return False
    retrieval_intents = (
        "최근",
        "최신",
        "보여",
        "찾아",
        "알려",
        "목록",
        "뭐",
        "어떤",
        "있어",
        "올라온",
    )
    return "공지사항" in query_token or any(
        token in query_token for token in retrieval_intents
    )


def run_ontology_shadow(
    session: Session,
    query: str,
    route: Sequence[str],
    *,
    max_hops: int = 2,
    max_entities: int = 8,
    max_relations: int = 100,
    max_documents: int = 50,
) -> OntologyShadowResult:
    """Evaluate the ontology path without changing the production retrieval path."""

    bounded_hops = min(2, max(1, max_hops))
    linked = link_entities(
        session,
        query,
        route,
        max_entities=max_entities,
    )
    traversal_route = list(route)
    if "rules" in traversal_route:
        requirement_kind = _academic_requirement_query_kind(query)
        has_cohort = any(
            item.entity_type == EntityType.ENTRY_COHORT.value for item in linked
        )
        has_organization = any(
            item.entity_type
            in {EntityType.DEPARTMENT.value, EntityType.COLLEGE.value}
            for item in linked
        )
        rules_are_scoped = (
            has_cohort
            and bool(requirement_kind)
            and (
                requirement_kind == "general_education"
                or has_organization
            )
        )
        if not rules_are_scoped:
            # Academic guides are cohort-specific, and college graduation
            # tables are organization-specific. Without both dimensions the
            # graph must not guess an edition or a target college.
            traversal_route = [item for item in traversal_route if item != "rules"]
    if "schedule" in traversal_route and not _generic_schedule_query_is_scoped(
        query,
        linked,
    ):
        traversal_route = [item for item in traversal_route if item != "schedule"]
    if "notices" in traversal_route and not _notice_query_is_scoped(query):
        traversal_route = [item for item in traversal_route if item != "notices"]
    relations, relation_ids = traverse_relations(
        session,
        linked,
        traversal_route,
        max_hops=bounded_hops,
        max_relations=max_relations,
    )
    if "rules" in traversal_route:
        relations, relation_ids = _filter_rule_relations_by_query(
            session,
            query,
            relations,
            relation_ids,
        )
    rules_only = set(traversal_route) == {"rules"}
    if rules_only:
        filtered_pairs = [
            (item, relation_id)
            for item, relation_id in zip(relations, relation_ids)
            if item.predicate
            in {Predicate.GOVERNS.value, Predicate.VALID_FOR_ENTRY.value}
        ]
        relations = tuple(item for item, _ in filtered_pairs)
        relation_ids = tuple(relation_id for _, relation_id in filtered_pairs)
    min_relation_support = 1
    if rules_only and _academic_requirement_query_kind(query) in {
        "graduation",
        "guide",
    }:
        # A college graduation document must match both the explicit cohort
        # and the explicit college/department. One-edge matches are either the
        # wrong year or a different target organization.
        min_relation_support = 2
    documents = evidence_document_keys(
        session,
        relation_ids,
        traversal_route,
        max_documents=max_documents,
        min_relation_support=min_relation_support,
    )
    return OntologyShadowResult(
        linked_entities=linked,
        traversed_relations=relations,
        document_keys=documents,
        max_hops=bounded_hops,
    )
