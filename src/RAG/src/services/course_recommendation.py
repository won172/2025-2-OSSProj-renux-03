"""구조화된 교과과정 데이터로 개인화 수강 후보를 계산합니다.

LLM은 추천 결과를 설명하는 데만 사용할 수 있고, 학년/학기 필터와 학점 합계는
이 모듈의 결정적인 규칙으로 계산합니다. 교과과정 페이지는 실제 개설 시간표가
아니므로 결과는 항상 "수강 후보"이며, 최종 개설 여부는 nDRIMS에서 확인해야 합니다.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping, Sequence
import csv
import math
import re

from src.config import DATA_SOURCES


_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_GRADE_RE = re.compile(r"(?<!\d)([1-6])\s*(?:학년|년)")
_GRADE_RANGE_RE = re.compile(r"([1-6])\s*(?:~|-|–|—|∼)\s*([1-6])")
_SEMESTER_RE = re.compile(r"(?<!\d)([12])\s*학기")
_TARGET_CREDIT_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s*학점(?:을|만큼|정도|까지)?\s*"
    r"(?:듣|채우|신청|구성|추천|짜|수강)"
)
_FALLBACK_CREDIT_RE = re.compile(r"(?<!\d)(\d{1,2})\s*학점")
_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9+#]{2,}")

_GENERIC_INTEREST_TOKENS = {
    "과목",
    "수업",
    "강의",
    "추천",
    "추천해줘",
    "추천해주세요",
    "듣고",
    "들어야",
    "싶어",
    "싶어요",
    "관심",
    "관련",
    "학점",
    "학년",
    "학기",
    "전공",
    "교양",
    "이번",
    "나한테",
    "내게",
    "맞는",
    "정도",
    "채워",
    "채우고",
    "수강",
    "신청",
    "시간표",
    "구성",
    "짜줘",
    "해주세요",
    "알려줘",
    "이고",
    "있어",
    "있어요",
}
_RECOMMENDATION_TERMS = (
    "추천",
    "뭘 들어",
    "뭐 들어",
    "어떤 수업",
    "어떤 과목",
    "수강 계획",
    "시간표 짜",
    "과목 골라",
    "강의 골라",
)


def _clean(value: object) -> str:
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in {"nan", "none"} else re.sub(r"\s+", " ", text)


def _first(row: Mapping[str, object], names: Sequence[str]) -> str:
    for name in names:
        value = _clean(row.get(name, ""))
        if value:
            return value
    return ""


def _normalized_identity(value: str) -> str:
    return re.sub(r"[^가-힣a-z0-9]", "", _clean(value).lower())


@lru_cache(maxsize=8)
def _load_department_aliases_cached(path_text: str, modified_ns: int) -> dict[str, str]:
    del modified_ns
    path = Path(path_text)
    aliases: dict[str, str] = {}
    if not path.exists():
        return aliases
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            alias = _normalized_identity(row.get("alias", ""))
            canonical = _normalized_identity(row.get("canonical_department_name", ""))
            if alias and canonical:
                aliases[alias] = canonical
    return aliases


def canonical_department_identity(value: str) -> str:
    identity = _normalized_identity(value)
    alias_path = Path(DATA_SOURCES["courses_all"]).with_name("dongguk_department_aliases.csv")
    modified_ns = alias_path.stat().st_mtime_ns if alias_path.exists() else -1
    aliases = _load_department_aliases_cached(str(alias_path.resolve()), modified_ns)
    return aliases.get(identity, identity)


def parse_credit(value: object) -> float | None:
    match = _NUMBER_RE.search(_clean(value))
    if not match:
        return None
    credit = float(match.group())
    if not math.isfinite(credit) or credit <= 0 or credit > 12:
        return None
    return credit


def parse_grades(value: object) -> tuple[int, ...]:
    text = _clean(value)
    if not text:
        return ()
    grades: set[int] = set()
    for start, end in _GRADE_RANGE_RE.findall(text):
        low, high = sorted((int(start), int(end)))
        grades.update(range(low, high + 1))
    grades.update(int(match) for match in _GRADE_RE.findall(text))
    # 공식 표에는 "학사3,4년", "2,3" 또는 단순 "2"처럼 일부 숫자에만
    # 학년 단위가 붙는 값도 많으므로 모든 고립된 1~4 숫자를 함께 읽는다.
    grades.update(int(value) for value in re.findall(r"(?<!\d)([1-6])(?!\d)", text))
    return tuple(sorted(grades))


def parse_semesters(value: object) -> tuple[int, ...]:
    text = _clean(value)
    if not text:
        return ()
    if any(token in text.lower() for token in ("매학기", "공통", "1, 2", "1,2", "1/2")):
        return (1, 2)
    semesters = {int(match) for match in _SEMESTER_RE.findall(text)}
    if not semesters and text in {"1", "2"}:
        semesters.add(int(text))
    if not semesters:
        semesters.update(int(value) for value in re.findall(r"(?<!\d)([12])(?!\d)", text))
    return tuple(sorted(semesters))


def _strip_korean_particle(token: str) -> str:
    for particle in ("으로", "하고", "에서", "에게", "부터", "까지", "처럼", "보다", "와", "과", "을", "를", "은", "는", "이", "가", "에", "로"):
        if token.endswith(particle) and len(token) - len(particle) >= 2:
            return token[: -len(particle)]
    return token


def _tokenize(value: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(value):
        token = _strip_korean_particle(raw.lower())
        if token and token not in _GENERIC_INTEREST_TOKENS:
            tokens.append(token)
    return tuple(tokens)


@dataclass(frozen=True)
class CourseRecord:
    department: str
    college: str
    course_code: str
    title: str
    credit: float | None
    grades: tuple[int, ...]
    semesters: tuple[int, ...]
    course_type: str
    description: str
    source_url: str
    raw_text: str
    record_type: str
    availability_status: str = "curriculum_only"
    source_type: str = ""
    source_priority: int = 0

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> "CourseRecord":
        raw_text = _first(row, ("raw_text", "raw_data"))
        return cls(
            department=_first(row, ("department_name", "major", "department", "학과", "학과명")),
            college=_first(row, ("college_name", "college", "단과대학")),
            course_code=_first(row, ("course_code", "학수번호", "과목코드")),
            title=_first(row, ("course_name", "title", "교과목명", "과목명", "국문교과목명")),
            credit=parse_credit(_first(row, ("credit_value", "credit", "학점"))),
            grades=parse_grades(_first(row, ("recommended_grades", "grade", "이수대상", "학년"))),
            semesters=parse_semesters(_first(row, ("offered_semesters", "semester", "개설학기", "학기"))),
            course_type=_first(row, ("course_type", "전공구분", "이수구분")),
            description=_first(row, ("description", "교과목해설", "해설", "비고")),
            source_url=_first(row, ("curriculum_url", "source_url", "url")),
            raw_text=raw_text,
            record_type=_first(row, ("record_type",)) or "table_row",
            availability_status=_first(row, ("availability_status",)) or "curriculum_only",
            source_type=_first(row, ("source_type",)),
            source_priority=int(float(_first(row, ("source_priority",)) or 0)),
        )

    @property
    def identity(self) -> str:
        return _normalized_identity(f"{self.course_code}:{self.title}" if self.course_code else f"{self.department}:{self.title}")

    @property
    def searchable_text(self) -> str:
        return " ".join(
            value
            for value in (
                self.title,
                self.course_code,
                self.course_type,
                self.description,
                self.raw_text,
            )
            if value
        ).lower()

    @property
    def is_required(self) -> bool:
        text = f"{self.course_type} {self.description} {self.raw_text}"
        return any(token in text for token in ("전공필수", "필수이수", "필수 이수"))

    @property
    def is_course_row(self) -> bool:
        if self.record_type not in {"table_row", "course"}:
            return False
        if not self.title or self.credit is None:
            return False
        # 합계/소계/졸업학점 행을 실제 교과목으로 추천하지 않는다.
        return self.title not in {"계", "합계", "소계", "졸업학점"} and "합계" not in self.title


@dataclass(frozen=True)
class CourseRecommendationProfile:
    major: str
    grade: int
    target_credits: float
    interests: tuple[str, ...] = ()
    semester: int | None = None
    completed_courses: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecommendedCourse:
    course: CourseRecord
    score: float
    reasons: tuple[str, ...]
    interest_matched: bool = False
    grade_unknown: bool = False
    semester_unknown: bool = False


@dataclass(frozen=True)
class CourseRecommendationPlan:
    profile: CourseRecommendationProfile
    courses: tuple[RecommendedCourse, ...]
    total_credits: float
    exact_credit_match: bool
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractedRecommendationProfile:
    profile: CourseRecommendationProfile | None
    missing_fields: tuple[str, ...] = ()
    interests_text: str = ""


def is_course_recommendation_query(query: str) -> bool:
    compact = re.sub(r"\s+", " ", _clean(query)).lower()
    course_term = any(term in compact for term in ("과목", "수업", "강의", "수강", "시간표"))
    return course_term and any(term in compact for term in _RECOMMENDATION_TERMS)


def _extract_interest_tokens(query: str, *, major: str = "") -> tuple[str, ...]:
    cleaned = _SEMESTER_RE.sub(" ", _GRADE_RE.sub(" ", _FALLBACK_CREDIT_RE.sub(" ", query)))
    major_identity = _normalized_identity(major)
    result: list[str] = []
    for token in _tokenize(cleaned):
        if token.isdigit():
            continue
        if major_identity and _normalized_identity(token) in major_identity:
            continue
        if token not in result:
            result.append(token)
    return tuple(result[:8])


def extract_recommendation_profile(
    query: str,
    *,
    major: str | None = None,
    grade: int | None = None,
    target_credits: float | None = None,
    semester: int | None = None,
    interests: Sequence[str] | None = None,
    completed_courses: Sequence[str] | None = None,
) -> ExtractedRecommendationProfile:
    text = _clean(query)
    if grade is None:
        match = _GRADE_RE.search(text)
        grade = int(match.group(1)) if match else None
    if semester is None:
        match = _SEMESTER_RE.search(text)
        semester = int(match.group(1)) if match else None
    if target_credits is None:
        match = _TARGET_CREDIT_RE.search(text) or _FALLBACK_CREDIT_RE.search(text)
        target_credits = float(match.group(1)) if match else None

    tokens = tuple(_clean(value).lower() for value in (interests or ()) if _clean(value))
    if not tokens:
        tokens = _extract_interest_tokens(text, major=major or "")

    missing: list[str] = []
    if not _clean(major):
        missing.append("학과")
    if grade not in {1, 2, 3, 4, 5, 6}:
        missing.append("학년")
    if target_credits is None or not (1 <= target_credits <= 24):
        missing.append("이번 학기에 들을 목표 학점(1~24학점)")
    if not tokens:
        missing.append("관심 분야나 듣고 싶은 과목")
    if missing:
        return ExtractedRecommendationProfile(
            profile=None,
            missing_fields=tuple(missing),
            interests_text=" ".join(tokens),
        )

    return ExtractedRecommendationProfile(
        profile=CourseRecommendationProfile(
            major=_clean(major),
            grade=int(grade),
            target_credits=float(target_credits),
            interests=tokens,
            semester=semester if semester in {1, 2} else None,
            completed_courses=tuple(_clean(value) for value in (completed_courses or ()) if _clean(value)),
        ),
        interests_text=" ".join(tokens),
    )


def infer_completed_courses(
    query: str,
    courses: Iterable[CourseRecord],
) -> tuple[str, ...]:
    text = _clean(query)
    if not any(
        marker in text
        for marker in ("이미 들", "전에 들", "수강했", "이수했", "이수한", "들은 과목", "들었던")
    ):
        return ()
    normalized_query = _normalized_identity(text)
    matches: list[str] = []
    for course in courses:
        identifiers = (course.course_code, course.title)
        if any(
            len(_normalized_identity(identifier)) >= 3
            and _normalized_identity(identifier) in normalized_query
            for identifier in identifiers
            if identifier
        ):
            key = course.course_code or course.title
            if key not in matches:
                matches.append(key)
    return tuple(matches)


def _interest_score(course: CourseRecord, interests: Sequence[str]) -> tuple[float, tuple[str, ...]]:
    title = course.title.lower()
    searchable = course.searchable_text
    score = 0.0
    matched: list[str] = []
    for raw_interest in interests:
        interest = raw_interest.lower().strip()
        if not interest:
            continue
        aliases = {interest}
        if interest in {"ai", "인공지능"}:
            aliases.update(
                {
                    "ai",
                    "인공지능",
                    "기계학습",
                    "머신러닝",
                    "딥러닝",
                    "데이터마이닝",
                    "데이터 분석",
                }
            )
        if interest in {"데이터", "데이터분석"}:
            aliases.update({"데이터", "통계", "분석"})
        if any(alias in title for alias in aliases):
            score += 5.0
            matched.append(raw_interest)
        elif any(alias in searchable for alias in aliases):
            score += 2.0
            matched.append(raw_interest)
    return score, tuple(dict.fromkeys(matched))


def _rank_course(
    course: CourseRecord,
    profile: CourseRecommendationProfile,
) -> RecommendedCourse | None:
    if not course.is_course_row:
        return None

    grade_unknown = not course.grades
    if course.grades and profile.grade not in course.grades:
        return None
    semester_unknown = bool(profile.semester and not course.semesters)
    if profile.semester and course.semesters and profile.semester not in course.semesters:
        return None

    interest_score, matched = _interest_score(course, profile.interests)

    score = interest_score
    reasons: list[str] = []
    if matched:
        reasons.append(f"관심 분야({', '.join(matched)})와 교과목 정보가 일치")
    else:
        reasons.append("관심 분야 직접 일치 정보는 없지만 학년·학기 조건에 적합")
    if course.grades:
        score += 2.0
        reasons.append(f"{profile.grade}학년 이수대상")
    else:
        score -= 1.5
    if profile.semester and course.semesters:
        score += 1.5
        reasons.append(f"{profile.semester}학기 개설 표기")
    if course.is_required:
        score += 1.25
        reasons.append("필수 이수 표기")
    if profile.grade <= 2 and "기초" in course.course_type:
        score += 0.75
        reasons.append("기초 단계 과목")
    if profile.grade >= 3 and any(token in course.course_type for token in ("전문", "심화")):
        score += 0.75
        reasons.append("심화 단계 과목")

    return RecommendedCourse(
        course=course,
        score=score,
        reasons=tuple(reasons),
        interest_matched=bool(matched),
        grade_unknown=grade_unknown,
        semester_unknown=semester_unknown,
    )


def _course_completeness(course: CourseRecord) -> int:
    return sum(
        (
            20 if course.is_course_row else 0,
            course.source_priority,
            5 if course.course_code else 0,
            4 if course.grades else 0,
            3 if course.semesters else 0,
            2 if course.course_type else 0,
            2 if course.description else 0,
            1 if re.search(r"[가-힣]", course.title) else 0,
        )
    )


def _select_credit_bundle(
    candidates: Sequence[RecommendedCourse],
    target_credits: float,
) -> tuple[RecommendedCourse, ...]:
    """점수 합을 최대화하면서 목표 학점에 가장 가까운 조합을 고릅니다."""
    unit = 2  # 0.5학점 단위 정수 DP
    target = int(round(target_credits * unit))
    maximum = target + 6  # 정확 조합이 없으면 최대 3학점 초과까지 탐색
    states: dict[int, tuple[float, tuple[int, ...]]] = {0: (0.0, ())}
    for index, candidate in enumerate(candidates[:40]):
        credit = candidate.course.credit
        if credit is None:
            continue
        value = int(round(credit * unit))
        for total, (score, selected) in list(states.items())[::-1]:
            next_total = total + value
            if next_total > maximum:
                continue
            next_score = score + candidate.score
            existing = states.get(next_total)
            if existing is None or next_score > existing[0]:
                states[next_total] = (next_score, selected + (index,))

    nonempty = [(total, value) for total, value in states.items() if total > 0]
    if not nonempty:
        return ()
    # 목표 학점과의 거리 → 초과보다 미달 선호 → 점수 → 과목 수가 적은 순서.
    best_total, (_, indexes) = min(
        nonempty,
        key=lambda item: (
            abs(item[0] - target),
            item[0] > target,
            -item[1][0],
            len(item[1][1]),
        ),
    )
    del best_total
    return tuple(candidates[index] for index in indexes)


def recommend_courses(
    profile: CourseRecommendationProfile,
    courses: Iterable[CourseRecord],
) -> CourseRecommendationPlan:
    major_identity = canonical_department_identity(profile.major)
    completed = {_normalized_identity(value) for value in profile.completed_courses}
    deduped: dict[str, CourseRecord] = {}
    for course in courses:
        if canonical_department_identity(course.department) != major_identity:
            continue
        if (
            course.identity in completed
            or _normalized_identity(course.course_code) in completed
            or _normalized_identity(course.title) in completed
        ):
            continue
        # 학수번호가 달라도 같은 학과·같은 과목명인 행은 구·신 교육과정이나
        # 트랙 표가 겹친 경우가 많아 한 번만 추천합니다. 반대로 공식 자료의
        # 동일 학수번호·서로 다른 과목명 충돌은 보존해 데이터 오류를 숨기지 않습니다.
        recommendation_identity = _normalized_identity(course.title) or course.identity
        existing = deduped.get(recommendation_identity)
        if existing is None or _course_completeness(course) > _course_completeness(existing):
            deduped[recommendation_identity] = course

    ranked = [
        recommendation
        for course in deduped.values()
        if (recommendation := _rank_course(course, profile)) is not None
    ]
    ranked.sort(
        key=lambda item: (
            item.grade_unknown,
            item.semester_unknown,
            -item.score,
            item.course.title,
        )
    )
    selected = _select_credit_bundle(ranked, profile.target_credits)
    total = sum(item.course.credit or 0 for item in selected)

    warnings: list[str] = []
    unmatched_count = sum(not item.interest_matched for item in selected)
    if unmatched_count:
        warnings.append(
            f"목표 학점을 맞추기 위해 관심 분야와 직접 일치하지 않는 "
            f"학년·학기 적합 과목 {unmatched_count}개를 포함했습니다."
        )
    if any(item.grade_unknown for item in selected):
        warnings.append("일부 과목은 공식 표에 권장 학년이 없어 학년 적합성을 추가 확인해야 합니다.")
    if any(item.semester_unknown for item in selected):
        warnings.append("일부 과목은 공식 표에 개설 학기가 없어 이번 학기 개설 여부를 추가 확인해야 합니다.")
    if selected and not math.isclose(total, profile.target_credits, abs_tol=0.01):
        warnings.append(
            f"확인 가능한 후보만으로는 {profile.target_credits:g}학점을 정확히 맞추지 못해 "
            f"{total:g}학점 조합을 제안합니다."
        )
    warnings.append("교과과정표 기반 후보이므로 실제 개설 여부·시간·정원은 nDRIMS에서 확인해야 합니다.")

    return CourseRecommendationPlan(
        profile=profile,
        courses=selected,
        total_credits=total,
        exact_credit_match=math.isclose(total, profile.target_credits, abs_tol=0.01),
        warnings=tuple(warnings),
    )


@lru_cache(maxsize=8)
def _load_course_catalog_cached(course_path_text: str, modified_ns: int) -> tuple[CourseRecord, ...]:
    del modified_ns  # 캐시 키로만 사용해 같은 프로세스에서도 파일 교체를 감지한다.
    course_path = Path(course_path_text)
    if not course_path.exists():
        return ()
    with course_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return tuple(CourseRecord.from_row(row) for row in csv.DictReader(handle))


def load_course_catalog(path: str | Path | None = None) -> tuple[CourseRecord, ...]:
    if path is not None:
        # Explicit paths remain available to migration tools and isolated tests.
        course_path = Path(path)
        modified_ns = course_path.stat().st_mtime_ns if course_path.exists() else -1
        return _load_course_catalog_cached(str(course_path.resolve()), modified_ns)

    # Runtime recommendations use the same canonical SourceDocument payloads as
    # indexing.  The import is local to keep this service usable by lightweight
    # parsing tests without initializing the whole ingestion module at import.
    try:
        from src.database import SessionLocal
        from src.pipelines.ingest import load_canonical_source_frame

        session = SessionLocal()
        try:
            frame = load_canonical_source_frame(session, "courses")
        finally:
            session.close()
        if frame.empty:
            return ()
        return tuple(CourseRecord.from_row(row) for row in frame.to_dict(orient="records"))
    except Exception:
        # A missing/uninitialized canonical DB is an unavailable dataset, not a
        # reason to silently answer from a stale CSV artifact.
        return ()


def format_recommendation_answer(plan: CourseRecommendationPlan) -> str:
    if not plan.courses:
        return (
            f"{plan.profile.major} {plan.profile.grade}학년과 관심 분야에 맞으면서 학점 정보가 확인되는 "
            "교과목을 현재 수집 자료에서 찾지 못했어요. 학과 교과과정 수집 상태를 먼저 보완해야 합니다."
        )

    semester = f", {plan.profile.semester}학기 기준" if plan.profile.semester else ""
    lines = [
        (
            f"{plan.profile.major} {plan.profile.grade}학년{semester}, "
            f"목표 {plan.profile.target_credits:g}학점에 맞춘 수강 후보예요. "
            f"현재 조합은 총 {plan.total_credits:g}학점입니다."
        ),
        "",
    ]
    for index, item in enumerate(plan.courses, start=1):
        course = item.course
        code = f" ({course.course_code})" if course.course_code else ""
        lines.append(f"{index}. {course.title}{code} — {course.credit:g}학점")
        if item.reasons:
            lines.append(f"- 추천 근거: {', '.join(item.reasons)}")
        if course.course_type:
            lines.append(f"- 이수구분: {course.course_type}")
        if course.source_url:
            lines.append(f"- [교과과정 원문]({course.source_url})")
        lines.append("")
    lines.append("확인할 점")
    lines.extend(f"- {warning}" for warning in plan.warnings)
    return "\n".join(lines).strip()


__all__ = [
    "CourseRecord",
    "CourseRecommendationPlan",
    "CourseRecommendationProfile",
    "ExtractedRecommendationProfile",
    "RecommendedCourse",
    "canonical_department_identity",
    "extract_recommendation_profile",
    "format_recommendation_answer",
    "infer_completed_courses",
    "is_course_recommendation_query",
    "load_course_catalog",
    "parse_credit",
    "parse_grades",
    "parse_semesters",
    "recommend_courses",
]
