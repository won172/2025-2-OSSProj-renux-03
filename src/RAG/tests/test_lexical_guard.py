"""RRF 융합에서 lexical guard가 희소 1위를 최상단에 고정하지 않아야 한다.

`final_score = max(weighted_score, s_score) + ...` 에서 s_score는 최댓값으로
나눈 값이라 희소 1위 문서가 **항상 정확히 1.0**이다. 반면 RRF 점수는 두 랭킹
모두 1위일 때만 1.0에 닿는다. 그래서 max()가 희소 상위 문서를 밀집 근거와
무관하게 끌어올려, 융합을 하는 의미가 사라졌다.

골든 69건 실측: RRF에서 guard를 끄면 키워드 커버리지 58.5% → 62.6%
(개선 6건 · 악화 0건). weighted 모드는 척도가 맞으므로 그대로 둔다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.search.hybrid as hybrid  # noqa: E402


@pytest.fixture
def 코퍼스(monkeypatch):
    """밀집은 '정답'을, 희소는 '어휘'를 1위로 올린다.

    제목·본문이 질의와 겹치지 않게 두어 제목 가산이 결과를 대신 결정하지 않게 한다.
    (처음 쓴 판은 제목 가산이 정답을 구해줘서 guard를 되돌려도 통과했다.)
    """
    df = pd.DataFrame([
        {"chunk_id": "정답", "chunk_text": "가나다 라마바 사아자", "title": "가나다"},
        {"chunk_id": "어휘", "chunk_text": "가나다 가나다 가나다", "title": "라마바"},
    ])
    # 밀집: 정답만 찾는다. 어휘 문서는 밀집 근거가 전혀 없다.
    # (양쪽을 대칭으로 두면 RRF 점수가 같아져 판별력이 사라진다.)
    monkeypatch.setattr(hybrid, "query_items", lambda *a, **k: {
        "ids": [["정답"]], "distances": [[0.10]],
    })
    monkeypatch.setattr(hybrid, "get_collection", lambda *a, **k: type("C", (), {"metadata": {}})())
    monkeypatch.setattr(hybrid, "encode_queries", lambda q: np.zeros((1, 3), dtype=np.float32))

    # 희소: 어휘 문서를 1위(정규화 후 1.0)로 올린다.
    monkeypatch.setattr(hybrid, "score_lexical_query",
                        lambda vec, mat, q: np.array([0.2, 1.0]))
    return df, object(), np.empty((2, 0), dtype=np.float32), ["정답", "어휘"]


def _상위(코퍼스, 모드, monkeypatch):
    df, vec, mat, ids = 코퍼스
    monkeypatch.setattr(hybrid, "HYBRID_FUSION_MODE", 모드)
    hits = hybrid.hybrid_search("dongguk_rules", df, vec, mat, "타차카 파하",
                                top_k=2, tfidf_chunk_ids=ids)
    return hits["chunk_id"].astype(str).tolist()


def test_rrf에서는_희소_1위가_밀집_1위를_밀어내지_않는다(코퍼스, monkeypatch):
    assert _상위(코퍼스, "rrf", monkeypatch)[0] == "정답"


def test_weighted에서는_guard가_그대로_동작한다(코퍼스, monkeypatch):
    """척도가 맞는 모드에서는 기존 보호 장치를 유지한다."""
    assert _상위(코퍼스, "weighted", monkeypatch)[0] == "어휘"


# --- 제목 집중도 가산 ----------------------------------------------------------


def test_제목_가산_기본값은_기존_동작을_유지한다():
    """하드코딩 0.18을 설정으로만 뺐다. 기본값을 바꾸면 동작이 달라진다.

    0으로 내리면 골든 70건 커버리지는 61.7% → 63.0%로 오르지만, test_hybrid의
    기존 보호 두 가지(정확 어휘 일치 보호, 제목 순위 신호)가 깨진다.
    +1.3%p는 그 둘을 뒤집기에 약해 기본값은 그대로 둔다.
    """
    from src.config import HYBRID_TITLE_FOCUS_WEIGHT

    assert HYBRID_TITLE_FOCUS_WEIGHT == 0.18


def test_가중치를_올리면_제목_일치_문서가_올라온다(monkeypatch):
    """설정이 실제로 점수에 반영되는지 확인한다(0으로 굳어 있지 않다)."""
    df = pd.DataFrame([
        {"chunk_id": "제목일치", "chunk_text": "본문 가나다", "title": "타차카 파하"},
        {"chunk_id": "제목무관", "chunk_text": "본문 가나다", "title": "라마바"},
    ])
    monkeypatch.setattr(hybrid, "query_items", lambda *a, **k: {
        "ids": [["제목무관", "제목일치"]], "distances": [[0.10, 0.11]],
    })
    monkeypatch.setattr(hybrid, "get_collection",
                        lambda *a, **k: type("C", (), {"metadata": {}})())
    monkeypatch.setattr(hybrid, "encode_queries", lambda q: np.zeros((1, 3), dtype=np.float32))
    monkeypatch.setattr(hybrid, "score_lexical_query", lambda v, m, q: np.array([0.0, 0.0]))

    def 상위(weight):
        monkeypatch.setattr(hybrid, "HYBRID_TITLE_FOCUS_WEIGHT", weight)
        hits = hybrid.hybrid_search("dongguk_rules", df, object(),
                                    np.empty((2, 0), dtype=np.float32), "타차카 파하",
                                    top_k=2, tfidf_chunk_ids=["제목일치", "제목무관"])
        return hits["chunk_id"].astype(str).tolist()[0]

    assert 상위(0.0) == "제목무관"   # 밀집 순서 그대로
    assert 상위(0.5) == "제목일치"   # 가산이 뒤집는다
