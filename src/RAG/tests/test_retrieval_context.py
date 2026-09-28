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
    assert "학사시기: 2026학년도 2학기" in row["retrieval_context"]
    assert "대상: 학부" in row["retrieval_context"]
    assert row["retrieval_text"].endswith(row["chunk_text"])


def test_context_enrichment_is_idempotent():
    once = enrich_retrieval_fields(_frame())
    twice = enrich_retrieval_fields(once)
    assert twice.loc[0, "retrieval_text"] == once.loc[0, "retrieval_text"]
    assert twice.loc[0, "retrieval_text"].count("[문서:") == 1


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
