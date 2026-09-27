"""Deterministic ontology projection built from canonical ``SourceDocument`` rows.

The graph is a derived retrieval aid, never a second source of truth.  Every
published relation must point back to at least one canonical document field.
The deterministic scope includes structured course/staff/schedule fields, the
explicit year/college/department metadata of entry-year academic guides, and
notice title/department labels that resolve to exactly one existing
organization. Free-text notice targeting and OCR-table credit interpretation
remain outside this projection until separately reviewed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
import csv
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy.orm import Session

from src.database import (
    OntologyAlias,
    OntologyEntity,
    OntologyEvidence,
    OntologyRelation,
    SourceDocument,
)
from src.pipelines.canonical import canonical_json


ONTOLOGY_SCHEMA_VERSION = 4
PUBLISHED_SOURCE_STATUSES = frozenset({"active", "updated"})
DETERMINISTIC_DATASETS = frozenset(
    {"courses", "notices", "rules", "schedule", "staff"}
)


class EntityType(StrEnum):
    COLLEGE = "College"
    DEPARTMENT = "Department"
    ORGANIZATION = "OrganizationUnit"
    COURSE = "Course"
    PERSON = "Person"
    ACADEMIC_REQUIREMENT = "AcademicRequirement"
    ENTRY_COHORT = "EntryCohort"
    ACADEMIC_EVENT = "AcademicEvent"
    DATE_RANGE = "DateRange"
    NOTICE = "Notice"


class Predicate(StrEnum):
    PART_OF = "PART_OF"
    OFFERED_BY = "OFFERED_BY"
    WORKS_AT = "WORKS_AT"
    GOVERNS = "GOVERNS"
    VALID_FOR_ENTRY = "VALID_FOR_ENTRY"
    OCCURS_DURING = "OCCURS_DURING"
    MANAGED_BY = "MANAGED_BY"
    MENTIONS_ORGANIZATION = "MENTIONS_ORGANIZATION"


@dataclass(frozen=True)
class ProjectedEntity:
    entity_key: str
    entity_type: EntityType
    canonical_name: str
    properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectedAlias:
    alias_key: str
    alias: str
    entity_key: str
    source_dataset: str
    source_document_key: str | None = None


@dataclass(frozen=True)
class ProjectedRelation:
    relation_key: str
    subject_key: str
    predicate: Predicate
    object_key: str
    qualifiers: Mapping[str, Any] = field(default_factory=dict)
    confidence: float = 1.0


@dataclass(frozen=True)
class ProjectedEvidence:
    relation_key: str
    source_dataset: str
    document_key: str
    evidence_locator: str
    evidence_text: str
    source_url: str = ""
    published_at: str = ""


@dataclass
class OntologyProjection:
    entities: dict[str, ProjectedEntity] = field(default_factory=dict)
    aliases: dict[tuple[str, str, str], ProjectedAlias] = field(default_factory=dict)
    relations: dict[str, ProjectedRelation] = field(default_factory=dict)
    evidence: dict[tuple[str, str, str], ProjectedEvidence] = field(default_factory=dict)
    source_datasets: set[str] = field(default_factory=set)
    documents_seen: int = 0
    collisions: list[str] = field(default_factory=list)

    def add_entity(self, entity: ProjectedEntity) -> None:
        existing = self.entities.get(entity.entity_key)
        if existing is None:
            self.entities[entity.entity_key] = entity
            return
        if (
            existing.entity_type != entity.entity_type
            or existing.canonical_name != entity.canonical_name
        ):
            self.collisions.append(
                f"entity_key collision: {entity.entity_key} "
                f"({existing.canonical_name!r} != {entity.canonical_name!r})"
            )

    def add_alias(self, alias: ProjectedAlias) -> None:
        key = (alias.alias_key, alias.entity_key, alias.source_dataset)
        self.aliases.setdefault(key, alias)
        self.source_datasets.add(alias.source_dataset)

    def add_relation(
        self,
        relation: ProjectedRelation,
        evidence: ProjectedEvidence,
    ) -> None:
        existing = self.relations.get(relation.relation_key)
        if existing is not None and existing != relation:
            self.collisions.append(f"relation_key collision: {relation.relation_key}")
            return
        self.relations.setdefault(relation.relation_key, relation)
        evidence_key = (
            evidence.relation_key,
            evidence.document_key,
            evidence.evidence_locator,
        )
        self.evidence.setdefault(evidence_key, evidence)
        self.source_datasets.add(evidence.source_dataset)


@dataclass(frozen=True)
class PersistedProjectionCounts:
    entities: int
    aliases: int
    relations: int
    evidence: int
    stale_relations_deleted: int
    stale_entities_deleted: int


class OntologyProjectionError(ValueError):
    pass


def clean_text(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text))


def identity_token(value: object) -> str:
    """Return the stable lookup identity used by entities and aliases."""
    normalized = clean_text(value).lower()
    return re.sub(r"[^0-9a-z가-힣]+", "", normalized)


def named_entity_key(entity_type: EntityType, canonical_name: str) -> str:
    token = identity_token(canonical_name)
    if not token:
        raise OntologyProjectionError(f"empty {entity_type.value} identity")
    return f"{entity_type.value.lower()}:{token}"


def _hashed_entity_key(entity_type: EntityType, label: str, identity: str) -> str:
    label_token = identity_token(label) or entity_type.value.lower()
    digest = hashlib.sha256(clean_text(identity).encode("utf-8")).hexdigest()[:16]
    return f"{entity_type.value.lower()}:{label_token}:{digest}"


def _source_bound_entity_key(
    entity_type: EntityType,
    label: str,
    document_key: object,
) -> str:
    """Hash a canonical source identity without Unicode normalization."""
    label_token = identity_token(label) or entity_type.value.lower()
    exact_document_key = "" if document_key is None else str(document_key)
    digest = hashlib.sha256(exact_document_key.encode("utf-8")).hexdigest()[:16]
    return f"{entity_type.value.lower()}:{label_token}:{digest}"


def _relation_key(
    subject_key: str,
    predicate: Predicate,
    object_key: str,
    qualifiers: Mapping[str, Any] | None = None,
) -> str:
    payload = [
        subject_key,
        predicate.value,
        object_key,
        dict(qualifiers or {}),
    ]
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:24]
    return f"relation:{digest}"


def _payload(document: SourceDocument) -> dict[str, Any]:
    try:
        payload = json.loads(document.normalized_payload_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _first(payload: Mapping[str, Any], names: Sequence[str]) -> str:
    for name in names:
        value = clean_text(payload.get(name))
        if value:
            return value
    return ""


def load_department_aliases(path: Path) -> dict[str, str]:
    """Load reviewed department aliases as ``raw alias -> canonical name``."""
    if not path.exists():
        return {}
    aliases: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            alias = clean_text(row.get("alias"))
            canonical = clean_text(row.get("canonical_department_name"))
            if alias and canonical:
                aliases[alias] = canonical
    return aliases


def _normalized_alias_map(aliases: Mapping[str, str]) -> dict[str, str]:
    return {
        identity_token(alias): clean_text(canonical)
        for alias, canonical in aliases.items()
        if identity_token(alias) and clean_text(canonical)
    }


def _canonical_department(value: str, aliases: Mapping[str, str]) -> str:
    cleaned = clean_text(value)
    return aliases.get(identity_token(cleaned), cleaned)


def _document_evidence(
    document: SourceDocument,
    *,
    relation_key: str,
    locator: str,
    text: str,
) -> ProjectedEvidence:
    return ProjectedEvidence(
        relation_key=relation_key,
        source_dataset=clean_text(document.dataset),
        # ``document_key`` is a canonical identity, not display text.  NFKC
        # would change compatibility characters (e.g. ＊ -> *) and Hangul
        # jamo, breaking the exact SourceDocument lineage join.
        document_key="" if document.document_key is None else str(document.document_key),
        evidence_locator=locator,
        evidence_text=clean_text(text),
        source_url=clean_text(document.source_url),
        published_at=clean_text(document.published_at),
    )


def _add_course_projection(
    projection: OntologyProjection,
    document: SourceDocument,
    payload: Mapping[str, Any],
    aliases: Mapping[str, str],
    department_keys: dict[str, str],
    college_keys: dict[str, str],
) -> None:
    raw_department = _first(
        payload,
        ("department_name", "major", "department", "학과", "학과명"),
    )
    department = _canonical_department(raw_department, aliases)
    college = _first(payload, ("college_name", "college", "단과대학", "대학"))
    course_code = _first(payload, ("course_code", "학수번호", "과목코드"))
    course_title = _first(
        payload,
        ("course_name", "title", "교과목명", "과목명", "국문교과목명"),
    )
    if not department:
        return

    department_key = named_entity_key(EntityType.DEPARTMENT, department)
    department_keys[identity_token(department)] = department_key
    projection.add_entity(
        ProjectedEntity(
            entity_key=department_key,
            entity_type=EntityType.DEPARTMENT,
            canonical_name=department,
        )
    )
    if raw_department and identity_token(raw_department) != identity_token(department):
        projection.add_alias(
            ProjectedAlias(
                alias_key=identity_token(raw_department),
                alias=raw_department,
                entity_key=department_key,
                source_dataset="courses",
                source_document_key=document.document_key,
            )
        )

    if college:
        college_key = named_entity_key(EntityType.COLLEGE, college)
        projection.add_entity(
            ProjectedEntity(
                entity_key=college_key,
                entity_type=EntityType.COLLEGE,
                canonical_name=college,
            )
        )
        college_keys[identity_token(college)] = college_key
        relation = ProjectedRelation(
            relation_key=_relation_key(
                department_key,
                Predicate.PART_OF,
                college_key,
            ),
            subject_key=department_key,
            predicate=Predicate.PART_OF,
            object_key=college_key,
        )
        projection.add_relation(
            relation,
            _document_evidence(
                document,
                relation_key=relation.relation_key,
                locator="$.department_name|$.college_name",
                text=f"학과: {department}; 단과대학: {college}",
            ),
        )

    if not (course_code or course_title):
        return
    course_identity = f"{department}|{course_code or course_title}|{course_title}"
    course_key = _hashed_entity_key(
        EntityType.COURSE,
        course_code or course_title,
        course_identity,
    )
    projection.add_entity(
        ProjectedEntity(
            entity_key=course_key,
            entity_type=EntityType.COURSE,
            canonical_name=course_title or course_code,
            properties={
                "course_code": course_code,
                "department": department,
                "record_type": _first(payload, ("record_type",)),
                "availability_status": _first(payload, ("availability_status",)),
            },
        )
    )
    relation = ProjectedRelation(
        relation_key=_relation_key(
            course_key,
            Predicate.OFFERED_BY,
            department_key,
        ),
        subject_key=course_key,
        predicate=Predicate.OFFERED_BY,
        object_key=department_key,
    )
    projection.add_relation(
        relation,
        _document_evidence(
            document,
            relation_key=relation.relation_key,
            locator="$.course_code|$.course_name|$.department_name",
            text=(
                f"교과목: {course_title or course_code}; "
                f"학수번호: {course_code or '확인 필요'}; 학과: {department}"
            ),
        ),
    )


def _staff_path(payload: Mapping[str, Any]) -> list[str]:
    raw_path = _first(
        payload,
        ("부서경로", "department_path", "organization_path"),
    )
    if raw_path:
        return [clean_text(part) for part in raw_path.split(">") if clean_text(part)]
    fallback = _first(payload, ("조직(트리)", "department", "소속"))
    return [fallback] if fallback else []


def _add_staff_projection(
    projection: OntologyProjection,
    document: SourceDocument,
    payload: Mapping[str, Any],
    aliases: Mapping[str, str],
    department_keys: Mapping[str, str],
    college_keys: Mapping[str, str],
    organization_keys: dict[str, set[str]],
) -> None:
    path = _staff_path(payload)
    if not path:
        return

    resolved_nodes: list[tuple[str, EntityType, str]] = []
    path_prefix: list[str] = []
    for component in path:
        path_prefix.append(component)
        canonical_component = _canonical_department(component, aliases)
        department_key = department_keys.get(identity_token(canonical_component))
        if department_key:
            node_key = department_key
            node_type = EntityType.DEPARTMENT
            node_name = canonical_component
        elif college_key := college_keys.get(identity_token(component)):
            node_key = college_key
            node_type = EntityType.COLLEGE
            node_name = component
        else:
            node_key = _hashed_entity_key(
                EntityType.ORGANIZATION,
                component,
                " > ".join(path_prefix),
            )
            node_type = EntityType.ORGANIZATION
            node_name = component
        projection.add_entity(
            ProjectedEntity(
                entity_key=node_key,
                entity_type=node_type,
                canonical_name=node_name,
                properties={"organization_path": " > ".join(path_prefix)},
            )
        )
        organization_keys.setdefault(identity_token(node_name), set()).add(node_key)
        resolved_nodes.append((node_key, node_type, node_name))

    for parent, child in zip(resolved_nodes, resolved_nodes[1:]):
        parent_key, _, parent_name = parent
        child_key, _, child_name = child
        if parent_key == child_key:
            continue
        relation = ProjectedRelation(
            relation_key=_relation_key(
                child_key,
                Predicate.PART_OF,
                parent_key,
            ),
            subject_key=child_key,
            predicate=Predicate.PART_OF,
            object_key=parent_key,
        )
        projection.add_relation(
            relation,
            _document_evidence(
                document,
                relation_key=relation.relation_key,
                locator="$.부서경로",
                text=f"조직 경로: {parent_name} > {child_name}",
            ),
        )

    name = _first(payload, ("성명", "이름", "name"))
    position = _first(payload, ("직위", "position"))
    role = _first(payload, ("담당업무", "role"))
    phone = _first(payload, ("전화번호", "phone"))
    email = _first(payload, ("이메일", "email"))
    if not name:
        return
    final_key, _, final_name = resolved_nodes[-1]
    person_key = _source_bound_entity_key(
        EntityType.PERSON,
        name,
        document.document_key,
    )
    projection.add_entity(
        ProjectedEntity(
            entity_key=person_key,
            entity_type=EntityType.PERSON,
            canonical_name=name,
            properties={
                "position": position,
                "role": role,
                "phone": phone,
                "email": email,
            },
        )
    )
    relation = ProjectedRelation(
        relation_key=_relation_key(
            person_key,
            Predicate.WORKS_AT,
            final_key,
        ),
        subject_key=person_key,
        predicate=Predicate.WORKS_AT,
        object_key=final_key,
    )
    projection.add_relation(
        relation,
        _document_evidence(
            document,
            relation_key=relation.relation_key,
            locator="$.성명|$.직위|$.담당업무|$.부서경로",
            text=(
                f"교직원: {name}; 소속: {final_name}; "
                f"직위: {position or '확인 필요'}; 담당업무: {role or '확인 필요'}"
            ),
        ),
    )


_GENERIC_SCHEDULE_MANAGERS = frozenset(
    {"각 학과별", "각 단과대학", "담당교원"}
)


_NOTICE_TITLE_PREFIX_RE = re.compile(r"^\[([^\[\]]{2,40})\]")


def _organization_candidates(
    projection: OntologyProjection,
) -> dict[str, set[str]]:
    organization_types = {
        EntityType.COLLEGE,
        EntityType.DEPARTMENT,
        EntityType.ORGANIZATION,
    }
    candidates: dict[str, set[str]] = {}
    for entity in projection.entities.values():
        if entity.entity_type not in organization_types:
            continue
        candidates.setdefault(identity_token(entity.canonical_name), set()).add(
            entity.entity_key
        )
    for alias in projection.aliases.values():
        entity = projection.entities.get(alias.entity_key)
        if entity is None or entity.entity_type not in organization_types:
            continue
        candidates.setdefault(identity_token(alias.alias), set()).add(
            alias.entity_key
        )
    return candidates


def _notice_organization_labels(
    document: SourceDocument,
    payload: Mapping[str, Any],
) -> list[tuple[str, str]]:
    labels: list[tuple[str, str]] = []
    title = _first(payload, ("title",)) or clean_text(document.title)
    if match := _NOTICE_TITLE_PREFIX_RE.match(title):
        label = clean_text(match.group(1))
        if label:
            labels.append((label, "$.title[leading_bracket]"))
    department = _first(payload, ("department",))
    if department and identity_token(department) not in {"공통", "전체"}:
        labels.append((department, "$.department"))
    return labels


def _add_notice_projection(
    projection: OntologyProjection,
    document: SourceDocument,
    payload: Mapping[str, Any],
    organization_candidates: Mapping[str, set[str]],
) -> None:
    resolved: dict[str, list[tuple[str, str]]] = {}
    for label, locator in _notice_organization_labels(document, payload):
        candidates = organization_candidates.get(identity_token(label), set())
        if len(candidates) != 1:
            # An unmatched or ambiguous label is retained only in the
            # canonical notice. The graph must not guess its organization.
            continue
        target_key = next(iter(candidates))
        resolved.setdefault(target_key, []).append((label, locator))
    if not resolved:
        return

    title = _first(payload, ("title",)) or clean_text(document.title)
    if not title:
        return
    notice_key = _source_bound_entity_key(
        EntityType.NOTICE,
        title,
        document.document_key,
    )
    projection.add_entity(
        ProjectedEntity(
            entity_key=notice_key,
            entity_type=EntityType.NOTICE,
            canonical_name=title,
            properties={
                "board_name": _first(payload, ("board_name",)),
                "category": _first(payload, ("category",)),
                "published_at": clean_text(document.published_at)
                or _first(payload, ("published_at",)),
                "is_pinned": _first(payload, ("is_pinned",)),
            },
        )
    )
    for target_key, label_locators in sorted(resolved.items()):
        relation = ProjectedRelation(
            relation_key=_relation_key(
                notice_key,
                Predicate.MENTIONS_ORGANIZATION,
                target_key,
            ),
            subject_key=notice_key,
            predicate=Predicate.MENTIONS_ORGANIZATION,
            object_key=target_key,
        )
        for label, locator in label_locators:
            projection.add_relation(
                relation,
                _document_evidence(
                    document,
                    relation_key=relation.relation_key,
                    locator=locator,
                    text=f"공지: {title}; 명시 조직 표기: {label}",
                ),
            )


def _schedule_aliases(title: str, start_date: str) -> list[str]:
    cleaned = clean_text(title)
    candidates: set[str] = set()
    without_year = clean_text(
        re.sub(r"^20\d{2}(?:학년도|년)\s*", "", cleaned)
    )
    if without_year and identity_token(without_year) != identity_token(cleaned):
        candidates.add(without_year)

    without_round = clean_text(
        re.sub(r"\s*\(\d+차\)\s*$", "", without_year or cleaned)
    )
    if without_round:
        candidates.add(without_round)

    if "학부 수강 신청" in cleaned or "학부 수강신청" in cleaned:
        undergraduate = re.sub(r"학부\s*수강\s*신청", "수강신청", without_year)
        candidates.update({clean_text(undergraduate), "수강신청"})
        year_semester = re.search(r"(20\d{2})학년도\s*([12])학기", cleaned)
        if year_semester:
            year, semester_number = year_semester.groups()
            candidates.update(
                {
                    f"{year}-{semester_number} 수강신청",
                    f"{year[-2:]}-{semester_number} 수강신청",
                }
            )
    if re.search(r"(?:여름|겨울)\s*계절학기\s*수강신청", cleaned):
        candidates.add("계절학기 수강신청")
    if "수강신청 확인 및 정정" in cleaned:
        candidates.update({"수강정정", "수강신청 정정"})
    if "성적처리(공시,정정)" in cleaned:
        semester_match = re.search(r"([12])학기", cleaned)
        if semester_match:
            candidates.add(f"{semester_match.group(1)}학기 성적공시")
            candidates.add(f"{semester_match.group(1)}학기 성적정정")
        candidates.update({"성적공시", "성적정정"})
    if "중간시험" in cleaned:
        candidates.update(
            {clean_text(cleaned.replace("중간시험", "중간고사")), "중간고사"}
        )
    if "기말시험" in cleaned:
        candidates.update(
            {clean_text(cleaned.replace("기말시험", "기말고사")), "기말고사"}
        )
    if "학위수여식" in cleaned:
        candidates.add("졸업식")
        ceremony_match = re.match(
            r"^(20\d{2}년\s+(?:봄|가을))\s+학위수여식\(([^)]+)\)$",
            cleaned,
        )
        if ceremony_match:
            candidates.add(
                f"{ceremony_match.group(1)} {ceremony_match.group(2)} 학위수여식"
            )

    try:
        month = date.fromisoformat(start_date).month
    except ValueError:
        month = 0
    semester = "1" if 3 <= month <= 8 else "2" if month >= 9 else ""
    if semester and cleaned in {"개강", "개강/학기개시일"}:
        candidates.update({f"{semester}학기 개강", "개강"})
    if semester and cleaned == "종강":
        candidates.update({f"{semester}학기 종강", "종강"})
    if semester and cleaned == "수강신청 확인 및 정정":
        candidates.update(
            {
                f"{semester}학기 수강정정",
                f"{semester}학기 수강신청 정정",
            }
        )

    return sorted(
        {
            alias
            for alias in candidates
            if identity_token(alias)
            and (
                identity_token(alias) != identity_token(cleaned)
                or len(identity_token(alias)) == 2
            )
        },
        key=lambda alias: (identity_token(alias), alias),
    )


def _add_schedule_projection(
    projection: OntologyProjection,
    document: SourceDocument,
    payload: Mapping[str, Any],
    organization_keys: Mapping[str, set[str]],
) -> None:
    if clean_text(document.source_type) != "academic_schedule":
        return
    academic_year = _first(payload, ("academic_year",))
    title = _first(payload, ("title", "content")) or clean_text(document.title)
    start_date = _first(payload, ("start_date",))
    end_date = _first(payload, ("end_date",))
    if not re.fullmatch(r"20\d{2}", academic_year) or not title:
        return
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return
    if end < start:
        return

    event_key = _source_bound_entity_key(
        EntityType.ACADEMIC_EVENT,
        title,
        document.document_key,
    )
    department = _first(payload, ("department",))
    projection.add_entity(
        ProjectedEntity(
            entity_key=event_key,
            entity_type=EntityType.ACADEMIC_EVENT,
            canonical_name=title,
            properties={
                "academic_year": academic_year,
                "start_date": start_date,
                "end_date": end_date,
                "category": _first(payload, ("category",)),
                "department": department,
            },
        )
    )
    for alias in _schedule_aliases(title, start_date):
        projection.add_alias(
            ProjectedAlias(
                alias_key=identity_token(alias),
                alias=alias,
                entity_key=event_key,
                source_dataset="schedule",
                source_document_key=document.document_key,
            )
        )

    range_name = start_date if start_date == end_date else f"{start_date}~{end_date}"
    range_key = _hashed_entity_key(
        EntityType.DATE_RANGE,
        range_name,
        f"{start_date}|{end_date}",
    )
    projection.add_entity(
        ProjectedEntity(
            entity_key=range_key,
            entity_type=EntityType.DATE_RANGE,
            canonical_name=range_name,
            properties={"start_date": start_date, "end_date": end_date},
        )
    )
    occurs_relation = ProjectedRelation(
        relation_key=_relation_key(
            event_key,
            Predicate.OCCURS_DURING,
            range_key,
        ),
        subject_key=event_key,
        predicate=Predicate.OCCURS_DURING,
        object_key=range_key,
    )
    projection.add_relation(
        occurs_relation,
        _document_evidence(
            document,
            relation_key=occurs_relation.relation_key,
            locator="$.title|$.start_date|$.end_date",
            text=f"학사일정: {title}; 기간: {range_name}",
        ),
    )

    manager_names = {
        clean_text(part)
        for part in department.split("/")
        if clean_text(part)
        and clean_text(part) not in _GENERIC_SCHEDULE_MANAGERS
    }
    for manager_name in sorted(manager_names, key=identity_token):
        candidates = organization_keys.get(identity_token(manager_name), set())
        if len(candidates) == 1:
            manager_key = next(iter(candidates))
        else:
            manager_key = _hashed_entity_key(
                EntityType.ORGANIZATION,
                manager_name,
                f"schedule-manager|{manager_name}",
            )
            projection.add_entity(
                ProjectedEntity(
                    entity_key=manager_key,
                    entity_type=EntityType.ORGANIZATION,
                    canonical_name=manager_name,
                    properties={"schedule_manager_only": True},
                )
            )
        managed_relation = ProjectedRelation(
            relation_key=_relation_key(
                event_key,
                Predicate.MANAGED_BY,
                manager_key,
            ),
            subject_key=event_key,
            predicate=Predicate.MANAGED_BY,
            object_key=manager_key,
        )
        projection.add_relation(
            managed_relation,
            _document_evidence(
                document,
                relation_key=managed_relation.relation_key,
                locator="$.title|$.department",
                text=f"학사일정: {title}; 담당: {manager_name}",
            ),
        )


_ACADEMIC_GUIDE_SECTIONS = frozenset(
    {"단과대학별 졸업기준", "교양교육과정 이수 기준"}
)
_GUIDE_DEPARTMENTS_RE = re.compile(r"소속\s*학과\s*:\s*([^)]+)\)")


def _guide_departments(title: str, aliases: Mapping[str, str]) -> list[str]:
    match = _GUIDE_DEPARTMENTS_RE.search(clean_text(title))
    if match is None:
        return []
    departments: list[str] = []
    seen: set[str] = set()
    for raw_name in match.group(1).split(","):
        canonical = _canonical_department(clean_text(raw_name), aliases)
        token = identity_token(canonical)
        if not token or token in seen:
            continue
        seen.add(token)
        departments.append(canonical)
    return departments


def _add_academic_guide_projection(
    projection: OntologyProjection,
    document: SourceDocument,
    payload: Mapping[str, Any],
    aliases: Mapping[str, str],
) -> None:
    if clean_text(document.source_type) != "entry_year_guide_pdf":
        return
    entry_year = _first(payload, ("entry_year",))
    section = _first(payload, ("section",))
    if not re.fullmatch(r"20\d{2}", entry_year) or section not in _ACADEMIC_GUIDE_SECTIONS:
        return

    college = _first(payload, ("college_name",))
    if section == "단과대학별 졸업기준" and not college:
        # The section header has no target college. Per-college slices below it
        # carry the explicit target and are the publishable units.
        return

    title = _first(payload, ("title",)) or clean_text(document.title)
    requirement_kind = (
        "graduation" if section == "단과대학별 졸업기준" else "general_education"
    )
    requirement_name = title or f"{entry_year}학번 {section}"
    requirement_key = _source_bound_entity_key(
        EntityType.ACADEMIC_REQUIREMENT,
        requirement_name,
        document.document_key,
    )
    projection.add_entity(
        ProjectedEntity(
            entity_key=requirement_key,
            entity_type=EntityType.ACADEMIC_REQUIREMENT,
            canonical_name=requirement_name,
            properties={
                "entry_year": entry_year,
                "requirement_kind": requirement_kind,
                "section": section,
                "college_name": college,
                "page_start": _first(payload, ("page_start",)),
                "page_end": _first(payload, ("page_end",)),
                # Credit values remain in canonical source text until the OCR
                # table layout is validated; no numeric requirement is guessed.
                "structured_credit_values": False,
            },
        )
    )

    cohort_name = f"{entry_year}학번"
    cohort_key = named_entity_key(EntityType.ENTRY_COHORT, cohort_name)
    projection.add_entity(
        ProjectedEntity(
            entity_key=cohort_key,
            entity_type=EntityType.ENTRY_COHORT,
            canonical_name=cohort_name,
            properties={"entry_year": entry_year},
        )
    )
    projection.add_alias(
        ProjectedAlias(
            alias_key=identity_token(f"{entry_year[-2:]}학번"),
            alias=f"{entry_year[-2:]}학번",
            entity_key=cohort_key,
            source_dataset="rules",
            source_document_key=document.document_key,
        )
    )
    valid_relation = ProjectedRelation(
        relation_key=_relation_key(
            requirement_key,
            Predicate.VALID_FOR_ENTRY,
            cohort_key,
            {"entry_year": entry_year},
        ),
        subject_key=requirement_key,
        predicate=Predicate.VALID_FOR_ENTRY,
        object_key=cohort_key,
        qualifiers={"entry_year": entry_year},
    )
    projection.add_relation(
        valid_relation,
        _document_evidence(
            document,
            relation_key=valid_relation.relation_key,
            locator="$.entry_year",
            text=f"적용 입학년도: {entry_year}학번; 구분: {section}",
        ),
    )

    governed_targets: list[tuple[str, EntityType, str, str]] = []
    college_key = ""
    if college:
        college_key = named_entity_key(EntityType.COLLEGE, college)
        projection.add_entity(
            ProjectedEntity(
                entity_key=college_key,
                entity_type=EntityType.COLLEGE,
                canonical_name=college,
            )
        )
        governed_targets.append(
            (college_key, EntityType.COLLEGE, college, "$.college_name")
        )

    for department in _guide_departments(title, aliases):
        department_key = named_entity_key(EntityType.DEPARTMENT, department)
        projection.add_entity(
            ProjectedEntity(
                entity_key=department_key,
                entity_type=EntityType.DEPARTMENT,
                canonical_name=department,
            )
        )
        governed_targets.append(
            (department_key, EntityType.DEPARTMENT, department, "$.title[소속 학과]")
        )
        if college_key:
            hierarchy_relation = ProjectedRelation(
                relation_key=_relation_key(
                    department_key,
                    Predicate.PART_OF,
                    college_key,
                ),
                subject_key=department_key,
                predicate=Predicate.PART_OF,
                object_key=college_key,
            )
            projection.add_relation(
                hierarchy_relation,
                _document_evidence(
                    document,
                    relation_key=hierarchy_relation.relation_key,
                    locator="$.title[소속 학과]|$.college_name",
                    text=f"학과: {department}; 단과대학: {college}",
                ),
            )

    for target_key, _, target_name, locator in governed_targets:
        relation = ProjectedRelation(
            relation_key=_relation_key(
                requirement_key,
                Predicate.GOVERNS,
                target_key,
                {
                    "entry_year": entry_year,
                    "requirement_kind": requirement_kind,
                },
            ),
            subject_key=requirement_key,
            predicate=Predicate.GOVERNS,
            object_key=target_key,
            qualifiers={
                "entry_year": entry_year,
                "requirement_kind": requirement_kind,
            },
        )
        projection.add_relation(
            relation,
            _document_evidence(
                document,
                relation_key=relation.relation_key,
                locator=locator,
                text=(
                    f"학업요건: {requirement_name}; 적용 대상: {target_name}; "
                    f"입학년도: {entry_year}학번"
                ),
            ),
        )


def build_deterministic_projection(
    documents: Iterable[SourceDocument],
    *,
    department_aliases: Mapping[str, str] | None = None,
    source_datasets: Iterable[str] | None = None,
) -> OntologyProjection:
    """Project deterministic structured fields into an evidence-bound graph."""
    projection = OntologyProjection()
    alias_map = _normalized_alias_map(department_aliases or {})
    document_list = list(documents)
    requested_datasets = {
        clean_text(dataset)
        for dataset in (
            source_datasets
            if source_datasets is not None
            else (document.dataset for document in document_list)
        )
        if clean_text(dataset) in DETERMINISTIC_DATASETS
    }
    projection.source_datasets.update(requested_datasets)
    if department_aliases is not None:
        projection.source_datasets.add("department_aliases")
    visible_documents = sorted(
        (
            document
            for document in document_list
            if clean_text(document.status) in PUBLISHED_SOURCE_STATUSES
            and clean_text(document.dataset) in requested_datasets
            and clean_text(document.document_key)
        ),
        key=lambda item: (clean_text(item.dataset), clean_text(item.document_key)),
    )
    projection.documents_seen = len(visible_documents)

    department_keys: dict[str, str] = {}
    college_keys: dict[str, str] = {}
    organization_keys: dict[str, set[str]] = {}
    for document in visible_documents:
        if document.dataset != "courses":
            continue
        _add_course_projection(
            projection,
            document,
            _payload(document),
            alias_map,
            department_keys,
            college_keys,
        )

    for raw_alias, canonical in sorted(
        (department_aliases or {}).items(),
        key=lambda item: identity_token(item[0]),
    ):
        alias = clean_text(raw_alias)
        canonical_name = clean_text(canonical)
        if not alias or not canonical_name:
            continue
        entity_key = named_entity_key(EntityType.DEPARTMENT, canonical_name)
        projection.add_entity(
            ProjectedEntity(
                entity_key=entity_key,
                entity_type=EntityType.DEPARTMENT,
                canonical_name=canonical_name,
            )
        )
        department_keys.setdefault(identity_token(canonical_name), entity_key)
        if identity_token(alias) != identity_token(canonical_name):
            projection.add_alias(
                ProjectedAlias(
                    alias_key=identity_token(alias),
                    alias=alias,
                    entity_key=entity_key,
                    source_dataset="department_aliases",
                )
            )

    for document in visible_documents:
        if document.dataset != "staff":
            continue
        _add_staff_projection(
            projection,
            document,
            _payload(document),
            alias_map,
            department_keys,
            college_keys,
            organization_keys,
        )

    for document in visible_documents:
        if document.dataset != "schedule":
            continue
        _add_schedule_projection(
            projection,
            document,
            _payload(document),
            organization_keys,
        )

    for document in visible_documents:
        if document.dataset != "rules":
            continue
        _add_academic_guide_projection(
            projection,
            document,
            _payload(document),
            alias_map,
        )

    organization_candidates = _organization_candidates(projection)
    for document in visible_documents:
        if document.dataset != "notices":
            continue
        _add_notice_projection(
            projection,
            document,
            _payload(document),
            organization_candidates,
        )

    return projection


_ALLOWED_RELATION_TYPES: dict[
    Predicate,
    tuple[set[EntityType], set[EntityType]],
] = {
    Predicate.PART_OF: (
        {EntityType.COLLEGE, EntityType.DEPARTMENT, EntityType.ORGANIZATION},
        {EntityType.COLLEGE, EntityType.DEPARTMENT, EntityType.ORGANIZATION},
    ),
    Predicate.OFFERED_BY: ({EntityType.COURSE}, {EntityType.DEPARTMENT}),
    Predicate.WORKS_AT: (
        {EntityType.PERSON},
        {EntityType.COLLEGE, EntityType.DEPARTMENT, EntityType.ORGANIZATION},
    ),
    Predicate.GOVERNS: (
        {EntityType.ACADEMIC_REQUIREMENT},
        {EntityType.COLLEGE, EntityType.DEPARTMENT},
    ),
    Predicate.VALID_FOR_ENTRY: (
        {EntityType.ACADEMIC_REQUIREMENT},
        {EntityType.ENTRY_COHORT},
    ),
    Predicate.OCCURS_DURING: (
        {EntityType.ACADEMIC_EVENT},
        {EntityType.DATE_RANGE},
    ),
    Predicate.MANAGED_BY: (
        {EntityType.ACADEMIC_EVENT},
        {EntityType.COLLEGE, EntityType.DEPARTMENT, EntityType.ORGANIZATION},
    ),
    Predicate.MENTIONS_ORGANIZATION: (
        {EntityType.NOTICE},
        {EntityType.COLLEGE, EntityType.DEPARTMENT, EntityType.ORGANIZATION},
    ),
}


def validate_projection(projection: OntologyProjection) -> list[str]:
    errors = list(projection.collisions)
    evidence_by_relation = {
        evidence.relation_key for evidence in projection.evidence.values()
    }
    for alias in projection.aliases.values():
        if alias.entity_key not in projection.entities:
            errors.append(
                f"alias target missing: {alias.alias!r} -> {alias.entity_key}"
            )
    for relation in projection.relations.values():
        subject = projection.entities.get(relation.subject_key)
        object_ = projection.entities.get(relation.object_key)
        if subject is None:
            errors.append(f"relation subject missing: {relation.relation_key}")
            continue
        if object_ is None:
            errors.append(f"relation object missing: {relation.relation_key}")
            continue
        allowed_subjects, allowed_objects = _ALLOWED_RELATION_TYPES[relation.predicate]
        if subject.entity_type not in allowed_subjects:
            errors.append(
                f"invalid subject type for {relation.predicate.value}: "
                f"{subject.entity_type.value}"
            )
        if object_.entity_type not in allowed_objects:
            errors.append(
                f"invalid object type for {relation.predicate.value}: "
                f"{object_.entity_type.value}"
            )
        if relation.relation_key not in evidence_by_relation:
            errors.append(f"relation evidence missing: {relation.relation_key}")
    for evidence in projection.evidence.values():
        if evidence.relation_key not in projection.relations:
            errors.append(f"evidence relation missing: {evidence.relation_key}")
        if not evidence.document_key or not evidence.evidence_locator:
            errors.append(
                f"incomplete evidence lineage: {evidence.relation_key}"
            )
    return sorted(set(errors))


def validate_source_lineage(
    session: Session,
    projection: OntologyProjection,
) -> list[str]:
    """Require every deterministic evidence key to exist in canonical storage."""
    evidence_keys = {
        evidence.document_key for evidence in projection.evidence.values()
    }
    if not evidence_keys:
        return []
    source_keys = {
        key
        for (key,) in session.query(SourceDocument.document_key)
        .filter(
            SourceDocument.dataset.in_(sorted(DETERMINISTIC_DATASETS)),
            SourceDocument.status.in_(sorted(PUBLISHED_SOURCE_STATUSES)),
        )
        .all()
    }
    missing = sorted(evidence_keys - source_keys)
    if not missing:
        return []
    preview = ", ".join(repr(key) for key in missing[:5])
    return [
        f"canonical evidence documents missing: {len(missing)} ({preview})"
    ]


def persist_projection(
    session: Session,
    projection: OntologyProjection,
) -> PersistedProjectionCounts:
    """Replace deterministic evidence in scope without touching reviewed edges.

    The caller owns the transaction.  This lets the CLI record a build run and
    publish all graph changes atomically.
    """
    errors = validate_projection(projection) + validate_source_lineage(
        session,
        projection,
    )
    if errors:
        raise OntologyProjectionError("; ".join(errors[:10]))

    scopes = sorted(projection.source_datasets)
    if scopes:
        session.query(OntologyEvidence).filter(
            OntologyEvidence.source_dataset.in_(scopes),
            OntologyEvidence.extraction_method == "deterministic",
        ).delete(synchronize_session=False)
        session.query(OntologyAlias).filter(
            OntologyAlias.source_dataset.in_(scopes)
        ).delete(synchronize_session=False)
        session.flush()

    for entity in projection.entities.values():
        row = (
            session.query(OntologyEntity)
            .filter(OntologyEntity.entity_key == entity.entity_key)
            .one_or_none()
        )
        properties_json = canonical_json(dict(entity.properties))
        if row is None:
            row = OntologyEntity(entity_key=entity.entity_key)
            session.add(row)
        row.entity_type = entity.entity_type.value
        row.canonical_name = entity.canonical_name
        row.properties_json = properties_json
        row.extraction_method = "deterministic"
        row.status = "active"
        row.schema_version = ONTOLOGY_SCHEMA_VERSION

    for alias in projection.aliases.values():
        session.add(
            OntologyAlias(
                alias_key=alias.alias_key,
                alias=alias.alias,
                entity_key=alias.entity_key,
                source_dataset=alias.source_dataset,
                source_document_key=alias.source_document_key,
                status="active",
            )
        )

    relation_rows: dict[str, OntologyRelation] = {}
    for relation in projection.relations.values():
        row = (
            session.query(OntologyRelation)
            .filter(OntologyRelation.relation_key == relation.relation_key)
            .one_or_none()
        )
        if row is None:
            row = OntologyRelation(relation_key=relation.relation_key)
            session.add(row)
        if row.extraction_method in {None, "", "deterministic"}:
            row.subject_key = relation.subject_key
            row.predicate = relation.predicate.value
            row.object_key = relation.object_key
            row.qualifiers_json = canonical_json(dict(relation.qualifiers))
            row.confidence = relation.confidence
            row.extraction_method = "deterministic"
            row.review_status = "approved"
            row.status = "active"
            row.schema_version = ONTOLOGY_SCHEMA_VERSION
        relation_rows[relation.relation_key] = row
    session.flush()

    for evidence in projection.evidence.values():
        relation_row = relation_rows[evidence.relation_key]
        session.add(
            OntologyEvidence(
                relation_id=relation_row.id,
                source_dataset=evidence.source_dataset,
                document_key=evidence.document_key,
                extraction_method="deterministic",
                evidence_locator=evidence.evidence_locator,
                evidence_text=evidence.evidence_text,
                source_url=evidence.source_url or None,
                published_at=evidence.published_at or None,
            )
        )
    session.flush()

    stale_relations = (
        session.query(OntologyRelation)
        .filter(
            OntologyRelation.extraction_method == "deterministic",
            ~OntologyRelation.evidence.any(),
        )
        .all()
    )
    for relation in stale_relations:
        session.delete(relation)
    session.flush()

    referenced_entity_keys = {
        key
        for subject_key, object_key in session.query(
            OntologyRelation.subject_key,
            OntologyRelation.object_key,
        ).all()
        for key in (subject_key, object_key)
    }
    referenced_entity_keys.update(
        key for (key,) in session.query(OntologyAlias.entity_key).all()
    )
    entity_query = session.query(OntologyEntity).filter(
        OntologyEntity.extraction_method == "deterministic"
    )
    if referenced_entity_keys:
        entity_query = entity_query.filter(
            ~OntologyEntity.entity_key.in_(sorted(referenced_entity_keys))
        )
    stale_entities = entity_query.all()
    for entity in stale_entities:
        session.delete(entity)
    session.flush()

    return PersistedProjectionCounts(
        entities=len(projection.entities),
        aliases=len(projection.aliases),
        relations=len(projection.relations),
        evidence=len(projection.evidence),
        stale_relations_deleted=len(stale_relations),
        stale_entities_deleted=len(stale_entities),
    )


def load_canonical_documents(
    session: Session,
    datasets: Sequence[str] = tuple(sorted(DETERMINISTIC_DATASETS)),
) -> list[SourceDocument]:
    return (
        session.query(SourceDocument)
        .filter(
            SourceDocument.dataset.in_(list(datasets)),
            SourceDocument.status.in_(list(PUBLISHED_SOURCE_STATUSES)),
        )
        .order_by(SourceDocument.dataset.asc(), SourceDocument.document_key.asc())
        .all()
    )


__all__ = [
    "DETERMINISTIC_DATASETS",
    "EntityType",
    "ONTOLOGY_SCHEMA_VERSION",
    "OntologyProjection",
    "OntologyProjectionError",
    "PersistedProjectionCounts",
    "Predicate",
    "build_deterministic_projection",
    "identity_token",
    "load_canonical_documents",
    "load_department_aliases",
    "named_entity_key",
    "persist_projection",
    "validate_source_lineage",
    "validate_projection",
]
