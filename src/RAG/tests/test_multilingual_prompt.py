from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.answer import ANSWER_PROMPT_TEMPLATE  # noqa: E402
from src.services.langchain_chat import _get_system_prompt  # noqa: E402


def test_langchain_prompt_allows_english_answers_with_original_sources():
    prompt = _get_system_prompt("rag")

    assert "사용자가 영어로 질문하면 영어로 답" in prompt
    assert "URL" in prompt
    assert "원문 표기" in prompt


def test_legacy_answer_prompt_allows_english_answers_with_original_sources():
    assert "사용자가 영어로 질문하면 영어로 답" in ANSWER_PROMPT_TEMPLATE
    assert "URL" in ANSWER_PROMPT_TEMPLATE
    assert "원문 표기" in ANSWER_PROMPT_TEMPLATE
