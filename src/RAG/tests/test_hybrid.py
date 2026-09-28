"""hybrid.py 핵심 로직 단위 테스트 (Chroma/임베딩 모델 없이 실행 가능).

실행: cd src/RAG && python -m pytest tests/ -q
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import chromadb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.search.hybrid import (  # noqa: E402
    BM25LexicalIndex,
    _academic_period_title_adjustment,
    _matches_where,
    build_tfidf_vectorizer,
    hybrid_search,
    load_lexical,
    load_lexical_with_ids,
    read_lexical_metadata,
    train_bm25,
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


# ---------- BM25 chunk_id 키잉 ----------

def test_train_bm25_persists_chunk_ids_and_metadata(tmp_path):
    with patch("src.search.hybrid.VECTORIZER_DIR", tmp_path):
        corpus = ["장학금 신청 안내", "수강신청 일정 공지", "졸업 요건 변경"]
        ids = ["c1", "c2", "c3"]
        train_bm25("testset", corpus, chunk_ids=ids)
        index, matrix, loaded_ids = load_lexical_with_ids("testset")
        assert loaded_ids == ids
        assert matrix.shape[0] == 3
        assert isinstance(index, BM25LexicalIndex)
        assert read_lexical_metadata("testset")["retriever_type"] == "bm25"
        assert (tmp_path / "testset_bm25.pkl").exists()


def test_korean_tfidf_tokenizer_handles_particle_and_spacing_variants():
    with patch("src.search.hybrid.TFIDF_TOKENIZER", "korean"):
        vectorizer = build_tfidf_vectorizer()
        matrix = vectorizer.fit_transform(["졸업 요건 변경 안내", "장학금 신청 안내"])

        scores = (vectorizer.transform(["졸업요건은"]) @ matrix.T).toarray().ravel()

    assert scores[0] > 0
    assert scores[0] > scores[1]


def test_korean_tfidf_tokenizer_exposes_three_syllable_compound_stem():
    with patch("src.search.hybrid.TFIDF_TOKENIZER", "korean"):
        vectorizer = build_tfidf_vectorizer()
        matrix = vectorizer.fit_transform(["개강 학기개시일", "휴학 복학 신청"])

        scores = (vectorizer.transform(["개강일이 언제야"]) @ matrix.T).toarray().ravel()

    assert scores[0] > 0
    assert scores[0] > scores[1]


def test_train_bm25_rejects_mismatched_ids(tmp_path):
    with patch("src.search.hybrid.VECTORIZER_DIR", tmp_path):
        with pytest.raises(ValueError):
            train_bm25("testset", ["a", "b"], chunk_ids=["only-one"])


# ---------- BM25 pkl 무결성 검증(매니페스트) ----------

def test_train_writes_manifest_and_load_verifies(tmp_path):
    """학습 시 매니페스트에 sha256이 기록되고, 정상 로드는 검증을 통과한다."""
    with patch("src.search.hybrid.VECTORIZER_DIR", tmp_path), \
         patch("src.search.hybrid.TFIDF_VERIFY_INTEGRITY", True):
        train_bm25("intg", ["가나다 안내", "라마바 공지"], chunk_ids=["c1", "c2"])
        manifest = hybrid._read_manifest()
        assert "intg_bm25.pkl" in manifest
        # 검증이 켜진 상태에서도 정상 아티팩트는 로드된다(예외 없음).
        vec, matrix = load_lexical("intg")
        assert matrix.shape[0] == 2


def test_load_rejects_tampered_artifact(tmp_path):
    """매니페스트 해시와 다른(변조된) pkl은 fail-closed로 로드를 거부한다."""
    with patch("src.search.hybrid.VECTORIZER_DIR", tmp_path), \
         patch("src.search.hybrid.TFIDF_VERIFY_INTEGRITY", True):
        train_bm25("intg", ["가나다 안내", "라마바 공지"], chunk_ids=["c1", "c2"])
        # 아티팩트 바이트를 변조 → 해시 불일치 유발
        pkl = tmp_path / "intg_bm25.pkl"
        pkl.write_bytes(pkl.read_bytes() + b"\x00tampered")
        with pytest.raises(ValueError, match="무결성"):
            load_lexical("intg")


def test_load_strict_mode_rejects_unmanifested(tmp_path):
    """TFIDF_REQUIRE_MANIFEST=1이면 매니페스트 미등록 아티팩트도 거부한다."""
    with patch("src.search.hybrid.VECTORIZER_DIR", tmp_path), \
         patch("src.search.hybrid.TFIDF_VERIFY_INTEGRITY", True), \
         patch("src.search.hybrid.TFIDF_REQUIRE_MANIFEST", True):
        train_bm25("intg", ["가나다 안내", "라마바 공지"], chunk_ids=["c1", "c2"])
        # 매니페스트를 비워 미등록 상태로 만든다
        (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError):
            load_lexical("intg")


def test_rebuild_does_not_expose_new_artifact_with_old_manifest(tmp_path, monkeypatch):
    """동시 로드는 pkl/매니페스트 교체가 모두 끝난 뒤의 세대만 읽는다."""
    with patch("src.search.hybrid.VECTORIZER_DIR", tmp_path), \
         patch("src.search.hybrid.TFIDF_VERIFY_INTEGRITY", True):
        train_bm25("intg", ["이전 문서"], chunk_ids=["old"])
        original_write = hybrid._write_manifest_unlocked
        manifest_write_entered = threading.Event()
        allow_manifest_write = threading.Event()

        def delayed_manifest_write(manifest):
            manifest_write_entered.set()
            assert allow_manifest_write.wait(timeout=5)
            original_write(manifest)

        monkeypatch.setattr(hybrid, "_write_manifest_unlocked", delayed_manifest_write)
        writer = threading.Thread(
            target=train_bm25,
            args=("intg", ["새 문서"]),
            kwargs={"chunk_ids": ["new"]},
        )
        writer.start()
        assert manifest_write_entered.wait(timeout=5)

        loaded: list[list[str] | None] = []

        def load_during_publish():
            _, _, chunk_ids = load_lexical_with_ids("intg")
            loaded.append(chunk_ids)

        reader = threading.Thread(target=load_during_publish)
        reader.start()
        reader.join(timeout=0.1)
        assert reader.is_alive(), "reader must wait while artifact generation is being published"

        allow_manifest_write.set()
        writer.join(timeout=5)
        reader.join(timeout=5)
        assert not writer.is_alive()
        assert not reader.is_alive()
        assert loaded == [["new"]]


def test_failed_rebuild_keeps_last_verified_artifact(tmp_path, monkeypatch):
    """직렬화 실패는 기존 정상 파일을 건드리지 않고 임시 파일도 정리한다."""
    with patch("src.search.hybrid.VECTORIZER_DIR", tmp_path), \
         patch("src.search.hybrid.TFIDF_VERIFY_INTEGRITY", True):
        train_bm25("intg", ["정상 문서"], chunk_ids=["stable"])
        original_digest = hybrid._sha256_file(tmp_path / "intg_bm25.pkl")

        def fail_dump(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(hybrid.joblib, "dump", fail_dump)
        with pytest.raises(OSError, match="disk full"):
            train_bm25("intg", ["미완성 문서"], chunk_ids=["broken"])

        assert hybrid._sha256_file(tmp_path / "intg_bm25.pkl") == original_digest
        assert not list(tmp_path.glob(".intg_bm25.pkl.*.tmp"))
        _, _, chunk_ids = load_lexical_with_ids("intg")
        assert chunk_ids == ["stable"]


def test_bm25_normalizes_for_document_length(tmp_path):
    corpus = [
        "장학금 신청",
        "장학금 신청 " + " ".join(f"무관단어{index}" for index in range(80)),
        "수강신청 일정",
        "졸업 요건",
        "도서관 운영시간",
        "기숙사 입사",
    ]
    with patch("src.search.hybrid.VECTORIZER_DIR", tmp_path):
        index, _ = train_bm25("length", corpus)

    scores = index.score("장학금 신청")
    assert scores[0] > scores[1] > 0


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


def _trace_dataset():
    chunks_df, vectorizer, matrix = _make_dataset()
    chunks_df["document_key"] = ["notices:1", "notices:2", "notices:3"]
    chunks_df["corpus_revision"] = "revision-1"
    return chunks_df, vectorizer, matrix


def _ranking_bytes(hits):
    return json.dumps(
        hits[["chunk_id", "hybrid_score", "vector_score", "sparse_score"]].to_dict("records"),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _assert_ranking_close(hits, expected_json):
    actual = json.loads(_ranking_bytes(hits))
    expected = json.loads(expected_json)
    assert len(actual) == len(expected)
    for actual_hit, expected_hit in zip(actual, expected):
        assert list(actual_hit) == list(expected_hit)
        for key, expected_value in expected_hit.items():
            if key in {"hybrid_score", "vector_score", "sparse_score"}:
                assert actual_hit[key] == pytest.approx(expected_value, rel=1e-9, abs=1e-12)
            else:
                assert actual_hit[key] == expected_value


def test_hybrid_search_trace_keeps_head_results_and_exposes_ranks():
    chunks_df, vectorizer, matrix = _trace_dataset()

    class Collection:
        metadata = {"hnsw:space": "cosine"}

        def query(self, **_kwargs):
            return {"ids": [["c2", "c1"]], "distances": [[0.1, 0.4]]}

    with patch("src.search.hybrid.get_collection", return_value=Collection()), \
         patch("src.search.hybrid.encode_queries", return_value=[[0.0]]):
        hits = hybrid.hybrid_search_with_meta(
            "dongguk_notices", chunks_df, vectorizer, matrix, "장학금 신청",
            top_k=3, tfidf_chunk_ids=["c1", "c2", "c3"],
        )

    # Fixed from origin/main a0729f9 before the trace change: IDs and order stay exact.
    _assert_ranking_close(hits, (
        '[{"chunk_id":"c1","hybrid_score":1.171935483870968,'
        '"vector_score":0.6,"sparse_score":0.8164965809277261},'
        '{"chunk_id":"c2","hybrid_score":0.62,'
        '"vector_score":0.9,"sparse_score":0.0}]'
    ))
    assert hits.attrs == {"retrieval_mode": "hybrid", "dense_error_type": None}
    assert hits["document_key"].tolist() == ["notices:1", "notices:2"]
    assert hits["corpus_revision"].tolist() == ["revision-1", "revision-1"]
    assert hits["dense_rank"].tolist() == [2, 1]
    assert hits["sparse_rank"].tolist() == [1, None]
    assert hits["fusion_rank"].tolist() == [1, 2]
    assert hits["dense_distance_raw"].tolist() == pytest.approx([0.4, 0.1], rel=1e-9, abs=1e-12)
    assert hits["dense_similarity_raw"].tolist() == pytest.approx([0.6, 0.9], rel=1e-9, abs=1e-12)
    assert hits["sparse_score_raw"].tolist() == pytest.approx([0.8164965809277261, 0.0], rel=1e-9, abs=1e-12)


def test_chroma_failure_trace_keeps_head_sparse_results():
    chunks_df, vectorizer, matrix = _trace_dataset()

    class BrokenCollection:
        def query(self, **_kwargs):
            raise chromadb.errors.InternalError("private failure detail")

    with patch("src.search.hybrid.get_collection", return_value=BrokenCollection()), \
         patch("src.search.hybrid.encode_queries", return_value=[[0.0]]):
        hits = hybrid.hybrid_search_with_meta(
            "dongguk_notices", chunks_df, vectorizer, matrix, "장학금 신청",
            top_k=3, tfidf_chunk_ids=["c1", "c2", "c3"],
        )

    _assert_ranking_close(hits, (
        '[{"chunk_id":"c1","hybrid_score":0.6799999999999999,'
        '"vector_score":0.0,"sparse_score":0.8164965809277261}]'
    ))
    assert hits.attrs == {
        "retrieval_mode": "sparse_degraded", "dense_error_type": "InternalError",
    }
    assert "private failure detail" not in repr(hits.attrs)
    assert hits.loc[0, "dense_rank"] is None
    assert hits.loc[0, "sparse_rank"] == 1
    assert hits.loc[0, "fusion_rank"] == 1
    assert hits.loc[0, "dense_distance_raw"] is None
    assert hits.loc[0, "dense_similarity_raw"] is None


def test_intentionally_disabled_dense_reports_sparse_only_without_chroma_call():
    chunks_df, vectorizer, matrix = _trace_dataset()
    with patch("src.search.hybrid.get_collection") as get_collection:
        hits = hybrid.hybrid_search_with_meta(
            "dongguk_notices", chunks_df, vectorizer, matrix, "장학금 신청",
            top_k=3, tfidf_chunk_ids=["c1", "c2", "c3"], dense_enabled=False,
        )

    get_collection.assert_not_called()
    _assert_ranking_close(hits, (
        '[{"chunk_id":"c1","hybrid_score":0.6799999999999999,'
        '"vector_score":0.0,"sparse_score":0.8164965809277261}]'
    ))
    assert hits.attrs == {"retrieval_mode": "sparse_only", "dense_error_type": None}
    assert hits.loc[0, "dense_rank"] is None
    assert hits.loc[0, "sparse_rank"] == 1
    assert hits.loc[0, "fusion_rank"] == 1


def test_bm25_trace_retains_pre_normalization_score():
    class Engine:
        def get_scores(self, _tokens):
            return [4.0, 2.0]

    chunks_df = pd.DataFrame({
        "chunk_id": ["c1", "c2"], "chunk_text": ["첫째", "둘째"],
    })
    vectorizer = BM25LexicalIndex(Engine(), "default", 2)
    with patch("src.search.hybrid.get_collection", return_value=_fake_chroma([], [])), \
         patch("src.search.hybrid.encode_queries", return_value=[[0.0]]):
        hits = hybrid_search(
            "fake", chunks_df, vectorizer, pd.DataFrame(index=range(2)), "질의",
            top_k=2, tfidf_chunk_ids=["c1", "c2"],
        )

    assert hits["sparse_score"].tolist() == [1.0, 0.5]
    assert hits["sparse_score_raw"].tolist() == [4.0, 2.0]
    assert hits["sparse_rank"].tolist() == [1, 2]
    assert hits["document_key"].tolist() == [None, None]
    assert hits["corpus_revision"].tolist() == [None, None]


def test_dense_hit_retains_sparse_raw_score_below_candidate_cutoff():
    class Engine:
        def get_scores(self, _tokens):
            return [6.0, 5.0, 4.0, 3.0, 2.0, 1.0]

    chunks_df = pd.DataFrame({
        "chunk_id": [f"c{index}" for index in range(1, 7)],
        "chunk_text": ["본문"] * 6,
        "major": ["excluded"] * 5 + ["allowed"],
    })
    vectorizer = BM25LexicalIndex(Engine(), "default", 6)
    with patch("src.search.hybrid.get_collection", return_value=_fake_chroma(["c6"], [0.0])), \
         patch("src.search.hybrid.encode_queries", return_value=[[0.0]]), \
         patch("src.search.hybrid.HYBRID_FUSION_MODE", "weighted"), \
         patch("src.search.hybrid.HYBRID_TITLE_FOCUS_WEIGHT", 0.0):
        hits = hybrid_search(
            "fake", chunks_df, vectorizer, pd.DataFrame(index=range(6)), "질의",
            top_k=1, alpha=0.7, where_filter={"major": {"$eq": "allowed"}},
            tfidf_chunk_ids=[f"c{index}" for index in range(1, 7)],
        )

    # Fixed from origin/main a0729f9: sparse top-5 excludes c6; filtered candidates leave c6.
    assert _ranking_bytes(hits) == (
        '[{"chunk_id":"c6","hybrid_score":0.7,"vector_score":1.0,"sparse_score":0.0}]'
    )
    assert hits.loc[0, "dense_rank"] == 1
    assert hits.loc[0, "sparse_rank"] is None
    assert hits.loc[0, "sparse_score_raw"] == 1.0


def test_duplicate_chunk_id_raw_sparse_score_matches_scoring_row():
    class Engine:
        def get_scores(self, _tokens):
            return [1.0, 4.0]

    chunks_df = pd.DataFrame({
        "chunk_id": ["c1", "c1"], "chunk_text": ["첫 행", "둘째 행"],
    })
    vectorizer = BM25LexicalIndex(Engine(), "default", 2)
    with patch("src.search.hybrid.get_collection", return_value=_fake_chroma([], [])), \
         patch("src.search.hybrid.encode_queries", return_value=[[0.0]]), \
         patch("src.search.hybrid.HYBRID_FUSION_MODE", "weighted"), \
         patch("src.search.hybrid.HYBRID_TITLE_FOCUS_WEIGHT", 0.0):
        hits = hybrid_search(
            "fake", chunks_df, vectorizer, pd.DataFrame(index=range(2)), "질의",
            top_k=1, tfidf_chunk_ids=["c1", "c1"],
        )

    # Fixed from origin/main a0729f9: descending candidates overwrite c1 with row 0.
    assert _ranking_bytes(hits) == (
        '[{"chunk_id":"c1","hybrid_score":0.25,'
        '"vector_score":0.0,"sparse_score":0.25}]'
    )
    assert hits.loc[0, "chunk_text"] == "첫 행"
    assert hits.loc[0, "sparse_score_raw"] == 1.0


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


def test_hybrid_search_uses_bm25_sparse_ranking(tmp_path):
    chunks_df = pd.DataFrame(
        {
            "chunk_id": ["c1", "c2", "c3", "c4"],
            "chunk_text": [
                "장학금 신청 안내",
                "수강신청 일정",
                "졸업 요건",
                "도서관 운영시간",
            ],
        }
    )
    with patch("src.search.hybrid.VECTORIZER_DIR", tmp_path):
        index, matrix = train_bm25(
            "bm25-search",
            chunks_df["chunk_text"],
            chunk_ids=chunks_df["chunk_id"],
        )
    with patch(
        "src.search.hybrid.get_collection",
        return_value=_fake_chroma([], []),
    ), patch("src.search.hybrid.encode_queries", return_value=[[0.0]]):
        result = hybrid_search(
            "fake",
            chunks_df,
            index,
            matrix,
            "장학금 신청",
            top_k=3,
            tfidf_chunk_ids=["c1", "c2", "c3", "c4"],
        )

    assert result.iloc[0]["chunk_id"] == "c1"
    assert result.iloc[0]["sparse_score"] == pytest.approx(1.0)


def test_hybrid_search_falls_back_to_sparse_when_chroma_segment_is_broken():
    chunks_df, vectorizer, matrix = _make_dataset()

    class BrokenCollection:
        metadata = {"hnsw:space": "cosine"}

        def query(self, **_kwargs):
            raise chromadb.errors.InternalError("Nothing found on disk")

    with patch("src.search.hybrid.get_collection", return_value=BrokenCollection()), \
         patch("src.search.hybrid.encode_queries", return_value=[[0.0]]):
        result = hybrid_search(
            "dongguk_notices",
            chunks_df,
            vectorizer,
            matrix,
            "장학금 신청",
            top_k=3,
            tfidf_chunk_ids=["c1", "c2", "c3"],
        )

    assert not result.empty
    assert result.iloc[0]["chunk_id"] == "c1"
    assert (result["vector_score"] == 0).all()


def test_exact_sparse_match_is_not_buried_by_dense_related_results():
    chunks_df = pd.DataFrame(
        {
            "chunk_id": ["exact", "dense-1", "dense-2"],
            "chunk_text": [
                "2026학년도 2학기 개강 학기개시일 9월 1일",
                "2026학년도 2학기 휴학 복학 신청 일정",
                "2026학년도 학사 일정 안내",
            ],
        }
    )
    from sklearn.feature_extraction.text import TfidfVectorizer

    vectorizer = TfidfVectorizer(max_features=100)
    matrix = vectorizer.fit_transform(chunks_df["chunk_text"].tolist())
    with patch(
        "src.search.hybrid.get_collection",
        return_value=_fake_chroma(["dense-1", "dense-2", "exact"], [0.10, 0.20, 1.20]),
    ), patch("src.search.hybrid.encode_queries", return_value=[[0.0]]):
        result = hybrid_search(
            "fake",
            chunks_df,
            vectorizer,
            matrix,
            "2026 2학기 개강 학기개시일",
            top_k=2,
            alpha=0.65,
            tfidf_chunk_ids=["exact", "dense-1", "dense-2"],
        )

    assert result.iloc[0]["chunk_id"] == "exact"
    assert "exact" in result["chunk_id"].tolist()


def test_exact_query_focus_in_title_adds_a_general_ranking_signal():
    chunks_df = pd.DataFrame(
        {
            "chunk_id": ["exact", "generic"],
            "chunk_text": ["[개강]\n기간 안내", "[학사일정 안내]\n개강 관련 내용"],
        }
    )
    from sklearn.feature_extraction.text import TfidfVectorizer

    vectorizer = TfidfVectorizer(max_features=100)
    matrix = vectorizer.fit_transform(chunks_df["chunk_text"].tolist())
    with patch(
        "src.search.hybrid.get_collection",
        return_value=_fake_chroma(["generic", "exact"], [0.20, 0.30]),
    ), patch("src.search.hybrid.encode_queries", return_value=[[0.0]]):
        result = hybrid_search(
            "fake", chunks_df, vectorizer, matrix, "개강일이 언제야?",
            top_k=2, alpha=0.65, tfidf_chunk_ids=["exact", "generic"],
        )

    assert result.iloc[0]["chunk_id"] == "exact"


def test_notice_academic_period_alignment_rewards_exact_target_and_penalizes_old_year():
    query = "2027학년도 1학기 교환학생 지원 기간"

    assert _academic_period_title_adjustment(
        query, "2027-1학기 파견 영어권 교환학생 선발 일정"
    ) == pytest.approx(0.40)
    assert _academic_period_title_adjustment(
        query, "2026-1학기 중화권 교환학생 선발 공지"
    ) == pytest.approx(-0.20)


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


def test_hybrid_search_with_meta_preserves_campus_date_and_locator_fields():
    raw = pd.DataFrame([{
        "chunk_id": "schedule-1", "chunk_text": "[개강]\n\n기간: 2026-09-01",
        "schedule_start": "2026-09-01", "schedule_end": "2026-09-01",
        "campus_scope": "shared", "schedule_id": "schedule-db-1",
        "department": "학사지원팀", "source": "schedule",
    }])
    with patch("src.search.hybrid.hybrid_search", return_value=raw):
        result = hybrid.hybrid_search_with_meta(
            "fake", raw, object(), object(), "개강일", top_k=1,
        )

    assert result.loc[0, "schedule_start"] == "2026-09-01"
    assert result.loc[0, "campus_scope"] == "shared"
    assert result.loc[0, "schedule_id"] == "schedule-db-1"
    assert result.loc[0, "department"] == "학사지원팀"
    assert result.columns.is_unique


def test_대괄호로_시작하는_제목을_통째로_되살린다():
    """`[홍보] …`처럼 대괄호 접두어로 시작하는 제목이 흔하다(5,528건 중 1,618건).

    첫 `]`에서 자르면 그런 제목이 전부 "홍보"가 되어 출처 표시가 무의미해지고,
    제목 일치 보너스도 잘린 조각으로 계산된다.
    """
    from src.search.hybrid import _extract_title

    assert (
        _extract_title("[[홍보] 2026년 소상공인 장학금 안내]\n\n본문")
        == "[홍보] 2026년 소상공인 장학금 안내"
    )
    assert (
        _extract_title("[2026학년도 2학기 학부 수강신청 안내]\n\n본문")
        == "2026학년도 2학기 학부 수강신청 안내"
    )
    assert _extract_title("제목 래퍼가 없는 본문") == "제목 래퍼가 없는 본문"
    assert _extract_title("") == ""
