from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.langchain_chat import _get_system_prompt  # noqa: E402


def test_langchain_prompt_allows_english_answers_with_original_sources():
    prompt = _get_system_prompt("rag")

    assert "사용자가 영어로 질문하면 영어로 답" in prompt
    assert "URL" in prompt
    assert "원문 표기" in prompt


def test_langchain_prompt_prohibits_generalizing_period_bound_documents():
    prompt = _get_system_prompt("rag")

    assert "그 기간에만" in prompt
    assert "현재의 일반 절차처럼 설명하지 말고" in prompt


def test_지난_일정도_내용을_먼저_알려주게_한다():
    """규칙이 모호하면 모델이 내용을 통째로 버린다.

    실측: 컨텍스트에 2025-12-12 종강총회가 있는데도 36B는 "확인되지 않습니다"로,
    8B는 "개최됩니다"(미래형)로 답했다. "지난 항목만 있으면 … 확인되지 않는다고
    안내하세요"라는 한 문장에서 모델이 뒷절만 취한 결과였다.
    """
    from src.services.langchain_chat import _get_system_prompt

    prompt = _get_system_prompt("rag")
    rule = next(line for line in prompt.splitlines() if "지난 항목만" in line)
    # 내용을 먼저 주라는 지시와, 없을 때만 쓰는 표현이라는 경계가 함께 있어야 한다.
    assert "그 내용을 먼저 알려준" in rule
    assert "자료 자체가 없을 때" in rule
