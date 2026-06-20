from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.rag_service import SourceChunk, _build_grounding_confirmation_answer  # noqa: E402
from src.services.grounding import GroundingResult  # noqa: E402


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
