"""희소 검색 백엔드 스위치(RAG_LEXICAL_BACKEND) 계약 테스트.

pkl과 FTS5를 바꿔 끼울 수 있게 하되, 바꿔도 **정규화 의미가 달라지지 않아야**
두 백엔드의 검색 품질을 비교할 수 있다(S1.4). 또 FTS5 인덱스가 아직 없는
상태에서 백엔드만 켰을 때 검색이 죽지 않고 pkl로 폴백해야 한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.search.fts_index import build_fts_index, fts5_available  # noqa: E402
from src.search.hybrid import (  # noqa: E402
    BM25LexicalIndex,
    _load_lexical_artifact,
    score_lexical_query,
    train_bm25,
)

pytestmark = pytest.mark.skipif(
    not fts5_available(), reason="이 SQLite 빌드는 FTS5를 지원하지 않습니다"
)

_문서 = {
    "밀집": "수강신청 수강신청 안내",
    "희석": "수강신청 " + "다른 내용이 계속 이어지는 문서입니다 " * 40,
    **{f"잡음{i}": f"학식 메뉴 장학금 도서관 공지 {i}" for i in range(50)},
}


@pytest.fixture
def 양쪽_인덱스(tmp_path, monkeypatch):
    """같은 코퍼스로 pkl과 FTS5 인덱스를 모두 만들고 두 경로를 tmp로 돌린다."""
    import src.search.fts_index as fts
    import src.search.hybrid as hybrid

    monkeypatch.setattr(hybrid, "VECTORIZER_DIR", tmp_path)
    monkeypatch.setattr(hybrid, "TFIDF_VERIFY_INTEGRITY", False)
    monkeypatch.setattr(fts, "fts_db_path", lambda: tmp_path / "lexical_fts.db")

    ids, texts = list(_문서.keys()), list(_문서.values())
    train_bm25("notices", texts, chunk_ids=ids)
    build_fts_index("notices", texts, ids, db_path=tmp_path / "lexical_fts.db")
    return ids


def _백엔드(monkeypatch, 이름: str) -> None:
    import src.search.hybrid as hybrid

    monkeypatch.setattr(hybrid, "LEXICAL_BACKEND", 이름)


def _점수(질의: str) -> tuple[np.ndarray, list[str]]:
    data = _load_lexical_artifact("notices")
    scores = score_lexical_query(data["vectorizer"], data["matrix"], 질의)
    return scores, [str(c) for c in data["chunk_ids"]]


# --- 백엔드 선택 --------------------------------------------------------------


def test_기본값은_pkl이다():
    """측정 전에 운영 동작이 바뀌면 안 된다."""
    from src.config import LEXICAL_BACKEND

    assert LEXICAL_BACKEND == "pickle"


def test_pkl_백엔드는_BM25인덱스를_돌려준다(양쪽_인덱스, monkeypatch):
    _백엔드(monkeypatch, "pickle")
    data = _load_lexical_artifact("notices")
    assert isinstance(data["vectorizer"], BM25LexicalIndex)
    assert data["metadata"]["retriever_type"] == "bm25"


def test_fts5_백엔드는_FTS5인덱스를_돌려준다(양쪽_인덱스, monkeypatch):
    from src.search.fts_index import Fts5LexicalIndex

    _백엔드(monkeypatch, "fts5")
    data = _load_lexical_artifact("notices")
    assert isinstance(data["vectorizer"], Fts5LexicalIndex)
    assert data["metadata"]["retriever_type"] == "fts5"


# --- 호출부 계약 --------------------------------------------------------------


@pytest.mark.parametrize("백엔드", ["pickle", "fts5"])
def test_matrix_행수가_문서_수와_같다(양쪽_인덱스, monkeypatch, 백엔드):
    """hybrid_search가 matrix.shape[0]으로 행 수 정합성을 확인한다."""
    _백엔드(monkeypatch, 백엔드)
    data = _load_lexical_artifact("notices")
    assert data["matrix"].shape[0] == len(_문서)
    assert len(data["chunk_ids"]) == len(_문서)


@pytest.mark.parametrize("백엔드", ["pickle", "fts5"])
def test_점수가_chunk_ids_순서와_맞는다(양쪽_인덱스, monkeypatch, 백엔드):
    _백엔드(monkeypatch, 백엔드)
    scores, ids = _점수("수강신청")
    assert scores.shape == (len(_문서),)
    assert ids[int(np.argmax(scores))] == "밀집"
    assert scores[ids.index("잡음0")] == 0.0


# --- 정규화 의미가 백엔드에 의존하지 않는다 ------------------------------------


@pytest.mark.parametrize("백엔드", ["pickle", "fts5"])
def test_정규화는_최댓값_1_클리핑_0(양쪽_인덱스, monkeypatch, 백엔드):
    _백엔드(monkeypatch, 백엔드)
    scores, _ = _점수("수강신청")
    assert scores.min() >= 0.0
    assert scores.max() == pytest.approx(1.0)


@pytest.mark.parametrize("백엔드", ["pickle", "fts5"])
def test_관련도_순서가_백엔드와_무관하게_유지된다(양쪽_인덱스, monkeypatch, 백엔드):
    """부호가 뒤집힌 백엔드가 끼어들면 여기서 잡힌다."""
    _백엔드(monkeypatch, 백엔드)
    scores, ids = _점수("수강신청")
    assert scores[ids.index("밀집")] > scores[ids.index("희석")] > 0


# --- 폴백 ---------------------------------------------------------------------


def test_FTS5_인덱스가_없으면_pkl로_폴백한다(tmp_path, monkeypatch, caplog):
    """재색인 전에 백엔드만 켜도 검색이 죽지 않아야 한다."""
    import logging

    import src.search.fts_index as fts
    import src.search.hybrid as hybrid

    monkeypatch.setattr(hybrid, "VECTORIZER_DIR", tmp_path)
    monkeypatch.setattr(hybrid, "TFIDF_VERIFY_INTEGRITY", False)
    monkeypatch.setattr(fts, "fts_db_path", lambda: tmp_path / "없는파일.db")
    train_bm25("notices", list(_문서.values()), chunk_ids=list(_문서.keys()))

    _백엔드(monkeypatch, "fts5")
    with caplog.at_level(logging.WARNING):
        data = _load_lexical_artifact("notices")

    assert isinstance(data["vectorizer"], BM25LexicalIndex)
    assert "pkl로 폴백" in caplog.text


def test_알_수_없는_백엔드_이름은_pkl로_동작한다(양쪽_인덱스, monkeypatch):
    """오타 난 env로 검색이 멈추지 않게 한다."""
    _백엔드(monkeypatch, "fts")
    data = _load_lexical_artifact("notices")
    assert isinstance(data["vectorizer"], BM25LexicalIndex)
