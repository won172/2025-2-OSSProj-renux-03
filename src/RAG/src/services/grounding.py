"""답변이 검색 컨텍스트에 의해 충분히 뒷받침되는지 점검합니다."""
from __future__ import annotations

import json
import logging
import time
from typing import Any
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.config import OPENAI_CHAT_TIMEOUT_SECONDS, OPENAI_MODEL
from src.services.langchain_chat import _append_usage_record, _extract_usage_metadata

logger = logging.getLogger(__name__)

_MAX_CONTEXT_CHARS = 4000
_MAX_ANSWER_CHARS = 2000

_GROUNDING_LLM = ChatOpenAI(
    model=OPENAI_MODEL,
    temperature=0,
    timeout=OPENAI_CHAT_TIMEOUT_SECONDS,
    max_retries=1,
    model_kwargs={"response_format": {"type": "json_object"}},
)


@dataclass
class GroundingResult:
    checked: bool
    grounded: bool
    score: float
    reason: str | None


def _unchecked_pass() -> GroundingResult:
    return GroundingResult(checked=False, grounded=True, score=1.0, reason=None)


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


async def check_answer_grounding(
    question: str,
    answer: str,
    context: str,
    *,
    min_score: float,
    usage_collector: list[dict[str, Any]] | None = None,
) -> GroundingResult:
    """답변 주장 중 컨텍스트로 직접 뒷받침되는 비율을 1회 LLM 호출로 평가합니다."""
    if not answer.strip() or not context.strip():
        return _unchecked_pass()

    try:
        messages = [
            SystemMessage(
                content=(
                    "당신은 RAG 답변의 근거성을 검증하는 평가자입니다. "
                    "컨텍스트에 직접 근거가 있는 답변 주장 비율만 평가하고, 추측하지 마세요."
                )
            ),
            HumanMessage(
                content=(
                    "[사용자 질문]\n"
                    f"{question.strip()}\n\n"
                    "[컨텍스트]\n"
                    f"{context.strip()[:_MAX_CONTEXT_CHARS]}\n\n"
                    "[답변]\n"
                    f"{answer.strip()[:_MAX_ANSWER_CHARS]}\n\n"
                    "컨텍스트와 답변을 비교해 답변의 주장 중 컨텍스트로 직접 뒷받침되는 비율을 0~1 숫자로 평가하세요. "
                    '반드시 STRICT JSON 객체만 출력하세요: {"score": 0.0, "reason": "..."}'
                )
            ),
        ]
        started_at = time.perf_counter()
        response = await _GROUNDING_LLM.ainvoke(messages)
        _append_usage_record(
            usage_collector,
            stage="grounding_check",
            provider="openai",
            model=OPENAI_MODEL,
            usage=_extract_usage_metadata(response),
            latency_ms=(time.perf_counter() - started_at) * 1000,
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        parsed = json.loads(_strip_code_fence(content))
        raw_score = parsed.get("score")
        score = max(0.0, min(1.0, float(raw_score)))
        reason = parsed.get("reason")
        return GroundingResult(
            checked=True,
            grounded=score >= min_score,
            score=score,
            reason=reason.strip() if isinstance(reason, str) and reason.strip() else None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Grounding check failed: %s", exc)
        return _unchecked_pass()


__all__ = ["GroundingResult", "check_answer_grounding"]
