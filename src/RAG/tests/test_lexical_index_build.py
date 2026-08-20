"""수집 파이프라인이 pkl과 FTS5 인덱스를 함께 갱신하는지 검증한다.

한쪽만 갱신하면 `RAG_LEXICAL_BACKEND`를 바꾼 순간 낡은 인덱스로 검색하게 된다.
스케줄러가 6시간마다 공지를 재색인하므로, 며칠이면 두 인덱스가 크게 벌어진다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pipelines.ingest import _train_lexical_indices  # noqa: E402
from src.search.fts_index import fts5_available, load_fts_index  # noqa: E402

pytestmark = pytest.mark.skipif(
    not fts5_available(), reason="이 SQLite 빌드는 FTS5를 지원하지 않습니다"
)

_텍스트 = ["수강신청 안내", "장학금 신청", "학식 메뉴"]
_아이디 = ["c1", "c2", "c3"]


@pytest.fixture
def 격리(tmp_path, monkeypatch):
    """pkl과 FTS5를 모두 임시 경로로 돌린다."""
    import src.search.fts_index as fts
    import src.search.hybrid as hybrid

    monkeypatch.setattr(hybrid, "VECTORIZER_DIR", tmp_path)
    monkeypatch.setattr(hybrid, "TFIDF_VERIFY_INTEGRITY", False)
    monkeypatch.setattr(fts, "fts_db_path", lambda: tmp_path / "lexical_fts.db")
    return tmp_path


def test_두_인덱스가_함께_만들어진다(격리):
    _train_lexical_indices("notices", _텍스트, _아이디)

    assert (격리 / "notices_bm25.pkl").exists()
    fts = load_fts_index("notices", db_path=격리 / "lexical_fts.db")
    assert fts is not None
    assert fts.chunk_ids == _아이디


def test_재색인이_양쪽에_반영된다(격리):
    """한쪽만 갱신되면 백엔드 전환 시 낡은 결과가 나온다."""
    _train_lexical_indices("notices", _텍스트, _아이디)
    _train_lexical_indices("notices", ["새 공지 본문"], ["c9"])

    from src.search.hybrid import load_lexical_with_ids

    _, _, pkl_ids = load_lexical_with_ids("notices")
    fts = load_fts_index("notices", db_path=격리 / "lexical_fts.db")

    assert [str(c) for c in pkl_ids] == ["c9"]
    assert fts is not None and fts.chunk_ids == ["c9"]


def test_반환값은_pkl_쪽이다(격리):
    """호출부 계약(vectorizer, matrix)을 유지해야 한다."""
    from src.search.hybrid import BM25LexicalIndex

    vectorizer, matrix = _train_lexical_indices("notices", _텍스트, _아이디)
    assert isinstance(vectorizer, BM25LexicalIndex)
    assert matrix.shape[0] == len(_텍스트)


# --- 실패 처리 ---------------------------------------------------------------


def _FTS_구축_실패(monkeypatch):
    import src.pipelines.ingest as ingest_mod
    import src.search.fts_index as fts

    def 폭발(*a, **k):
        raise RuntimeError("FTS5 구축 실패")

    monkeypatch.setattr(fts, "build_fts_index", 폭발)
    return ingest_mod


def test_pkl_백엔드에서는_FTS5_실패가_수집을_막지_않는다(격리, monkeypatch, caplog):
    """아직 검색에 쓰이지 않는 인덱스 때문에 공지 수집이 멈춰서는 안 된다."""
    import logging

    import src.config as config_mod

    _FTS_구축_실패(monkeypatch)
    # _train_lexical_indices는 호출 시점에 src.config에서 읽는다.
    monkeypatch.setattr(config_mod, "LEXICAL_BACKEND", "pickle")

    with caplog.at_level(logging.WARNING):
        vectorizer, _ = _train_lexical_indices("notices", _텍스트, _아이디)

    assert vectorizer is not None
    assert (격리 / "notices_bm25.pkl").exists()
    assert "FTS5 인덱스 구축에 실패" in caplog.text


def test_fts5_백엔드에서는_FTS5_실패를_그대로_올린다(격리, monkeypatch):
    """사용 중인 인덱스가 낡은 채로 남는 것이 수집 실패보다 나쁘다."""
    import src.config as config_mod

    _FTS_구축_실패(monkeypatch)
    monkeypatch.setattr(config_mod, "LEXICAL_BACKEND", "fts5")

    with pytest.raises(RuntimeError, match="FTS5 구축 실패"):
        _train_lexical_indices("notices", _텍스트, _아이디)
