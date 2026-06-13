"""hybrid.py 핵심 로직 단위 테스트 (Chroma/임베딩 모델 없이 실행 가능).

실행: cd src/RAG && python -m pytest tests/ -q
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.search.hybrid import (  # noqa: E402
    _matches_where,
    hybrid_search,
    load_tfidf,
    load_tfidf_with_ids,
    train_tfidf,
)
import src.search.hybrid as hybrid  # noqa: E402


# ---------- _matches_where ----------

def _row(**kwargs) -> pd.Series:
    return pd.Series(kwargs)


def test_matches_where_eq():
    assert _matches_where(_row(major="통계학과"), {"major": {"$eq": "통계학과"}})
    assert not _matches_where(_row(major="컴퓨터공학과"), {"major": {"$eq": "통계학과"}})


def test_matches_where_missing_key():
    assert not _matches_where(_row(other="x"), {"major": {"$eq": "통계학과"}})


def test_matches_where_in_and_ne():
    assert _matches_where(_row(topics="장학"), {"topics": {"$in": ["장학", "학사"]}})
    assert not _matches_where(_row(topics="채용"), {"topics": {"$in": ["장학", "학사"]}})
    assert _matches_where(_row(topics="채용"), {"topics": {"$ne": "장학"}})


def test_matches_where_and_or():
    f = {"$and": [{"major": {"$eq": "통계학과"}}, {"topics": {"$eq": "장학"}}]}
    assert _matches_where(_row(major="통계학과", topics="장학"), f)
    assert not _matches_where(_row(major="통계학과", topics="채용"), f)
    f_or = {"$or": [{"major": {"$eq": "통계학과"}}, {"topics": {"$eq": "장학"}}]}
    assert _matches_where(_row(major="수학과", topics="장학"), f_or)


def test_matches_where_unknown_operator_is_conservative():
    assert not _matches_where(_row(major="통계학과"), {"major": {"$gt": "a"}})


# ---------- train_tfidf chunk_id 키잉 ----------

def test_train_tfidf_persists_chunk_ids(tmp_path):
    with patch("src.search.hybrid.VECTORIZER_DIR", tmp_path), \
         patch("src.search.hybrid._vectorizer_path", lambda ident: tmp_path / f"{ident}_tfidf.pkl"):
        corpus = ["장학금 신청 안내", "수강신청 일정 공지", "졸업 요건 변경"]
        ids = ["c1", "c2", "c3"]
        train_tfidf("testset", corpus, chunk_ids=ids)
        _, matrix, loaded_ids = load_tfidf_with_ids("testset")
        assert loaded_ids == ids
        assert matrix.shape[0] == 3


def test_train_tfidf_rejects_mismatched_ids(tmp_path):
    with patch("src.search.hybrid._vectorizer_path", lambda ident: tmp_path / f"{ident}_tfidf.pkl"):
        with pytest.raises(ValueError):
            train_tfidf("testset", ["a", "b"], chunk_ids=["only-one"])


# ---------- TF-IDF pkl 무결성 검증(매니페스트) ----------

def test_train_writes_manifest_and_load_verifies(tmp_path):
    """학습 시 매니페스트에 sha256이 기록되고, 정상 로드는 검증을 통과한다."""
    with patch("src.search.hybrid.VECTORIZER_DIR", tmp_path), \
         patch("src.search.hybrid.TFIDF_VERIFY_INTEGRITY", True):
        train_tfidf("intg", ["가나다", "라마바"], chunk_ids=["c1", "c2"])
        manifest = hybrid._read_manifest()
        assert "intg_tfidf.pkl" in manifest
        # 검증이 켜진 상태에서도 정상 아티팩트는 로드된다(예외 없음).
        vec, matrix = load_tfidf("intg")
        assert matrix.shape[0] == 2


def test_load_rejects_tampered_artifact(tmp_path):
    """매니페스트 해시와 다른(변조된) pkl은 fail-closed로 로드를 거부한다."""
    with patch("src.search.hybrid.VECTORIZER_DIR", tmp_path), \
         patch("src.search.hybrid.TFIDF_VERIFY_INTEGRITY", True):
        train_tfidf("intg", ["가나다", "라마바"], chunk_ids=["c1", "c2"])
        # 아티팩트 바이트를 변조 → 해시 불일치 유발
        pkl = tmp_path / "intg_tfidf.pkl"
        pkl.write_bytes(pkl.read_bytes() + b"\x00tampered")
        with pytest.raises(ValueError, match="무결성"):
            load_tfidf("intg")


def test_load_strict_mode_rejects_unmanifested(tmp_path):
    """TFIDF_REQUIRE_MANIFEST=1이면 매니페스트 미등록 아티팩트도 거부한다."""
    with patch("src.search.hybrid.VECTORIZER_DIR", tmp_path), \
         patch("src.search.hybrid.TFIDF_VERIFY_INTEGRITY", True), \
         patch("src.search.hybrid.TFIDF_REQUIRE_MANIFEST", True):
        train_tfidf("intg", ["가나다", "라마바"], chunk_ids=["c1", "c2"])
        # 매니페스트를 비워 미등록 상태로 만든다
        (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError):
            load_tfidf("intg")


# ---------- hybrid_search (Chroma/임베딩 모킹) ----------

def _fake_chroma(vec_ids, vec_dists):
    class FakeCollection:
        def query(self, **kwargs):
            return {"ids": [vec_ids], "distances": [vec_dists]}
    return FakeCollection()


def _make_dataset():
    chunks_df = pd.DataFrame(
        {
            "chunk_id": ["c1", "c2", "c3"],
            "chunk_text": ["장학금 신청 안내", "수강신청 일정 공지", "졸업 요건 변경"],
            "major": ["통계학과", "컴퓨터공학과", "통계학과"],
        }
    )
    from sklearn.feature_extraction.text import TfidfVectorizer

    vectorizer = TfidfVectorizer(max_features=100)
    matrix = vectorizer.fit_transform(chunks_df["chunk_text"].tolist())
    return chunks_df, vectorizer, matrix


def test_hybrid_search_maps_sparse_by_chunk_ids():
    chunks_df, vectorizer, matrix = _make_dataset()
    # chunks_df를 역순으로 섞어 행 순서 결합이 깨진 상황을 재현
    shuffled = chunks_df.iloc[::-1].reset_index(drop=True)
    with patch("src.search.hybrid.get_collection", return_value=_fake_chroma(["c1"], [0.4])), \
         patch("src.search.hybrid.encode_queries", return_value=[[0.0]]):
        result = hybrid_search(
            "fake", shuffled, vectorizer, matrix, "장학금 신청",
            top_k=3, alpha=0.5, tfidf_chunk_ids=["c1", "c2", "c3"],
        )
    # '장학금 신청'의 sparse 최고점은 학습 순서 기준 c1 — 섞인 df에서도 c1에 매핑돼야 함
    top = result.iloc[0]
    assert top["chunk_id"] == "c1"
    assert top["sparse_score"] > 0


def test_hybrid_search_skips_sparse_on_row_mismatch():
    chunks_df, vectorizer, matrix = _make_dataset()
    truncated = chunks_df.iloc[:2].reset_index(drop=True)  # 행 수 불일치 + 매핑 없음
    with patch("src.search.hybrid.get_collection", return_value=_fake_chroma(["c1"], [0.4])), \
         patch("src.search.hybrid.encode_queries", return_value=[[0.0]]):
        result = hybrid_search("fake", truncated, vectorizer, matrix, "장학금", top_k=3)
    # sparse는 건너뛰고 vector-only로 동작해야 함
    assert (result["sparse_score"] == 0).all()


def test_hybrid_search_where_filter_keeps_matching_sparse_hits():
    chunks_df, vectorizer, matrix = _make_dataset()
    where = {"major": {"$eq": "통계학과"}}
    # 벡터 검색은 아무것도 못 찾는 상황: 키워드-only 히트가 필터를 통과해 살아남아야 함
    with patch("src.search.hybrid.get_collection", return_value=_fake_chroma([], [])), \
         patch("src.search.hybrid.encode_queries", return_value=[[0.0]]):
        result = hybrid_search(
            "fake", chunks_df, vectorizer, matrix, "장학금 신청",
            top_k=3, where_filter=where, tfidf_chunk_ids=["c1", "c2", "c3"],
        )
    assert not result.empty
    assert set(result["chunk_id"]) <= {"c1", "c3"}  # 통계학과 문서만


def test_hybrid_search_where_filter_drops_non_matching_sparse_hits():
    chunks_df, vectorizer, matrix = _make_dataset()
    where = {"major": {"$eq": "물리학과"}}  # 아무 문서도 매칭 안 됨
    with patch("src.search.hybrid.get_collection", return_value=_fake_chroma([], [])), \
         patch("src.search.hybrid.encode_queries", return_value=[[0.0]]):
        result = hybrid_search(
            "fake", chunks_df, vectorizer, matrix, "장학금 신청",
            top_k=3, where_filter=where, tfidf_chunk_ids=["c1", "c2", "c3"],
        )
    assert result.empty


def test_hybrid_search_duplicate_chunk_ids_no_error():
    chunks_df, vectorizer, matrix = _make_dataset()
    dup = pd.concat([chunks_df, chunks_df.iloc[[0]]], ignore_index=True)  # c1 중복
    with patch("src.search.hybrid.get_collection", return_value=_fake_chroma(["c1"], [0.3])), \
         patch("src.search.hybrid.encode_queries", return_value=[[0.0]]):
        result = hybrid_search(
            "fake", dup, vectorizer, matrix, "장학금",
            top_k=3, tfidf_chunk_ids=["c1", "c2", "c3", "c1"],
        )
    assert (result["chunk_id"] == "c1").sum() == 1
