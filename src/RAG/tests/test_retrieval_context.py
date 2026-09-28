from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pipelines import ingest  # noqa: E402
from src.services.retrieval_context import (  # noqa: E402
    _academic_period,
    enrich_retrieval_fields,
)
from api import rag_service  # noqa: E402


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "chunk_id": "notice-1",
                "chunk_text": "신청 기간은 8월 3일부터 8월 7일까지입니다.",
                "title": "2026학년도 2학기 학부 수강 신청 안내",
                "published_at": "2026-07-22",
                "source": "notices",
            }
        ]
    )


def test_context_header_is_attached_to_every_retrieval_chunk():
    enriched = enrich_retrieval_fields(_frame())
    row = enriched.iloc[0]

    assert row["title_norm"] == "2026학년도2학기학부수강신청안내"
    assert row["audience"] == "undergraduate"
    assert "문서: 2026학년도 2학기 학부 수강 신청 안내" in row["retrieval_context"]
    assert "기준일: 2026-07-22" in row["retrieval_context"]
    assert "학사시기:" not in row["retrieval_context"]
    assert "대상: 학부" in row["retrieval_context"]
    assert row["retrieval_text"].endswith(row["chunk_text"])


def test_context_enrichment_is_idempotent():
    once = enrich_retrieval_fields(_frame())
    twice = enrich_retrieval_fields(once)
    assert twice.loc[0, "retrieval_text"] == once.loc[0, "retrieval_text"]
    assert twice.loc[0, "retrieval_text"].count("[문서:") == 1


@pytest.mark.parametrize(
    ("dataset", "builder", "source_row"),
    [
        (
            "notices", ingest.build_notice_chunks,
            {"제목": "수강신청 안내", "게시판": "학사공지", "게시일": "2026-07-22",
             "본문": "신청 기간을 확인하세요.", "상세URL": "https://example.test/notice",
             "첨부파일": [], "document_key": "notices:one"},
        ),
        (
            "rules", ingest.build_rule_chunks,
            {"title": "학칙", "filename": "학칙.hwp", "text": "제1조(목적) 학생의 수학을 정한다.",
             "source_url": "https://example.test/rule", "document_key": "rules:one"},
        ),
        (
            "schedule", ingest.build_schedule_chunks,
            {"title": "가을 학위수여식", "content": "참가 안내",
             "start_date": "2026-08-21", "end_date": "2026-08-21",
             "document_key": "schedule:one"},
        ),
        (
            "courses", ingest.build_course_chunks,
            {"title": "데이터 과학", "course_name": "데이터 과학", "course_code": "MIS2001",
             "description": "기초를 다룬다.", "document_key": "courses:one"},
        ),
        (
            "staff", ingest.build_staff_chunks,
            {"조직(트리)": "학사지원팀", "성명": "김**", "담당업무": "수강신청 운영",
             "document_key": "staff:one"},
        ),
        (
            "meals", ingest.build_meal_chunks,
            {"date": "2026-08-21", "weekday": "금", "restaurant": "상록원",
             "menu_text": "김치찌개, 쌀밥", "document_key": "meals:one"},
        ),
    ],
)
def test_each_dataset_indexes_title_once_after_rebuild_and_runtime_load(
    dataset, builder, source_row, monkeypatch, tmp_path,
):
    chunks = builder(pd.DataFrame([source_row]))
    assert not chunks.empty
    enriched = enrich_retrieval_fields(chunks)

    for original, indexed in zip(chunks.to_dict("records"), enriched.to_dict("records")):
        title = indexed["title"]
        assert indexed["retrieval_text"].count(title) == 1
        assert indexed["chunk_text"] == original["chunk_text"]
        for field in ("doc_id", "chunk_id", "url", "source"):
            assert indexed[field] == original[field]

    chunk_path = tmp_path / f"{dataset}.parquet"
    # A saved artifact may carry the old retrieval_text; both rebuild and
    # runtime loading must calculate from the unchanged source chunk again.
    enriched.assign(retrieval_text="stale indexed text").to_parquet(chunk_path)
    rebuilt = enrich_retrieval_fields(pd.read_parquet(chunk_path))
    assert rebuilt["retrieval_text"].tolist() == enriched["retrieval_text"].tolist()
    assert enrich_retrieval_fields(rebuilt)["retrieval_text"].tolist() == rebuilt["retrieval_text"].tolist()

    lexical_path = tmp_path / f"{dataset}.pkl"
    lexical_path.touch()
    monkeypatch.setitem(
        rag_service.DATASET_ARTIFACTS,
        dataset,
        ingest.DatasetArtifacts(dataset, "test", chunk_path),
    )
    monkeypatch.setattr(rag_service, "live_lexical_index_path", lambda _key: lexical_path)
    monkeypatch.setattr(rag_service, "read_lexical_metadata", lambda _key: {})
    monkeypatch.setattr(
        rag_service,
        "load_lexical_with_ids",
        lambda _key: (object(), object(), chunks["chunk_id"].tolist()),
    )
    monkeypatch.setattr(rag_service, "_datasets", {})
    runtime = rag_service._ensure_dataset_locked(dataset)[0]
    assert runtime["retrieval_text"].tolist() == rebuilt["retrieval_text"].tolist()
    assert runtime["chunk_text"].tolist() == chunks["chunk_text"].tolist()


def test_empty_notice_keeps_source_fallback_but_drops_search_only_title_label():
    chunks = ingest.build_notice_chunks(pd.DataFrame([{
        "제목": "본문 없음", "게시판": "학사공지", "게시일": "2026-06-19",
        "본문": "", "상세URL": "https://example.test/notice", "첨부파일": [],
        "document_key": "notices:empty",
    }]))
    source_text = chunks.loc[0, "chunk_text"]
    indexed = enrich_retrieval_fields(chunks).loc[0, "retrieval_text"]

    assert source_text.count("본문 없음") == 2
    assert chunks.loc[0, "chunk_text"] == source_text
    assert indexed.count("본문 없음") == 1
    assert "공지 제목:" not in indexed
    assert "본문이 비어 있어 상세 내용은 공지 링크를 확인하세요" in indexed


@pytest.mark.parametrize(
    ("chunk_text", "expected_body"),
    [
        ("[학칙]\n\n학칙시행세칙에 따른다.", "학칙시행세칙에 따른다."),
        ("학칙\n\n내용을 확인한다.", "내용을 확인한다."),
        ("공지 제목: 학칙\n\n내용을 확인한다.", "내용을 확인한다."),
        ("[학칙]\n\n일정: 학칙\n\n내용을 확인한다.", "내용을 확인한다."),
        ("[학칙]\n\n학칙을 준수한다.\n학칙", "학칙을 준수한다.\n학칙"),
        ("[학칙]\n\n내용을 확인한다.\n공지 제목: 학칙",
         "내용을 확인한다.\n공지 제목: 학칙"),
        ("[학칙]\n\n학칙시행세칙에 따른다. 학칙을 준수한다.",
         "학칙시행세칙에 따른다. 학칙을 준수한다."),
    ],
)
def test_only_leading_title_lines_are_removed_from_retrieval_body(
    chunk_text, expected_body,
):
    frame = pd.DataFrame([{"title": "학칙", "chunk_text": chunk_text}])
    once = enrich_retrieval_fields(frame)
    twice = enrich_retrieval_fields(once)

    assert once.loc[0, "retrieval_text"] == f"[문서: 학칙]\n\n{expected_body}"
    assert twice.loc[0, "retrieval_text"] == once.loc[0, "retrieval_text"]
    assert once.loc[0, "chunk_text"] == chunk_text


def test_period_already_in_title_is_not_repeated_in_header():
    frame = pd.DataFrame([{
        "title": "2026학년도 2학기",
        "chunk_text": "[2026학년도 2학기]\n\n수강 신청 기간을 확인하세요.",
    }])
    once = enrich_retrieval_fields(frame)
    twice = enrich_retrieval_fields(once)

    assert once.loc[0, "retrieval_text"].count("2026학년도 2학기") == 1
    assert "학사시기:" not in once.loc[0, "retrieval_context"]
    assert twice.loc[0, "retrieval_text"] == once.loc[0, "retrieval_text"]
    assert once.loc[0, "chunk_text"] == frame.loc[0, "chunk_text"]


@pytest.mark.parametrize(
    ("title", "chunk_text"),
    [
        ("MIS2001 교과목 안내", ""),
        ("CSE2025 교과목 안내", ""),
        ("교과목 안내", "ISE2025 과목 설명"),
        ("CSE2025학년도 교과목 안내", ""),
        ("CSE_2025 교과목 안내", ""),
        ("CSE_26-1 교과목 안내", ""),
        ("학번2026 안내", ""),
        ("2025학번 수강신청 안내", ""),
        ("CSE26-1 교과목 안내", ""),
        ("2025CSE 교과목 안내", ""),
        ("A1학기 교과목 안내", ""),
        ("규정 2-1-1", ""),
        ("규정 21-1-1", ""),
        ("규정 5-13-1", ""),
        ("2025-2026-1234 연락처", ""),
        ("학과 연락처", "02-2025-1234"),
        ("학과 연락처", "010-2025-1234"),
        ("학과 연락처", "02-26-1234"),
        ("학과 연락처", "02-2025-1234 / 010-2026-5678"),
    ],
)
def test_codes_and_phone_numbers_do_not_create_academic_period(title, chunk_text):
    row = enrich_retrieval_fields(
        pd.DataFrame([{"title": title, "chunk_text": chunk_text}])
    ).iloc[0]

    assert _academic_period(row) == ""
    assert "학사시기:" not in row["retrieval_context"]
    assert "학사시기:" not in row["retrieval_text"]


@pytest.mark.parametrize(
    ("title", "chunk_text", "period"),
    [
        ("2026학년도 수강 안내", "", "2026학년도"),
        ("수강 안내", "2026년 1학기 신청", "2026학년도 1학기"),
        ("수강 안내", "02-2025-1234, 2026년 1학기 신청", "2026학년도 1학기"),
        ("CSE_2025 교과목 안내, 2026년 1학기 개설", "", "2026학년도 1학기"),
        ("2026학년도1학기 수강 안내", "", "2026학년도 1학기"),
        ("2025학년도 제1학기 교양영어 레벨변경 안내", "", "2025학년도 1학기"),
        ("2025학년도 제2학기 영어 비교과 프로그램 안내", "", "2025학년도 2학기"),
        ("교양 제1학기 수강 안내", "", "1학기"),
        ("교양 제2학기 수강 안내", "", "2학기"),
        ("공지2026학년도 수강 안내", "", "2026학년도"),
        ("26-1학기 수강 안내", "", "2026학년도 1학기"),
        ("GROW코칭_26-1학기 모집 안내", "", "2026학년도 1학기"),
        ("26-1 수강 안내", "", "2026학년도 1학기"),
        ("26-1_수강신청 안내", "", "2026학년도 1학기"),
        ("26-1_English course registration", "", "2026학년도 1학기"),
        ("26.2 수강 안내", "", "2026학년도 2학기"),
        ("26/2 수강 안내", "", "2026학년도 2학기"),
        ("2026-2학기 수강 안내", "", "2026학년도 2학기"),
        ("2026-1 수강 안내", "", "2026학년도"),
        ("2025-2026학년도 학생증 안내", "", "2025학년도"),
        ("2026-2027 장학생 선발 안내", "", "2026학년도"),
        ("2024-25년 장학 프로그램 안내", "", "2024학년도"),
        ("2026 수강 안내", "", "2026학년도"),
        ("2026.2.15 시행", "", "2026학년도"),
        ("2024.12.2 시행", "", "2024학년도"),
        ("2025.03.10 접수", "", "2025학년도"),
        ("2025-03-10 접수", "", "2025학년도"),
        ("2025년 3월 접수", "", "2025학년도"),
        ("2학기 수강 안내", "", "2학기"),
    ],
)
def test_academic_period_forms_keep_existing_header(title, chunk_text, period):
    row = enrich_retrieval_fields(
        pd.DataFrame([{"title": title, "chunk_text": chunk_text}])
    ).iloc[0]

    assert _academic_period(row) == period
    if period in title:
        assert "학사시기:" not in row["retrieval_context"]
    else:
        assert f"학사시기: {period}" in row["retrieval_context"]


def test_artifact_only_persistence_trains_bm25_on_contextual_text(
    monkeypatch,
    tmp_path,
):
    captured: dict[str, object] = {}

    def fake_train(identifier, corpus, chunk_ids=None):
        captured["identifier"] = identifier
        captured["corpus"] = list(corpus)
        captured["chunk_ids"] = list(chunk_ids or [])
        return object(), np.empty((1, 0), dtype=np.float32)

    monkeypatch.setitem(
        ingest.DATASET_ARTIFACTS,
        "notices",
        ingest.DatasetArtifacts(
            key="notices",
            collection="test",
            chunk_path=tmp_path / "notices.parquet",
        ),
    )
    monkeypatch.setattr(ingest, "train_bm25", fake_train)

    persisted, _, _ = ingest.persist_dataset_artifacts_only(
        "notices",
        _frame(),
    )

    assert persisted.loc[0, "audience"] == "undergraduate"
    assert captured["chunk_ids"] == ["notice-1"]
    assert str(captured["corpus"][0]).startswith("[문서:")
    assert (tmp_path / "notices.parquet").exists()
