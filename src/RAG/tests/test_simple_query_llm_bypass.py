"""Safe LLM bypasses keep deterministic evidence and the shared API contract."""
import asyncio

import pandas as pd
import pytest

from api import rag_service as service
from src.services.evidence_selector import EvidenceGroupDecision, EvidenceSelectionDecision


def _shortlist(*, keys=("rules:one",), score=0.9, structured=False):
    return pd.DataFrame([
        {
            "candidate_id": f"c{index}",
            "chunk_id": f"chunk-{index}",
            "document_key": key,
            "dataset": "rules",
            "title": "2024학번 교양 이수 기준",
            "chunk_text": "2024학번 교양 이수 기준 공식 자료",
            "source": "official",
            "hybrid_score": score,
            "vector_score": score,
            "sparse_score": score,
            "structured_match": int(structured),
            "dataset_rank": index,
        }
        for index, key in enumerate(keys, 1)
    ])


@pytest.mark.parametrize(
    ("strategy", "keys", "score", "structured", "reason"),
    [
        (service.RetrievalStrategy("structured", "rules"), ("rules:one",), 0.0, True,
         "structured_sql_document"),
        (service.RetrievalStrategy("structured", "rules"), ("rules:one", "rules:one"), 0.0, True,
         "structured_sql_document"),
    ],
)
def test_safe_selector_bypass_matches_deterministic_path(
    monkeypatch, strategy, keys, score, structured, reason,
):
    frame = _shortlist(keys=keys, score=score, structured=structured)
    expected = service._deterministic_evidence_fallback(
        "2024학번 교양 이수 기준", service._refine_final_candidate_scope("2024학번 교양 이수 기준", frame),
    )
    async def unexpected(*_args, **_kwargs):
        pytest.fail("LLM selector called")
    monkeypatch.setattr(service, "select_evidence_groups", unexpected)
    metadata = {}
    selected, fell_back = asyncio.run(service._select_answer_evidence(
        "2024학번 교양 이수 기준", frame, [],
        recent_notice_query=False, strategy=strategy,
        structured_keys_by_dataset={"rules": ("rules:one", "rules:two")},
        selection_metadata=metadata,
    ))
    assert not fell_back
    pd.testing.assert_frame_equal(selected, expected)
    assert metadata == {"skipped": True, "reason": reason}


@pytest.mark.parametrize(
    ("strategy", "keys", "score", "structured"),
    [
        (service.RetrievalStrategy("structured", "rules"), ("rules:one", "rules:two"), 0.0, True),
        (service.RetrievalStrategy("structured", "rules"), ("rules:one", "rules:other"), 0.0, True),
        (service.RetrievalStrategy("structured", "rules"), ("rules:other",), 0.0, True),
        (service.RetrievalStrategy("structured", "rules"), ("rules:one",), 0.0, False),
        (service.RetrievalStrategy("hybrid"), ("rules:one",), 0.9, False),
        (service.RetrievalStrategy("hybrid"), ("rules:one",), 0.31, False),
        (service.RetrievalStrategy("hybrid"), ("rules:one", "rules:two"), 0.9, False),
    ],
)
def test_selector_near_miss_keeps_llm(monkeypatch, strategy, keys, score, structured):
    frame = _shortlist(keys=keys, score=score, structured=structured)
    calls = []
    async def select(_question, candidates, _usage):
        calls.append(candidates)
        return EvidenceSelectionDecision(groups=[EvidenceGroupDecision(document_ids=["c1"])])
    monkeypatch.setattr(service, "select_evidence_groups", select)
    metadata = {}
    selected, fell_back = asyncio.run(service._select_answer_evidence(
        "2024학번 교양 이수 기준", frame, [],
        recent_notice_query=False, strategy=strategy,
        structured_keys_by_dataset={"rules": ("rules:one", "rules:two")},
        selection_metadata=metadata,
    ))
    assert not fell_back
    assert len(calls) == 1
    assert selected["candidate_id"].tolist() == ["c1"]
    assert metadata == {"skipped": False, "reason": "llm_required"}


def test_high_score_on_uncited_chunk_does_not_bypass_selector(monkeypatch):
    frame = _shortlist(keys=("rules:one", "rules:one"), score=0.1)
    frame.loc[1, ["vector_score", "sparse_score", "hybrid_score"]] = 0.9
    calls = []
    async def select(_question, _candidates, _usage):
        calls.append(True)
        return EvidenceSelectionDecision(groups=[EvidenceGroupDecision(document_ids=["c2"])])
    monkeypatch.setattr(service, "select_evidence_groups", select)
    selected, _ = asyncio.run(service._select_answer_evidence(
        "2024학번 교양 이수 기준", frame, [], recent_notice_query=False,
        strategy=service.RetrievalStrategy("hybrid"),
    ))
    assert calls == [True]
    assert selected["candidate_id"].tolist() == ["c2"]
