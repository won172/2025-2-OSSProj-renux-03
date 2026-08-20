from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.rag_service import (  # noqa: E402
    SourceChunk,
    _apply_grounding_failure_policy,
    _build_grounding_confirmation_answer,
)
from src.services import grounding  # noqa: E402
from src.services.grounding import (  # noqa: E402
    GroundingResult,
    check_answer_grounding,
)


def test_grounding_confirmation_answer_points_to_sources():
    result = GroundingResult(
        checked=True,
        grounded=False,
        score=0.32,
        reason="일부 주장이 컨텍스트에 없습니다.",
    )
    sources = [
        SourceChunk(
            source="notices",
            metadata={"title": "장학 신청 안내"},
            snippet="신청 기간 안내",
            citation_number=1,
            title="장학 신청 안내",
            url="https://www.dongguk.edu/article/JANGHAKNOTICE/detail/1",
        )
    ]

    answer = _build_grounding_confirmation_answer(result, sources)

    assert answer.startswith("확인 필요")
    assert "근거 일치도" in answer
    assert "장학 신청 안내" in answer
    assert "https://www.dongguk.edu/article/JANGHAKNOTICE/detail/1" in answer


def test_rejected_candidate_is_replaced_before_it_is_transported():
    guarded = _apply_grounding_failure_policy(
        "검증되지 않은 단정",
        "확인 필요: 공식 출처를 확인해 주세요.",
    )

    assert guarded == "확인 필요: 공식 출처를 확인해 주세요."
    assert "검증되지 않은 단정" not in guarded


def test_already_streamed_candidate_can_only_receive_a_guard():
    guarded = _apply_grounding_failure_policy(
        "이미 전송된 답변",
        "확인 필요",
        stream_already_emitted=True,
    )

    assert guarded == "이미 전송된 답변\n\n확인 필요"


@pytest.mark.asyncio
async def test_grounding_rejects_sourced_answer_that_is_irrelevant_to_question(monkeypatch):
    async def fake_invoke(_messages):
        return SimpleNamespace(
            content=(
                '{"grounding_score": 1.0, "relevance_score": 0.05, '
                '"reason": "질문과 답변 주제가 다릅니다."}'
            ),
            usage_metadata={},
            response_metadata={},
        )

    monkeypatch.setattr(
        grounding,
        "_GROUNDING_LLM",
        SimpleNamespace(ainvoke=fake_invoke),
    )
    result = await check_answer_grounding(
        "샤갈",
        "현재 모집 중인 공모전은 코오롱 챌린지입니다.",
        "코오롱 챌린지 모집 공지",
        min_score=0.6,
    )

    assert result.checked is True
    assert result.grounded is False
    assert result.score == 0.05
    assert result.relevance_score == 0.05
