"""데이터셋별 청크 표현 계약 (pipeline-audit 09 P0-2, P0-3, P2).

- 수집 bookkeeping 필드(collected_at, collection_status, data_quality_score …)는
  어떤 데이터셋에서도 ``chunk_text``/``retrieval_text``에 나타나지 않는다.
- courses는 화이트리스트 필드만 한글 라벨로 찍고, collected_at이 달라도 청크
  텍스트가 같다.
- rules는 조문(제N조) 경계로 1차 분할하고, 긴 조문의 이어지는 조각에는 조문
  제목을 붙인다. 본문 속 참조("제5조에 따라")에서는 나누지 않는다.

모두 인메모리 합성 데이터만 쓴다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import CHUNK_OVERLAP, CHUNK_SIZE  # noqa: E402
from src.pipelines import ingest  # noqa: E402
from src.services.retrieval_context import enrich_retrieval_fields  # noqa: E402
from src.utils.preprocess import (  # noqa: E402
    chunk_text,
    make_chunk_id,
    make_doc_id,
    normalize_whitespace,
    split_rule_articles,
)


# 라벨과, 라벨 없이 값만 새는 경우를 잡기 위한 고유 sentinel 값.
BOOKKEEPING = {
    "collected_at": "2031-01-02T03:04:05+00:00",
    "collection_status": "fresh_SENTINEL_STATUS",
    "collection_error": "SENTINEL_ERROR",
    "data_quality_score": "SENTINEL_QUALITY_77",
    "course_code_conflict": "SENTINEL_CONFLICT",
    "availability_status": "SENTINEL_AVAILABILITY",
    "record_type": "SENTINEL_RECORD_TYPE",
    "source_priority": "SENTINEL_PRIORITY",
    "raw_text": "SENTINEL_RAW_TEXT",
    "db_id": 987654321,
}
FORBIDDEN_LABELS = (
    "collected_at",
    "collection_status",
    "collection_error",
    "data_quality_score",
    "course_code_conflict",
    "availability_status",
    "curriculum_url",
    "record_type",
    "source_priority",
    "raw_text",
    "department_url",
    "db_id",
    "document_key",
)


def _texts(chunks: pd.DataFrame) -> list[str]:
    enriched = enrich_retrieval_fields(chunks)
    return [
        *enriched["chunk_text"].astype(str).tolist(),
        *enriched["retrieval_text"].astype(str).tolist(),
    ]


def _assert_no_bookkeeping(chunks: pd.DataFrame, *, sentinels: bool = True) -> None:
    assert not chunks.empty
    for text in _texts(chunks):
        for label in FORBIDDEN_LABELS:
            assert label not in text, (label, text)
        if sentinels:
            for value in BOOKKEEPING.values():
                assert str(value) not in text, (value, text)
            assert "2031-01-02" not in text


# --- courses ---------------------------------------------------------------

def _course_row(**overrides) -> dict:
    row = {
        "title": "4차 산업사회와 빅데이터",
        "course_name": "4차 산업사회와 빅데이터",
        "학수번호": "MIS2001",
        "course_code": "MIS2001",
        "credit": "3",
        "credit_value": "3.0",
        "course_type": "전공 기초과정",
        "grade": "2",
        "semester": "1",
        "description": "빅데이터 분석의 기초 개념과 산업 적용 사례를 다룬다.",
        "english_title": "Big Data in the 4th Industrial Society",
        "college_name": "경영대학",
        "department_name": "경영정보학과",
        "major": "경영정보학과",
        "curriculum_title": "교과과정 > 교과목 이수",
        "curriculum_url": "https://mis.dongguk.edu/page/411",
        "department_url": "https://mis.dongguk.edu/",
        "section_title": "전공과목",
        "source_type": "department_curriculum",
        "_source_table": "department_curriculum",
        "unexpected_new_crawler_column": "SENTINEL_NEW_COLUMN",
        **BOOKKEEPING,
    }
    row.update(overrides)
    return row


def test_course_chunks_contain_only_whitelisted_korean_labels():
    chunks = ingest.build_course_chunks(pd.DataFrame([_course_row(document_key="courses:k1")]))

    _assert_no_bookkeeping(chunks)
    text = chunks.iloc[0]["chunk_text"]
    # 제목은 "[제목]" 접두로 한 번만 나온다(본문에 "교과목명:" 줄을 다시 찍지 않는다).
    assert text.startswith("[4차 산업사회와 빅데이터]\n\n")
    assert text.count("4차 산업사회와 빅데이터") == 1
    assert chunks.iloc[0]["title"] == "4차 산업사회와 빅데이터"
    for expected in (
        "학수번호: MIS2001",
        "학점: 3",
        "이수구분: 전공 기초과정",
        "이수대상: 2학년",
        "개설학기: 1학기",
        "개설학과: 경영정보학과",
        "단과대학: 경영대학",
        "교과목 설명: 빅데이터 분석의 기초 개념과 산업 적용 사례를 다룬다.",
    ):
        assert expected in text, (expected, text)
    # 원문 URL은 본문이 아니라 출처 메타데이터로 남는다.
    assert "https://" not in text
    assert "SENTINEL_NEW_COLUMN" not in text
    assert "course_code:" not in text and "credit:" not in text


def test_course_chunk_text_is_deterministic_across_collected_at():
    first = ingest.build_course_chunks(pd.DataFrame([_course_row(document_key="courses:k1")]))
    second = ingest.build_course_chunks(pd.DataFrame([_course_row(
        document_key="courses:k1",
        collected_at="2032-12-31T23:59:59+00:00",
        collection_status="stale",
        data_quality_score="10",
    )]))

    assert first["chunk_text"].tolist() == second["chunk_text"].tolist()
    assert first["chunk_id"].tolist() == second["chunk_id"].tolist()
    assert (
        enrich_retrieval_fields(first)["retrieval_text"].tolist()
        == enrich_retrieval_fields(second)["retrieval_text"].tolist()
    )


def test_course_identity_and_metadata_are_preserved():
    keyed = ingest.build_course_chunks(pd.DataFrame([_course_row(document_key="courses:k1")]))
    row = keyed.iloc[0]
    assert row["doc_id"] == "courses:k1"
    assert row["chunk_id"] == make_chunk_id("courses:k1", 0)
    assert row["url"] == "https://mis.dongguk.edu/page/411"
    assert row["course_id"] == BOOKKEEPING["db_id"]
    assert row["course_code"] == "MIS2001"
    assert row["major"] == "경영정보학과"
    assert row["college_name"] == "경영대학"
    assert row["credit"] == "3.0"
    assert row["course_type"] == "전공 기초과정"
    # 검색·필터 코드가 읽는 bookkeeping은 메타데이터로 계속 전달된다.
    assert row["course_code_conflict"] == BOOKKEEPING["course_code_conflict"]
    assert row["availability_status"] == BOOKKEEPING["availability_status"]
    assert row["data_quality_score"] == BOOKKEEPING["data_quality_score"]
    assert row["collection_status"] == BOOKKEEPING["collection_status"]

    # document_key가 없을 때의 폴백 식별자 공식도 그대로다.
    unkeyed = ingest.build_course_chunks(pd.DataFrame([_course_row()]))
    assert unkeyed.iloc[0]["doc_id"] == make_doc_id(
        "courses",
        "경영정보학과",
        "MIS2001",
        "https://mis.dongguk.edu/page/411",
        "전공과목",
        "department_curriculum",
    )


def test_canonical_course_row_without_content_still_gets_one_chunk():
    # 정본 행을 건너뛰면 계보 검사가 source_missing_artifact를 낸다.
    frame = pd.DataFrame([{
        **BOOKKEEPING,
        "curriculum_url": "https://mis.dongguk.edu/page/1",
        "document_key": "courses:empty",
    }])
    chunks = ingest.build_course_chunks(frame)
    assert len(chunks) == 1
    assert chunks.iloc[0]["doc_id"] == "courses:empty"
    assert chunks.iloc[0]["chunk_text"] == "[교과목 정보]\n\n교과목명: 교과목 정보"
    _assert_no_bookkeeping(chunks)


# data/dongguk_courses_all.csv의 실제 행 모양(공식 PDF 행: 이론·실습·원어강의·비고가
# raw_text에만 있다).
OFFICIAL_PDF_ROW = {
    "college_name": "경영대학",
    "department_name": "경영정보학과",
    "department_url": "https://www.dongguk.edu/page/137",
    "curriculum_title": "2026학년도 공식 교과과정",
    "curriculum_url": "https://www.dongguk.edu/resources/files/curriculum/2026/7. 경영대학.pdf",
    "source_type": "official_curriculum_pdf",
    "section_title": "교과 교육과정",
    "record_type": "table_row",
    "course_code": "MIS4079",
    "학수번호": "MIS4079",
    "major": "경영정보학과",
    "title": "AI기반의비즈니스혁신",
    "course_name": "AI기반의비즈니스혁신",
    "description": "2026년신설",
    "credit": "3",
    "semester": "1",
    "grade": "4",
    "course_type": "전문",
    "english_title": "",
    "raw_text": (
        "course_code: MIS4079\ntitle: AI기반의비즈니스혁신\ncredit: 3\ntheory_hours: 3\n"
        "practice_hours: 0\noriginal_language: 영어\ncourse_type: 전문\ngrade: 4\nsemester: 1\n"
        "remarks: 2026년신설"
    ),
    "credit_value": "3.0",
    "recommended_grades": "4",
    "offered_semesters": "1",
    "is_required": "False",
    "availability_status": "curriculum_only",
    "collection_status": "fresh",
    "collection_error": "",
    "collected_at": "2026-08-23T18:01:40+00:00",
    "curriculum_year": "2026.0",
    "source_page": "79.0",
    "source_priority": "100.0",
    "data_quality_score": "100",
    "course_code_conflict": "False",
    "document_key": "courses:pdf",
}

# 학과 표 행: 매핑되지 않은 한글 열(설계, 개설학과(전공), 교과과정영역 …)과
# 버려야 할 열(col_N, 연도형 키, No.)이 raw_text에만 있다.
DEPARTMENT_TABLE_ROW = {
    "college_name": "공과대학",
    "department_name": "산업시스템공학과",
    "department_url": "https://ise.dongguk.edu",
    "curriculum_title": "교육과정",
    "curriculum_url": "https://ise.dongguk.edu/page/100",
    "source_type": "curriculum_table",
    "section_title": "전공 교육과정",
    "record_type": "table_row",
    "course_code": "ISE2025",
    "학수번호": "ISE2025",
    "major": "산업시스템공학과",
    "title": "3D모델링",
    "course_name": "3D모델링",
    "description": "선택필수",
    "credit": "3",
    "semester": "1",
    "grade": "학사2년",
    "course_type": "기초",
    "raw_text": (
        "개설학과(전공): 산업시스템공학전공\ncourse_code: ISE2025\ntitle: 3D모델링\ncredit: 3\n"
        "이론: 3\n실습: 0\n설계: 1\n교과과정영역: 설계역량\n세부전공목표: 스마트제조 설계 역량\n"
        "권장 비교과 프로그램: 캡스톤 박람회\n이론__2: 3\ncol_1: SENTINEL_COL\n2019: SENTINEL_YEAR\n"
        "2016.02 이전: SENTINEL_YEAR2\nNo.: 17\ncollected_at: 2031-01-02T03:04:05+00:00\n"
        "course_type: 기초\ngrade: 학사2년\nsemester: 1\ndescription: 선택필수"
    ),
    "availability_status": "curriculum_only",
    "collection_status": "fresh",
    "collected_at": "2026-08-23T18:04:06+00:00",
    "data_quality_score": "85",
    "course_code_conflict": "False",
    "document_key": "courses:dept",
}


def test_official_pdf_row_projects_raw_text_only_fields():
    chunks = ingest.build_course_chunks(pd.DataFrame([OFFICIAL_PDF_ROW]))
    assert len(chunks) == 1
    _assert_no_bookkeeping(chunks, sentinels=False)
    text = chunks.iloc[0]["chunk_text"]
    for expected in (
        "학수번호: MIS4079",
        "학점: 3",
        "이론시간: 3",
        "실습시간: 0",
        "이수구분: 전문",
        "이수대상: 4학년",
        "개설학기: 1학기",
        "원어강의: 영어",
        "교육과정: 2026학년도",
        "교과목 설명: 2026년신설",
    ):
        assert expected in text, (expected, text)
    assert text.count("2026년신설") == 1
    assert text.count("AI기반의비즈니스혁신") == 1
    assert "https://" not in text
    assert "79.0" not in text and "100.0" not in text and "2026.0" not in text


def test_department_table_row_keeps_unmapped_korean_headers_only():
    chunks = ingest.build_course_chunks(pd.DataFrame([DEPARTMENT_TABLE_ROW]))
    assert len(chunks) == 1
    _assert_no_bookkeeping(chunks)
    text = chunks.iloc[0]["chunk_text"]
    for expected in (
        "이론시간: 3",
        "실습시간: 0",
        "설계: 1",
        "개설학과(전공): 산업시스템공학전공",
        "교과과정영역: 설계역량",
        "세부전공목표: 스마트제조 설계 역량",
        "권장 비교과 프로그램: 캡스톤 박람회",
        "이수대상: 학사2년",
    ):
        assert expected in text, (expected, text)
    for dropped in ("SENTINEL_COL", "SENTINEL_YEAR", "col_1", "No.", "이론:", "실습:"):
        assert dropped not in text, (dropped, text)
    # 이미 찍은 값(이수구분·학기·설명 …)은 raw_text에서 반복하지 않는다.
    assert text.count("선택필수") == 1
    assert text.count("기초") == 1
    assert text.count("3D모델링") == 1


def test_course_legacy_korean_columns_and_duplicate_description():
    frame = pd.DataFrame([{
        "국문교과목명": "회귀분석",
        "학수번호": "STA3001",
        "영문명": "Regression Analysis",
        "학점": "3",
        "이수구분": "전공필수",
        "이수대상": "3학년",
        "개설학기": "2",
        "해설": "선형 회귀모형을 다룬다.",
        "major": "통계학과",
        "_source_table": "combined_statistics",
        "remarks": "선형 회귀모형을 다룬다.",
    }])
    text = ingest.build_course_chunks(frame).iloc[0]["chunk_text"]
    assert text.startswith("[회귀분석]\n\n")
    assert "영문명: Regression Analysis" in text
    assert "개설학기: 2학기" in text
    assert "이수대상: 3학년" in text
    assert text.count("선형 회귀모형을 다룬다.") == 1


# --- rules -----------------------------------------------------------------

LONG_BODY = "학생은 매 학기 소정의 기간 내에 수강신청을 하여야 하며 신청 학점의 범위는 따로 정한다. " * 15
RULE_TEXT = (
    "동국대학교 학칙 "
    "제1장 총칙 "
    "제1조(목적) 이 학칙은 동국대학교의 교육목적 달성에 필요한 사항을 규정함을 목적으로 한다. "
    "제2조(정의) 이 학칙에서 사용하는 용어의 뜻은 다음과 같다. 학생이란 이 대학교에 재적 중인 자를 말한다. "
    "제3조(수업연한) 수업연한은 4년으로 한다. 다만, 제2조에 따라 정한 편입학생과 "
    "제2조(정의)에 따른 시간제 등록생 및 「고등교육법」 제23조(학점의 인정 등) 해당자는 그러하지 아니하다. "
    "제2장 학사 "
    f"제12조(수강신청) {LONG_BODY}"
    "제12조의2(수강정정) 수강정정은 개강 후 1주 이내에 하며, 세부 절차는 총장이 따로 정한다. "
    "제13조(삭제) "
    "제14조(성적) 성적은 제12조의 규정에 따라 수강신청한 교과목에 한하여 부여한다."
)


def test_split_rule_articles_starts_each_segment_at_article_boundary():
    segments = split_rule_articles(RULE_TEXT, CHUNK_SIZE, CHUNK_OVERLAP)

    starts = segments
    assert starts[0].startswith("동국대학교 학칙")
    assert "제1장 총칙" in starts[0] and "제1조(목적)" in starts[0]
    assert starts[1].startswith("제2조(정의)")
    assert starts[2].startswith("제3조(수업연한)")
    # 본문 속 참조는 경계가 아니다.
    assert "제2조에 따라" in starts[2]
    assert "제2조(정의)에 따른" in starts[2]
    assert "「고등교육법」 제23조(학점의 인정 등)" in starts[2].replace("」제", "」 제")
    # 장 제목은 다음 조문 앞에 붙는다.
    assert starts[3].startswith("제2장 학사")
    assert "제12조(수강신청)" in starts[3]

    long_parts = [s for s in segments if "수강신청을 하여야" in s]
    assert len(long_parts) >= 2
    for part in long_parts[1:]:
        assert part.startswith("제12조(수강신청)\n")
    assert all(len(s) <= CHUNK_SIZE for s in segments)

    assert any(s.startswith("제12조의2(수강정정)") for s in segments)
    # 짧은 "제13조(삭제)"는 다음 조문 앞에 붙는다.
    last = segments[-1]
    assert last.startswith("제13조(삭제)")
    assert "제14조(성적)" in last and "제12조의 규정에 따라" in last

    # 모든 조문 본문이 빠짐없이 남는다.
    joined = "".join(segments).replace(" ", "").replace("\n", "")
    for marker in ("제1조(목적)", "제2조(정의)", "제3조(수업연한)", "제12조의2(수강정정)", "제13조(삭제)", "제14조(성적)"):
        assert marker in joined


def test_split_rule_articles_handles_line_start_markers_and_references():
    text = (
        "제1조(목적) 이 규정은 동국대학교 학생에 대한 장학금 지급에 관한 사항을 정함을 목적으로 한다.\n"
        "제2조 장학금은 성적과 가계곤란을 고려하여 지급하며 제1조에 따른 목적을 따른다.\n"
        "제5조제1항에 해당하는 학생은 신청할 수 없다는 점을 유의하여야 한다.\n"
        "제3조의2(특별장학금) 특별장학금은 총장이 정하는 바에 따라 별도로 지급한다."
    )
    segments = split_rule_articles(text, CHUNK_SIZE, CHUNK_OVERLAP)
    assert [s[:6] for s in segments] == ["제1조(목적", "제2조 장학", "제3조의2("]
    assert "제5조제1항에 해당하는" in segments[1]


def test_split_rule_articles_falls_back_without_article_markers():
    text = "장학금 지급에 관한 안내입니다. " * 60
    assert split_rule_articles(text, CHUNK_SIZE, CHUNK_OVERLAP) == chunk_text(text, CHUNK_SIZE, CHUNK_OVERLAP)
    reference_only = "이 지침은 학칙 제5조에 따라 운영한다. " * 40
    assert split_rule_articles(reference_only, CHUNK_SIZE, CHUNK_OVERLAP) == chunk_text(
        reference_only, CHUNK_SIZE, CHUNK_OVERLAP
    )


def test_build_rule_chunks_uses_article_units_and_keeps_identity():
    row = {
        "title": "학칙",
        "filename": "학칙.hwp",
        "relative_dir": "제1편",
        "text": RULE_TEXT,
        "source_url": "https://rule.dongguk.edu/current",
        "source_version": "69:1",
        "published_at": "2026-01-01",
        "document_key": "rules:학칙",
        **BOOKKEEPING,
    }
    chunks = ingest.build_rule_chunks(pd.DataFrame([row]))

    _assert_no_bookkeeping(chunks)
    expected = split_rule_articles(RULE_TEXT, CHUNK_SIZE, CHUNK_OVERLAP)
    assert chunks["chunk_text"].tolist() == [f"[학칙]\n\n{segment}" for segment in expected]
    assert set(chunks["doc_id"]) == {"rules:학칙"}
    assert chunks["chunk_id"].tolist() == [make_chunk_id("rules:학칙", i) for i in range(len(chunks))]
    assert set(chunks["url"]) == {"https://rule.dongguk.edu/current"}
    assert set(chunks["source_version"]) == {"69:1"}
    assert set(chunks["rule_id"]) == {BOOKKEEPING["db_id"]}

    unkeyed = ingest.build_rule_chunks(pd.DataFrame([{k: v for k, v in row.items() if k != "document_key"}]))
    assert set(unkeyed["doc_id"]) == {make_doc_id("rules", "제1편", "학칙.hwp", "", "", "")}


# --- other datasets: bookkeeping never reaches indexed text -----------------

def test_notice_chunks_exclude_bookkeeping():
    frame = pd.DataFrame([{
        "게시판": "학사공지",
        "제목": "2026학년도 수강신청 안내",
        "게시일": "2026-02-01",
        "상세URL": "https://www.dongguk.edu/article/HAKSANOTICE/detail/1",
        "본문": "수강신청은 2월 10일부터 진행합니다.",
        "첨부파일": [],
        "document_key": "notices:1",
        **BOOKKEEPING,
    }])
    _assert_no_bookkeeping(ingest.build_notice_chunks(frame))


def test_schedule_chunks_exclude_bookkeeping():
    frame = pd.DataFrame([{
        "title": "개강",
        "start_date": "2026-03-02",
        "end_date": "2026-03-02",
        "category": "학사",
        "department": "학사지원팀",
        "content": "1학기 개강",
        "document_key": "schedule:1",
        **BOOKKEEPING,
    }])
    _assert_no_bookkeeping(ingest.build_schedule_chunks(frame))


def test_meal_chunks_exclude_bookkeeping():
    frame = pd.DataFrame([{
        "date": "2026-03-03",
        "weekday": "화",
        "restaurant": "상록원",
        "menu_text": "김치찌개, 쌀밥",
        "is_closed": "false",
        **BOOKKEEPING,
    }])
    _assert_no_bookkeeping(ingest.build_meal_chunks(frame))


def test_staff_chunks_exclude_bookkeeping_labels():
    frame = pd.DataFrame([{
        "조직(트리)": "동국대학교 > 학사지원본부",
        "성명": "김**",
        "직위": "팀장",
        "담당업무": "학사 운영 총괄",
        "전화번호": "02-2260-3000",
        "document_key": "staff:1",
        "db_id": BOOKKEEPING["db_id"],
    }])
    _assert_no_bookkeeping(ingest.build_staff_chunks(frame), sentinels=False)
    text = ingest.build_staff_chunks(frame).iloc[0]["chunk_text"]
    assert str(BOOKKEEPING["db_id"]) not in text


def test_staff_projection_labels_content_and_omits_title_duplicates():
    row = {
        "조직(트리)": "학사지원팀",
        "부서경로": "동국대학교 > 학사지원팀",
        "성명": "김**",
        "직위": "팀장",
        "직책": "학사 책임자",
        "담당업무": "수강신청 운영",
        "전화번호": "02-2260-3000",
        "이메일": "staff@example.test",
        "document_key": "staff:person",
        **BOOKKEEPING,
    }
    chunks = ingest.build_staff_chunks(pd.DataFrame([row]))
    assert len(chunks) == 1
    _assert_no_bookkeeping(chunks, sentinels=False)
    chunk = chunks.iloc[0]
    assert chunk["chunk_text"] == (
        "[학사지원팀 - 김**]\n\n소속: 학사지원팀\n\n"
        "부서경로: 동국대학교 > 학사지원팀\n\n직위: 팀장\n\n"
        "직책: 학사 책임자\n\n담당업무: 수강신청 운영\n\n"
        "전화번호: 02-2260-3000\n\n이메일: staff@example.test"
    )
    assert chunk["chunk_text"].count("김**") == 1
    assert "정보:" not in chunk["chunk_text"]
    assert chunk["staff_position"] == "팀장"
    assert chunk["staff_role"] == "수강신청 운영"
    assert chunk["staff_phone"] == "02-2260-3000"
    assert chunk["doc_id"] == "staff:person"
    assert chunk["chunk_id"] == make_chunk_id("staff:person", 0)


def test_staff_legacy_fields_are_labeled_and_bookkeeping_is_not_projected():
    chunks = ingest.build_staff_chunks(pd.DataFrame([{
        "조직(트리)": "전산팀", "Data_0": "이**", "Data_1": "팀원",
        "Data_2": "서버 운영", "Data_3": "770-2773",
        "collection_status": "fresh_SENTINEL_STATUS",
        "document_key": "staff:legacy",
    }]))
    text = chunks.iloc[0]["chunk_text"]
    assert "[전산팀 - 이**]" in text
    assert "직위: 팀원" in text
    assert "담당업무: 서버 운영" in text
    assert "전화번호: 770-2773" in text
    assert "fresh_SENTINEL_STATUS" not in text


def test_staff_legacy_extra_data_is_kept_with_head_identity_when_unkeyed():
    frame = pd.DataFrame([{
        "조직(트리)": "전산팀",
        "Data_0": "이**",
        "Data_1": "팀원",
        "Data_2": "서버 운영",
        "Data_3": "평일 09:00~18:00",
        "Data_4": "770-2773",
    }])
    chunk = ingest.build_staff_chunks(frame).iloc[0]
    text = chunk["chunk_text"]
    assert "[전산팀 - 이**]" in text
    assert "직위: 팀원" in text
    assert "담당업무: 서버 운영" in text
    assert "기타: 평일 09:00~18:00" in text
    assert "전화번호: 770-2773" in text
    assert text.count("이**") == 1

    # HEAD hashed the unlabeled text in column order, even though the indexed
    # representation now uses labels. Keep both legacy IDs byte-identical.
    head_text = (
        "소속: 전산팀\n\n정보: 이** 팀원 서버 운영 평일 09:00~18:00"
        "\n\n전화번호: 770-2773"
    )
    head_doc_id = make_doc_id("staff", "전산팀", head_text)
    assert chunk["doc_id"] == head_doc_id
    assert chunk["chunk_id"] == make_chunk_id(head_doc_id, 0)


def test_schedule_is_one_labeled_chunk_even_when_content_is_long():
    content = "행사장 안내와 참석 절차를 확인하세요. " * 80
    chunks = ingest.build_schedule_chunks(pd.DataFrame([{
        "title": "가을 학위수여식", "content": content,
        "start_date": "2026-08-21", "end_date": "2026-08-22",
        "category": "학사일정", "department": "학사지원팀",
        "document_key": "schedule:long", "db_id": 12,
    }]))
    assert len(chunks) == 1
    chunk = chunks.iloc[0]
    normalized = normalize_whitespace(chunk["chunk_text"])
    assert "일정: 가을 학위수여식\n\n내용:" in normalized
    assert "기간: 2026-08-21 ~ 2026-08-22\n\n구분: 학사일정" in normalized
    assert "\n\n주관부서: 학사지원팀" in normalized
    assert chunk["doc_id"] == "schedule:long"
    assert chunk["chunk_id"] == make_chunk_id("schedule:long", 0)
    assert chunk["schedule_id"] == 12
    assert chunk["schedule_start"] == "2026-08-21"
    assert chunk["schedule_end"] == "2026-08-22"


def test_schedule_and_staff_empty_canonical_rows_keep_artifacts():
    schedule = ingest.build_schedule_chunks(pd.DataFrame([{"document_key": "schedule:empty"}]))
    staff = ingest.build_staff_chunks(pd.DataFrame([{"document_key": "staff:empty"}]))
    assert schedule["chunk_id"].tolist() == [make_chunk_id("schedule:empty", 0)]
    assert staff["chunk_id"].tolist() == [make_chunk_id("staff:empty", 0)]
    assert schedule.iloc[0]["chunk_text"].strip()
    assert staff.iloc[0]["chunk_text"].strip()


def test_notice_low_value_metadata_keeps_every_canonical_document():
    base = {
        "게시판": "학사공지", "게시일": "2026-06-19",
        "상세URL": "https://example.test/notice",
    }
    frame = pd.DataFrame([
        {**base, "제목": "본문 없음", "본문": "", "첨부파일": " [ ] ", "document_key": "notices:empty"},
        {**base, "제목": "첨부만 있음", "본문": "", "첨부파일": [{"name": "요강.pdf"}], "document_key": "notices:file"},
        {**base, "제목": "본문 있음", "본문": "신청 기간 안내", "첨부파일": [], "document_key": "notices:body"},
        {**base, "제목": "링크도 없음", "본문": "", "첨부파일": [], "상세URL": "", "document_key": "notices:bare"},
        {**base, "제목": "", "본문": "", "첨부파일": [], "상세URL": "", "document_key": "notices:untitled"},
    ])
    chunks = ingest.build_notice_chunks(frame)
    by_id = chunks.set_index("doc_id")
    assert set(by_id.index) == {"notices:empty", "notices:file", "notices:body", "notices:bare", "notices:untitled"}
    assert by_id.loc["notices:empty", "low_value"] == "1"
    assert by_id.loc["notices:bare", "low_value"] == "1"
    assert by_id.loc["notices:untitled", "low_value"] == "1"
    assert by_id.loc["notices:file", "low_value"] == "0"
    assert by_id.loc["notices:body", "low_value"] == "0"
    assert by_id.loc["notices:empty", "has_substantive_body"] == "0"
    assert "공지 제목: 링크도 없음" in by_id.loc["notices:bare", "chunk_text"]
    assert "공지 내용 확인 필요" in by_id.loc["notices:untitled", "chunk_text"]
    for doc_id in by_id.index:
        assert by_id.loc[doc_id, "chunk_id"] == make_chunk_id(doc_id, 0)
        assert by_id.loc[doc_id, "chunk_text"]


@pytest.mark.parametrize(
    ("url", "head_fallback"),
    [
        (
            "https://example.test/notice",
            "공지 제목: 본문 없음\n"
            "본문이 비어 있어 상세 내용은 공지 링크를 확인하세요: https://example.test/notice",
        ),
        ("", "공지 제목: 본문 없음"),
    ],
)
def test_titled_empty_notice_keeps_head_fallback_text(url: str, head_fallback: str):
    frame = pd.DataFrame([{
        "제목": "본문 없음", "본문": "", "게시판": "학사공지",
        "게시일": "2026-06-19", "상세URL": url, "첨부파일": [],
        "document_key": "notices:empty",
    }])
    chunk = ingest.build_notice_chunks(frame).iloc[0]
    assert chunk["chunk_text"] == (
        "[본문 없음]\n\n[게시판: 학사공지, 게시일: 2026-06-19]\n\n"
        + head_fallback
    )



@pytest.mark.parametrize(
    "reference",
    [
        "4 제51조(학사과정의 수료와 졸업)의 졸업학점 개정내용은 2020학년도 입학생부터 적용한다.",
        "2 제5조(대학원)의 <별표2> 및 제6조의 개정규정은 2021년 3월 1일부터 시행한다.",
        "제65조의 2(징계의결에 관한 특례)의 신설내용은 공포한 날부터 시행한다.",
        "종전의 제7조(학위수여)로 정한 기준은 폐지하고 새 기준을 적용하기로 한다.",
        "종전의 제8조(휴학)는 이 학칙 시행 전 휴학한 학생에게도 적용된다.",
        "다만 제9조(복학)에도 불구하고 군 휴학자는 복학 시기를 따로 정할 수 있다.",
    ],
)
def test_midline_reference_with_title_and_particle_is_not_a_boundary(reference):
    text = (
        "제1조(시행일) 이 학칙은 2020년 3월 1일부터 시행한다는 점을 여기에 분명히 정한다. "
        f"부칙 경과조치 {reference}"
    )
    segments = split_rule_articles(text, CHUNK_SIZE, CHUNK_OVERLAP)
    assert len(segments) == 1, segments
    assert segments[0].startswith("제1조(시행일)")


def test_midline_article_after_sentence_end_or_with_body_particle_is_a_boundary():
    # 실제 조문은 괄호 제목 뒤에 본문이 붙어 온다("제1조(목적)이 법인은…").
    text = (
        "학교법인 동국대학 정관 제1조(목적)이 법인은 대한민국의 교육이념과 불교정신에 입각하여 교육을 실시함을 목적으로 한다. "
        "제2조(명칭)이 법인은 학교법인 동국대학이라 칭하며 그 명칭은 대외적으로 이 이름을 사용한다."
    )
    segments = split_rule_articles(text, CHUNK_SIZE, CHUNK_OVERLAP)
    assert [s[:7] for s in segments] == ["학교법인 동국", "제2조(명칭)"]
    assert "제1조(목적)이 법인은" in segments[0]


def test_raw_text_extras_dedupe_on_label_and_value():
    row = {
        **DEPARTMENT_TABLE_ROW,
        "document_key": "courses:design0",
        "raw_text": (
            "course_code: ISE2025\ntitle: 3D모델링\ncredit: 3\n이론: 3\n실습: 0\n설계: 0\n"
            "학점구성: 3\n교과과정영역: 선택필수\n설계: 0"
        ),
    }
    text = ingest.build_course_chunks(pd.DataFrame([row])).iloc[0]["chunk_text"]
    assert "실습시간: 0" in text
    assert "설계: 0" in text and text.count("설계: 0") == 1
    assert "학점구성: 3" in text
    # 짧은 값은 라벨이 다르면 유지한다.
    assert "교과과정영역: 선택필수" in text


def test_raw_text_long_value_repeating_description_is_not_duplicated():
    description = "제조 시스템의 3차원 형상 모델링 기법을 실습한다."
    row = {
        **DEPARTMENT_TABLE_ROW,
        "document_key": "courses:longdup",
        "description": description,
        "raw_text": f"title: 3D모델링\n교과목 해설: {description}",
    }
    text = ingest.build_course_chunks(pd.DataFrame([row])).iloc[0]["chunk_text"]
    assert text.count(description) == 1


def test_raw_text_url_or_timestamp_only_values_are_dropped():
    row = {
        **DEPARTMENT_TABLE_ROW,
        "document_key": "courses:urlts",
        "raw_text": (
            "title: 3D모델링\n설계: 1\n수집일시: 2026-01-01T00:00:00\n갱신시각: 2026-01-01 09:30:00+09:00\n"
            "상세URL: https://ise.dongguk.edu/page/100\n홈페이지: www.dongguk.edu/x\n"
            "비고 링크: 안내는 https://ise.dongguk.edu 참조"
        ),
    }
    chunks = ingest.build_course_chunks(pd.DataFrame([row]))
    text = chunks.iloc[0]["chunk_text"]
    assert "설계: 1" in text
    for dropped in ("수집일시", "갱신시각", "상세URL", "홈페이지", "2026-01-01"):
        assert dropped not in text, (dropped, text)
    # URL이 문장 일부인 값은 내용이므로 남긴다.
    assert "비고 링크: 안내는" in text
    _assert_no_bookkeeping(chunks)
