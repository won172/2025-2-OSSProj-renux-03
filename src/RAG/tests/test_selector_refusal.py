"""근거 셀렉터가 "관련 없음"이라 판정했을 때의 처리.

셀렉터 프롬프트 규칙 6은 "아무것도 직접 관련 없으면 빈 목록을 돌려라"다.
그런데 호출부가 그 판정을 통째로 버리고 느슨한 어휘 폴백으로 문서를 되살리고
있었다. 골든 190문항 실측에서 그렇게 되살린 41건 중 **34건(83%)이 결국
grounding 가드로 거절**됐다 — 생성·검증 LLM을 두 번 더 태우고(p50 약 3.2초)
같은 결론에 도달한 것이다. 실제 답변으로 이어진 것은 5건뿐이었다.

여기서는 판정을 존중하되 어휘 근거가 뚜렷한 후보만 되살리는 동작을 고정한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import rag_service  # noqa: E402
from src.services.evidence_selector import EvidenceSelectionDecision  # noqa: E402


def _shortlist() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "candidate_id": "c1",
                "dataset": "notices",
                "dataset_rank": 1,
                "title": "조기졸업 신청 안내",
                "chunk_text": "조기졸업 신청 자격과 절차를 안내합니다. 8학기 이상 재학생이 대상입니다.",
                "topics": "학사공지",
                "category": "",
            },
            {
                "candidate_id": "c2",
                "dataset": "notices",
                "dataset_rank": 2,
                "title": "학사일정 안내",
                "chunk_text": "2026학년도 전과 신청 기간을 안내합니다.",
                "topics": "학사공지",
                "category": "",
            },
        ]
    )


@pytest.fixture()
def _empty_decision(monkeypatch):
    """셀렉터가 '관련 있는 것이 없다'고 판정한 상태."""

    async def _decide(question, candidates, usage_collector=None):
        return EvidenceSelectionDecision(groups=[])

    monkeypatch.setattr(rag_service, "select_evidence_groups", _decide)


@pytest.mark.asyncio
async def test_weakly_related_evidence_is_dropped_when_the_selector_refuses(
    monkeypatch, _empty_decision
):
    """질문 용어를 거의 담지 못한 후보는 되살리지 않는다.

    이게 실측의 34건이다. 되살려 봐야 생성·grounding을 거쳐 결국 거절된다.
    """
    monkeypatch.setattr(rag_service.rag_config, "RAG_HONOR_SELECTOR_REFUSAL", True)
    monkeypatch.setattr(rag_service.rag_config, "RAG_SELECTOR_REFUSAL_MIN_COVERAGE", 0.6)

    selected, fell_back = await rag_service._select_evidence_for_answer(
        "학사일정이 그동안 몇 번 바뀌었는지 변경 이력을 보여줘",
        _shortlist(),
        [],
    )
    assert selected.empty
    assert fell_back is True


@pytest.mark.asyncio
async def test_strongly_matching_evidence_survives_a_selector_refusal(
    monkeypatch, _empty_decision
):
    """셀렉터 오탐을 대비해, 어휘가 뚜렷하게 겹치면 되살린다.

    실측에서 실제 답변으로 이어진 건들(GR-004 등)이 이 경로다.
    """
    monkeypatch.setattr(rag_service.rag_config, "RAG_HONOR_SELECTOR_REFUSAL", True)
    monkeypatch.setattr(rag_service.rag_config, "RAG_SELECTOR_REFUSAL_MIN_COVERAGE", 0.6)

    selected, fell_back = await rag_service._select_evidence_for_answer(
        "조기졸업 신청 자격과 절차를 알려줘",
        _shortlist(),
        [],
    )
    assert not selected.empty
    assert fell_back is True
    # 문턱을 넘었으면 근거 묶음은 종전(느슨한 폴백)과 같아야 한다. 여기서 엄격한
    # 부분집합만 넘기면 근거가 얇아져 grounding이 무너진다(실측 7건).
    loose = rag_service._deterministic_evidence_fallback(
        "조기졸업 신청 자격과 절차를 알려줘", _shortlist()
    )
    assert set(selected["candidate_id"]) == set(loose["candidate_id"])


@pytest.mark.asyncio
async def test_flag_off_keeps_the_previous_permissive_behaviour(
    monkeypatch, _empty_decision
):
    """기본값에서는 종전과 똑같이 동작한다(실험 절차: 검증 전에는 기본값을 바꾸지 않는다)."""
    monkeypatch.setattr(rag_service.rag_config, "RAG_HONOR_SELECTOR_REFUSAL", False)

    selected, fell_back = await rag_service._select_evidence_for_answer(
        "학사일정이 그동안 몇 번 바뀌었는지 변경 이력을 보여줘",
        _shortlist(),
        [],
    )
    assert not selected.empty  # 느슨한 폴백이 약한 후보를 되살린다
    assert fell_back is True


@pytest.mark.asyncio
async def test_a_selector_exception_still_uses_the_permissive_fallback(monkeypatch):
    """예외는 판정이 아니다. 셀렉터가 죽었을 때까지 엄격하게 굴면 답을 잃는다."""
    monkeypatch.setattr(rag_service.rag_config, "RAG_HONOR_SELECTOR_REFUSAL", True)
    monkeypatch.setattr(rag_service.rag_config, "RAG_SELECTOR_REFUSAL_MIN_COVERAGE", 0.6)

    async def _fail(question, candidates, usage_collector=None):
        return None  # select_evidence_groups 는 실패 시 None 을 돌려준다

    monkeypatch.setattr(rag_service, "select_evidence_groups", _fail)

    selected, fell_back = await rag_service._select_evidence_for_answer(
        "학사일정이 그동안 몇 번 바뀌었는지 변경 이력을 보여줘",
        _shortlist(),
        [],
    )
    assert not selected.empty
    assert fell_back is True


def test_coverage_bar_counts_shared_query_terms():
    """임계값의 의미를 고정한다 — 질문 내용어 중 후보에 나타난 비율."""
    shortlist = _shortlist()
    question = "조기졸업 신청 자격과 절차를 알려줘"

    loose = rag_service._deterministic_evidence_fallback(question, shortlist)
    strict = rag_service._deterministic_evidence_fallback(
        question, shortlist, min_term_coverage=0.6
    )
    assert len(loose) >= len(strict)
    assert set(strict["candidate_id"]) == {"c1"}


def test_coverage_bar_of_zero_preserves_the_any_term_rule():
    shortlist = _shortlist()
    matched = rag_service._deterministic_evidence_fallback(
        "학사일정 알려줘", shortlist, min_term_coverage=0.0
    )
    assert not matched.empty
