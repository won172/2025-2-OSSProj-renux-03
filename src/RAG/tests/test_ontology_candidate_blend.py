from __future__ import annotations

import asyncio
from datetime import date

import pandas as pd

import api.rag_service as rag_service
from src.services.ontology_retrieval import OntologyShadowResult
from src.utils.date_parser import QueryDateFilter


def _shadow_result(*document_keys: str) -> OntologyShadowResult:
    return OntologyShadowResult(
        linked_entities=(),
        traversed_relations=(),
        document_keys=tuple(document_keys),
        max_hops=2,
    )


def test_candidate_document_keys_are_route_scoped_allowlisted_and_bounded(monkeypatch):
    monkeypatch.setattr(rag_service.rag_config, "RAG_ONTOLOGY_CANDIDATES_ENABLED", True)
    monkeypatch.setattr(
        rag_service.rag_config,
        "RAG_ONTOLOGY_CANDIDATE_DATASETS",
        ("rules", "schedule", "notices"),
    )
    monkeypatch.setattr(
        rag_service.rag_config,
        "RAG_ONTOLOGY_CANDIDATE_DOCUMENTS_PER_DATASET",
        2,
    )

    grouped = rag_service._ontology_document_keys_by_dataset(
        _shadow_result(
            "rules:r1",
            "rules:r2",
            "rules:r3",
            "notices:n1",
            "schedule:s1",
            "courses:c1",
            "staff:p1",
        ),
        ["rules", "notices", "courses", "staff"],
    )

    assert grouped == {
        "rules": ("rules:r1", "rules:r2"),
        "notices": ("notices:n1",),
    }


def test_candidate_document_keys_are_empty_when_feature_is_disabled(monkeypatch):
    monkeypatch.setattr(rag_service.rag_config, "RAG_ONTOLOGY_CANDIDATES_ENABLED", False)

    assert rag_service._ontology_document_keys_by_dataset(
        _shadow_result("rules:r1"),
        ["rules"],
    ) == {}


def test_materialized_candidates_use_canonical_identity_filters_and_one_chunk_per_document():
    chunks = pd.DataFrame(
        [
            {
                "chunk_id": "r1-0",
                "document_key": "rules:r1",
                "doc_id": "legacy-r1",
                "title": "재수강 성적 처리 규정",
                "chunk_text": "재수강한 교과목의 성적 처리 기준",
                "position": 0,
                "entry_year": 2025,
                "effective_date": "2025-03-01",
            },
            {
                "chunk_id": "r1-1",
                "document_key": "rules:r1",
                "doc_id": "legacy-r1",
                "title": "재수강 성적 처리 규정",
                "chunk_text": "부칙",
                "position": 1,
                "entry_year": 2025,
                "effective_date": "2025-03-01",
            },
            {
                "chunk_id": "r2-0",
                "document_key": "rules:r2",
                "title": "졸업 요건",
                "chunk_text": "졸업 학점 안내",
                "position": 0,
                "entry_year": 2024,
                "effective_date": "2024-03-01",
            },
            {
                "chunk_id": "other-0",
                "document_key": "rules:other",
                "title": "무관한 규정",
                "chunk_text": "무관한 내용",
                "position": 0,
                "entry_year": 2025,
                "effective_date": "2025-03-01",
            },
        ]
    )

    candidates = rag_service._ontology_candidate_hits(
        chunks_df=chunks,
        dataset="rules",
        document_keys=("rules:r1", "rules:r2"),
        query="2025학번 재수강 성적 처리",
        where_filter={"entry_year": {"$eq": 2025}},
        date_filter=QueryDateFilter(
            start=date(2025, 1, 1),
            end=date(2025, 12, 31),
            label="2025",
            is_relative=False,
        ),
    )

    assert candidates["chunk_id"].tolist() == ["r1-0"]
    assert candidates.iloc[0]["ontology_document_key"] == "rules:r1"
    assert candidates.iloc[0]["ontology_document_rank"] == 1
    assert candidates.iloc[0]["ontology_match"] == 1
    assert candidates.iloc[0]["sparse_score"] > 0


def test_merge_marks_existing_chunk_and_appends_only_missing_candidate():
    hits = pd.DataFrame(
        [
            {
                "chunk_id": "existing",
                "dataset": "rules",
                "hybrid_score": 0.9,
            }
        ]
    )
    candidates = pd.DataFrame(
        [
            {
                "chunk_id": "existing",
                "dataset": "rules",
                "ontology_match": 1,
                "ontology_document_rank": 1,
                "ontology_document_key": "rules:r1",
            },
            {
                "chunk_id": "missing",
                "dataset": "rules",
                "ontology_match": 1,
                "ontology_document_rank": 2,
                "ontology_document_key": "rules:r2",
            },
        ]
    )

    merged = rag_service._merge_ontology_candidate_hits(hits, candidates)

    assert merged["chunk_id"].tolist() == ["existing", "missing"]
    existing = merged.loc[merged["chunk_id"] == "existing"].iloc[0]
    assert existing["ontology_match"] == 1
    assert existing["ontology_document_key"] == "rules:r1"


def test_merge_preserves_temporal_rank_on_an_existing_schedule_hit():
    hits = pd.DataFrame(
        [
            {
                "chunk_id": "future",
                "dataset": "schedule",
                "hybrid_score": 0.9,
            }
        ]
    )
    candidates = pd.DataFrame(
        [
            {
                "chunk_id": "future",
                "dataset": "schedule",
                "ontology_match": 1,
                "ontology_document_rank": 2,
                "ontology_document_key": "schedule:future",
                "ontology_temporal_rank": 131.0,
            }
        ]
    )

    merged = rag_service._merge_ontology_candidate_hits(hits, candidates)

    assert merged.iloc[0]["ontology_temporal_rank"] == 131.0


def test_balanced_shortlist_reserves_a_bounded_ontology_slot(monkeypatch):
    monkeypatch.setattr(
        rag_service.rag_config,
        "RAG_ONTOLOGY_CANDIDATE_DOCUMENTS_PER_DATASET",
        2,
    )
    monkeypatch.setattr(
        rag_service.rag_config,
        "RAG_ONTOLOGY_CANDIDATE_SLOTS_PER_DATASET",
        1,
    )
    frame = pd.DataFrame(
        [
            {
                "chunk_id": "semantic-best",
                "dataset": "rules",
                "title": "상위 의미 후보",
                "chunk_text": "상위 의미 후보",
                "hybrid_score": 0.95,
                "sparse_score": 0.10,
            },
            {
                "chunk_id": "lexical-best",
                "dataset": "rules",
                "title": "정확 어휘 후보",
                "chunk_text": "정확 어휘 후보",
                "hybrid_score": 0.85,
                "sparse_score": 0.90,
            },
            {
                "chunk_id": "ordinary-third",
                "dataset": "rules",
                "title": "일반 후보",
                "chunk_text": "일반 후보",
                "hybrid_score": 0.80,
                "sparse_score": 0.05,
            },
            {
                "chunk_id": "ontology-related",
                "dataset": "rules",
                "title": "관계 기반 후보",
                "chunk_text": "관계 기반 후보",
                "hybrid_score": 0.01,
                "sparse_score": 0.0,
                "ontology_match": 1,
                "ontology_document_rank": 1,
                "ontology_document_key": "rules:r-related",
            },
        ]
    )

    shortlist = rag_service._build_balanced_shortlist(
        [frame],
        per_dataset=3,
        max_candidates=3,
        query="재수강 기준",
    )

    assert shortlist["chunk_id"].tolist() == [
        "semantic-best",
        "lexical-best",
        "ontology-related",
    ]


def test_schedule_candidates_prefer_active_or_future_event_over_stale_event():
    chunks = pd.DataFrame(
        [
            {
                "chunk_id": "past",
                "document_key": "schedule:past",
                "title": "1학기 성적처리 공시 정정",
                "chunk_text": "1학기 성적 정정 기간",
                "schedule_start": "2026-06-24",
                "schedule_end": "2026-06-29",
            },
            {
                "chunk_id": "future",
                "document_key": "schedule:future",
                "title": "2학기 성적처리 공시 정정",
                "chunk_text": "2학기 성적 정정 기간",
                "schedule_start": "2026-12-23",
                "schedule_end": "2026-12-28",
            },
        ]
    )

    candidates = rag_service._ontology_candidate_hits(
        chunks_df=chunks,
        dataset="schedule",
        document_keys=("schedule:past", "schedule:future"),
        query="성적 정정 기간 아직 안 지났죠?",
        where_filter=None,
        date_filter=None,
        as_of=date(2026, 8, 13),
    )

    assert candidates["chunk_id"].tolist() == ["future", "past"]
    assert candidates.iloc[0]["ontology_temporal_rank"] < candidates.iloc[1][
        "ontology_temporal_rank"
    ]


def test_existing_best_schedule_relation_does_not_pull_second_stale_relation(monkeypatch):
    monkeypatch.setattr(
        rag_service.rag_config,
        "RAG_ONTOLOGY_CANDIDATE_SLOTS_PER_DATASET",
        1,
    )
    frame = pd.DataFrame(
        [
            {
                "chunk_id": "future-related",
                "dataset": "schedule",
                "title": "2학기 성적 정정",
                "chunk_text": "2학기 성적 정정 기간",
                "hybrid_score": 0.90,
                "sparse_score": 0.80,
                "ontology_match": 1,
                "ontology_document_rank": 2,
                "ontology_temporal_rank": 131,
            },
            {
                "chunk_id": "ordinary",
                "dataset": "schedule",
                "title": "2학기 성적 입력",
                "chunk_text": "2학기 성적 입력 기간",
                "hybrid_score": 0.85,
                "sparse_score": 0.70,
            },
            {
                "chunk_id": "another",
                "dataset": "schedule",
                "title": "수강 정정",
                "chunk_text": "수강 정정 기간",
                "hybrid_score": 0.80,
                "sparse_score": 0.60,
            },
            {
                "chunk_id": "past-related",
                "dataset": "schedule",
                "title": "1학기 성적 정정",
                "chunk_text": "지난 1학기 성적 정정 기간",
                "hybrid_score": 0.0,
                "sparse_score": 0.50,
                "ontology_match": 1,
                "ontology_document_rank": 1,
                "ontology_temporal_rank": 1_000_045,
            },
        ]
    )

    shortlist = rag_service._build_balanced_shortlist(
        [frame],
        per_dataset=3,
        max_candidates=3,
        query="성적 정정 기간 아직 안 지났죠?",
        as_of=date(2026, 8, 13),
    )

    assert "future-related" in shortlist["chunk_id"].tolist()
    assert "past-related" not in shortlist["chunk_id"].tolist()


def test_retrieval_applies_audience_and_campus_boundaries_to_ontology_candidates(monkeypatch):
    chunks = pd.DataFrame(
        [
            {
                "chunk_id": "base",
                "document_key": "notices:base",
                "title": "학부 수강 공지",
                "chunk_text": "학부 공지",
                "audience": "undergraduate",
                "campus": "서울",
            },
            {
                "chunk_id": "graduate",
                "document_key": "notices:graduate",
                "title": "대학원 수강 공지",
                "chunk_text": "대학원 공지",
                "audience": "graduate",
                "campus": "서울",
            },
            {
                "chunk_id": "wise",
                "document_key": "notices:wise",
                "title": "WISE 수강 공지",
                "chunk_text": "WISE 공지",
                "audience": "undergraduate",
                "campus": "WISE",
            },
        ]
    )
    monkeypatch.setattr(
        rag_service,
        "_ensure_dataset",
        lambda dataset: (chunks, object(), object(), None),
    )
    monkeypatch.setattr(
        rag_service,
        "hybrid_search_with_meta",
        lambda **kwargs: pd.DataFrame(
            [
                {
                    **chunks.iloc[0].to_dict(),
                    "vector_score": 0.9,
                    "sparse_score": 0.8,
                    "hybrid_score": 0.85,
                }
            ]
        ),
    )

    frames, _, _ = asyncio.run(
        rag_service._retrieve_frames(
            route=["notices"],
            query="학부 수강 공지 알려줘",
            final_where_filter={},
            notice_board_filter=None,
            date_filter=None,
            entry_year=None,
            request_id="ontology-boundary-test",
            allow_wise=False,
            ontology_document_keys_by_dataset={
                "notices": ("notices:graduate", "notices:wise"),
            },
        )
    )

    assert len(frames) == 1
    assert frames[0]["chunk_id"].tolist() == ["base"]
