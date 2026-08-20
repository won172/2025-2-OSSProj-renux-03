from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import rag_service


def _candidates(dataset: str, first: float, second: float, length: int = 100):
    return pd.DataFrame(
        [
            {
                "dataset": dataset,
                "chunk_text": "가" * length,
                "final_score": first,
                "hybrid_score": first,
            },
            {
                "dataset": dataset,
                "chunk_text": "나" * length,
                "final_score": second,
                "hybrid_score": second,
            },
        ]
    )


def test_conditional_reranker_accepts_long_form_low_gap(monkeypatch):
    monkeypatch.setattr(rag_service.rag_config, "RERANKER_MODE", "conditional")
    monkeypatch.setattr(rag_service.rag_config, "RERANKER_MAX_TOP_GAP", 0.08)

    assert rag_service._should_apply_cross_encoder_rerank(
        _candidates("rules", 0.70, 0.65)
    )


def test_conditional_reranker_rejects_large_gap_and_short_notice(monkeypatch):
    monkeypatch.setattr(rag_service.rag_config, "RERANKER_MODE", "conditional")
    monkeypatch.setattr(rag_service.rag_config, "RERANKER_MAX_TOP_GAP", 0.08)
    monkeypatch.setattr(rag_service.rag_config, "RERANKER_MIN_TEXT_CHARS", 450)

    assert not rag_service._should_apply_cross_encoder_rerank(
        _candidates("rules", 0.90, 0.60)
    )
    assert not rag_service._should_apply_cross_encoder_rerank(
        _candidates("notices", 0.70, 0.65, length=100)
    )


def test_always_and_off_modes_are_explicit(monkeypatch):
    candidates = _candidates("notices", 0.90, 0.10)
    monkeypatch.setattr(rag_service.rag_config, "RERANKER_MODE", "always")
    assert rag_service._should_apply_cross_encoder_rerank(candidates)

    monkeypatch.setattr(rag_service.rag_config, "RERANKER_MODE", "off")
    assert not rag_service._should_apply_cross_encoder_rerank(candidates)
