from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import rag_service  # noqa: E402
from src.pipelines.ingest import (  # noqa: E402
    _canonicalize_campus_scope_frame,
    build_rule_chunks,
    reindex_from_db,
)
from src.services.campus_scope import (  # noqa: E402
    CampusScope,
    apply_campus_safety_boundary,
    classify_campus_scope,
    query_explicitly_requests_wise,
)


def test_classifier_uses_canonical_scopes_and_conservative_evidence():
    assert classify_campus_scope({"title": "서울캠퍼스 학사 안내"}) is CampusScope.SEOUL
    assert classify_campus_scope({"title": "바이오메디캠퍼스 시설 안내"}) is CampusScope.BMC
    assert classify_campus_scope({"title": "서울캠퍼스 및 WISE캠퍼스 공통 규정"}) is CampusScope.SHARED
    assert classify_campus_scope({"title": "대학 공통 안내"}) is CampusScope.UNKNOWN

    # A corrupt/legacy explicit value cannot hide strong WISE evidence.
    assert classify_campus_scope({
        "campus_scope": "unknown",
        "relative_dir": "규정/WISE캠퍼스/학사",
    }) is CampusScope.WISE
    assert classify_campus_scope({
        "campus_scope": "seoul",
        "url": "https://wise.dongguk.ac.kr/rules/1",
    }) is CampusScope.WISE
    assert classify_campus_scope({
        "campus_scope": "shared",
        "filename": "WISE캠퍼스 학사구조개편 규정.hwp",
    }) is CampusScope.WISE


def test_real_wise_4_0_49_identity_outranks_weak_shared_body_phrase():
    real_legacy_row = {
        "title": "[4-0-49] WISE캠퍼스 체육지도위원회 규정 - 2024.05.01..hwp",
        "filename": "[4-0-49] WISE캠퍼스 체육지도위원회 규정 - 2024.05.01..hwp",
        "relative_dir": "제4편_위원회",
        "chunk_text": (
            "운동부 신설과 폐지에 대한 사항"
            "(최종결정은 양 캠퍼스 체육지도위원회가 공동 심의)"
        ),
    }
    assert classify_campus_scope(real_legacy_row) is CampusScope.WISE
    filtered, blocked = apply_campus_safety_boundary(
        pd.DataFrame([real_legacy_row]),
        allow_wise=False,
    )
    assert blocked == 1
    assert filtered.empty


def test_default_boundary_blocks_all_twelve_adversarial_wise_rows():
    wise_rows = [
        {"chunk_id": "w1", "campus_scope": "wise"},
        {"chunk_id": "w2", "campus_scope": "unknown", "title": "WISE캠퍼스 규정"},
        {"chunk_id": "w3", "campus_scope": "seoul", "filename": "WISE캠퍼스 학칙.hwp"},
        {"chunk_id": "w4", "campus_scope": "shared", "relative_dir": "제3편/WISE캠퍼스"},
        {"chunk_id": "w5", "url": "https://wise.dongguk.ac.kr/notice/5"},
        {"chunk_id": "w6", "source_url": "https://wise.dongguk.ac.kr/rule/6"},
        {"chunk_id": "w7", "source_file": "WISE캠퍼스_교원인사.hwp"},
        {"chunk_id": "w8", "raw_path": "/archive/WISE캠퍼스/rule-8.html"},
        {"chunk_id": "w9", "normalized_path": "/normalized/wise/rule-9.json"},
        {"chunk_id": "w10", "chunk_text": "동국대학교 WISE캠퍼스 학생에게만 적용한다."},
        {"chunk_id": "w11", "topics": "WISE 캠퍼스 학사"},
        {"chunk_id": "w12", "department": "WISE캠퍼스 학사지원서비스팀"},
    ]
    safe_row = {
        "chunk_id": "s1",
        "campus_scope": "unknown",
        "title": "서울 재학생 장학 안내",
        "chunk_text": "장학 신청 서류 안내",
        "dataset": "notices",
        "source": "notices",
        "evidence_group": 1,
        "citation_number": 1,
    }
    filtered, blocked = apply_campus_safety_boundary(
        pd.DataFrame([*wise_rows, safe_row]),
        allow_wise=False,
    )

    assert blocked == 12
    assert filtered["chunk_id"].tolist() == ["s1"]
    assert set(filtered["campus_scope"]) == {"shared"}
    context = rag_service._build_selected_evidence_context(filtered)
    assert "WISE" not in context
    assert "장학 신청 서류" in context

    untrusted, _ = apply_campus_safety_boundary(
        pd.DataFrame([{"chunk_id": "legacy", "title": "출처 미상 안내"}]),
        allow_wise=False,
    )
    assert untrusted.iloc[0]["campus_scope"] == "unknown"


def test_wise_rows_are_detected_but_remain_blocked_for_product_retrieval():
    rows = pd.DataFrame([
        {"chunk_id": "w1", "title": "WISE캠퍼스 학사 규정"},
        {"chunk_id": "s1", "title": "서울캠퍼스 학사 규정"},
    ])
    assert query_explicitly_requests_wise("WISE캠퍼스 휴학 규정 알려줘") is True
    assert query_explicitly_requests_wise("경주캠퍼스 휴학 규정 알려줘") is True
    assert query_explicitly_requests_wise("와이즈캠퍼스 휴학 규정 알려줘") is True
    assert query_explicitly_requests_wise("와이즈 캠퍼스 휴학 규정 알려줘") is True
    assert query_explicitly_requests_wise("휴학 규정 알려줘") is False
    assert query_explicitly_requests_wise("otherwise라는 단어 뜻") is False

    filtered, blocked = apply_campus_safety_boundary(rows, allow_wise=False)
    assert blocked == 1
    assert filtered["chunk_id"].tolist() == ["s1"]
    assert rag_service._semantic_cache_namespace(None, allow_wise=False).endswith(
        "seoul_bmc"
    )


def test_new_rule_ingest_assigns_campus_scope_before_indexing():
    chunks = build_rule_chunks(pd.DataFrame([
        {
            "filename": "WISE캠퍼스 학사구조개편 대상 학과 규정.hwp",
            "relative_dir": "제3편/학사행정",
            "text": "소속 변경에 관한 규정",
        },
        {
            "filename": "서울캠퍼스 장학 규정.hwp",
            "relative_dir": "제3편/학사행정",
            "text": "장학금 지급에 관한 규정",
        },
    ]))

    assert set(chunks["campus_scope"]) == {"wise", "seoul"}


def test_db_reindex_frame_and_legacy_parquet_fallback_get_canonical_scope():
    legacy = pd.DataFrame([{
        "chunk_id": "legacy-wise",
        "title": "[4-0-49] WISE캠퍼스 체육지도위원회 규정 - 2024.05.01..hwp",
        "filename": "[4-0-49] WISE캠퍼스 체육지도위원회 규정 - 2024.05.01..hwp",
        "relative_dir": "제4편_위원회",
        "chunk_text": "양 캠퍼스 체육지도위원회가 공동 심의한다.",
    }])
    canonical = _canonicalize_campus_scope_frame(legacy)
    assert canonical.loc[0, "campus_scope"] == "wise"

    # Older parquet rows may still lack the new column; runtime fallback must
    # classify and block them without requiring a bulk rebuild.
    filtered, blocked = apply_campus_safety_boundary(
        canonical.drop(columns=["campus_scope"]),
        allow_wise=False,
    )
    assert blocked == 1
    assert filtered.empty
    assert inspect.getsource(reindex_from_db).count("_canonicalize_campus_scope_frame") >= 5


def test_parent_neighbor_expansion_reapplies_boundary_without_wise_leak(monkeypatch):
    cached = pd.DataFrame([
        {
            "chunk_id": "safe-0",
            "doc_id": "mixed-document",
            "position": 0,
            "title": "서울캠퍼스 장학 안내",
            "chunk_text": "[서울 장학 안내]\n\n서울 학생의 장학 신청 서류입니다.",
            "source": "rules",
        },
        {
            "chunk_id": "wise-1",
            "doc_id": "mixed-document",
            "position": 1,
            "title": "WISE캠퍼스 전용 규정",
            "chunk_text": "[WISE 전용]\n\nWISE_ONLY_SECRET 규정 내용",
            "source": "rules",
        },
    ])
    cache = rag_service.DatasetCache(
        chunks=cached,
        vectorizer=object(),
        matrix=object(),
        chunk_path=Path("unused.parquet"),
        chunk_mtime=0,
        tfidf_mtime=0,
    )
    monkeypatch.setitem(rag_service._datasets, "rules", cache)
    monkeypatch.setattr(rag_service, "PARENT_CONTEXT_ENABLED", True)

    selected, blocked = apply_campus_safety_boundary(
        cached.iloc[[0]].assign(dataset="rules", evidence_group=1, citation_number=1),
        allow_wise=False,
    )
    assert blocked == 0
    context = rag_service._build_selected_evidence_context(selected)
    source_metadata = rag_service._source_metadata(selected.iloc[0])

    assert "서울 학생의 장학 신청 서류" in context
    assert "WISE_ONLY_SECRET" not in context
    assert "WISE" not in str(source_metadata)
