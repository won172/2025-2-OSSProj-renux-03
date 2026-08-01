from __future__ import annotations

import csv
from pathlib import Path

from src.config import DATA_SOURCES
from src.services.course_recommendation import (
    CourseRecord,
    CourseRecommendationProfile,
    canonical_department_identity,
    extract_recommendation_profile,
    infer_completed_courses,
    parse_grades,
    parse_semesters,
    recommend_courses,
)


def _course(
    code: str,
    title: str,
    *,
    credit: str = "3",
    grade: str = "3학년",
    semester: str = "1학기",
    description: str = "",
    course_type: str = "전문",
) -> CourseRecord:
    return CourseRecord.from_row(
        {
            "department_name": "통계학과",
            "course_code": code,
            "course_name": title,
            "credit": credit,
            "grade": grade,
            "semester": semester,
            "description": description,
            "course_type": course_type,
            "record_type": "table_row",
            "curriculum_url": "https://stat.dongguk.edu/curriculum",
        }
    )


def test_grade_and_semester_normalization_handles_official_table_variants():
    assert parse_grades("학사1~2년") == (1, 2)
    assert parse_grades("2,3") == (2, 3)
    assert parse_grades("약학 5~6학년") == (5, 6)
    assert parse_semesters("1, 2") == (1, 2)
    assert parse_semesters("2학기") == (2,)


def test_current_department_aliases_resolve_to_official_curriculum_name():
    assert canonical_department_identity("컴퓨터·AI학부/인공지능전공") == canonical_department_identity(
        "컴퓨터·AI학부"
    )
    assert canonical_department_identity("국제통상학전공") == canonical_department_identity("국제통상학과")
    assert canonical_department_identity("영어영문학부 영어문학전공") == canonical_department_identity(
        "영어영문학부"
    )
    assert canonical_department_identity("영어영문학부 영어통번역학전공") == canonical_department_identity(
        "영어영문학부"
    )


def test_every_catalog_department_has_structured_course_rows_after_alias_resolution():
    data_dir = Path(DATA_SOURCES["courses_all"]).parent
    with (data_dir / "dongguk_departments_catalog.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        catalog_departments = {
            row["department_name"].strip() for row in csv.DictReader(handle)
        }
    with Path(DATA_SOURCES["courses_all"]).open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        course_departments = {
            canonical_department_identity(row["department_name"])
            for row in csv.DictReader(handle)
            if row.get("record_type") == "table_row"
        }

    missing = sorted(
        department
        for department in catalog_departments
        if canonical_department_identity(department) not in course_departments
    )
    assert missing == []


def test_profile_extraction_requires_personalization_fields():
    extracted = extract_recommendation_profile(
        "AI 관련 수업 추천해줘",
        major="통계학과",
    )

    assert extracted.profile is None
    assert "학년" in extracted.missing_fields
    assert "이번 학기에 들을 목표 학점(1~24학점)" in extracted.missing_fields
    assert extracted.interests_text == "ai"


def test_profile_extraction_strips_korean_particles_from_interests():
    extracted = extract_recommendation_profile(
        "3학년이고 1학기 18학점, AI와 데이터에 관심 있어. 수업 추천해줘",
        major="통계학과",
    )

    assert extracted.profile is not None
    assert extracted.profile.interests == ("ai", "데이터")


def test_recommendation_filters_grade_semester_and_completed_courses_and_matches_credits():
    courses = [
        _course("STA3001", "머신러닝", description="인공지능과 데이터 분석"),
        _course("STA3002", "딥러닝", description="인공지능 심화"),
        _course("STA3003", "데이터마이닝", description="대규모 데이터 분석"),
        _course("STA3004", "AI통계프로젝트", description="인공지능 프로젝트"),
        _course("STA2001", "통계프로그래밍", grade="2학년", description="데이터 분석"),
        _course("STA3005", "인공지능특강", semester="2학기", description="AI"),
        _course("STA3006", "데이터시각화", description="데이터 분석"),
    ]
    profile = CourseRecommendationProfile(
        major="통계학과",
        grade=3,
        target_credits=9,
        interests=("ai", "데이터"),
        semester=1,
        completed_courses=("STA3003",),
    )

    plan = recommend_courses(profile, courses)

    assert plan.exact_credit_match is True
    assert plan.total_credits == 9
    codes = {item.course.course_code for item in plan.courses}
    assert "STA3003" not in codes
    assert "STA2001" not in codes
    assert "STA3005" not in codes
    assert len(codes) == 3


def test_recommendation_fills_remaining_credits_with_grade_semester_matches():
    courses = [
        _course("STA3001", "머신러닝", description="인공지능과 데이터 분석"),
        _course("STA3002", "회귀분석"),
        _course("STA3003", "표본조사론"),
    ]
    profile = CourseRecommendationProfile(
        major="통계학과",
        grade=3,
        target_credits=9,
        interests=("ai",),
        semester=1,
    )

    plan = recommend_courses(profile, courses)

    assert plan.exact_credit_match is True
    assert plan.total_credits == 9
    assert plan.courses[0].course.course_code == "STA3001"
    assert sum(not item.interest_matched for item in plan.courses) == 2
    assert any("직접 일치하지 않는" in warning for warning in plan.warnings)


def test_recommendation_never_treats_summary_rows_as_courses():
    plan = recommend_courses(
        CourseRecommendationProfile(
            major="통계학과",
            grade=3,
            target_credits=3,
            interests=("데이터",),
        ),
        [
            _course("", "합계", credit="18", description="데이터"),
            _course("STA3001", "데이터분석", credit="3", description="데이터"),
        ],
    )

    assert [item.course.title for item in plan.courses] == ["데이터분석"]


def test_duplicate_course_code_prefers_structured_korean_course_row():
    incomplete = CourseRecord.from_row(
        {
            "department_name": "통계학과",
            "course_code": "STA3001",
            "course_name": "Data Analysis",
            "record_type": "table_row",
        }
    )
    complete = _course("STA3001", "데이터분석", description="데이터 분석")

    plan = recommend_courses(
        CourseRecommendationProfile(
            major="통계학과",
            grade=3,
            target_credits=3,
            interests=("데이터",),
            semester=1,
        ),
        [incomplete, complete],
    )

    assert [item.course.title for item in plan.courses] == ["데이터분석"]


def test_same_course_title_with_different_codes_is_recommended_once():
    plan = recommend_courses(
        CourseRecommendationProfile(
            major="통계학과",
            grade=3,
            target_credits=6,
            interests=("데이터",),
            semester=1,
        ),
        [
            _course("STA3001", "데이터분석", description="데이터"),
            _course("STA3999", "데이터분석", description="데이터"),
            _course("STA3002", "머신러닝", description="데이터"),
        ],
    )

    assert [item.course.title for item in plan.courses].count("데이터분석") == 1


def test_completed_course_can_be_inferred_from_conversation_text():
    courses = [
        _course("STA3001", "데이터분석"),
        _course("STA3002", "머신러닝"),
    ]

    completed = infer_completed_courses("데이터분석은 이미 들었고 AI 수업을 추천해줘", courses)

    assert completed == ("STA3001",)
