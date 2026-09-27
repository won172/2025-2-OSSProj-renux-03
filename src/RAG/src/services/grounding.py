"""답변이 검색 컨텍스트에 의해 충분히 뒷받침되는지 점검합니다."""
from __future__ import annotations

import json
import logging
import time
from typing import Any
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.config import OPENAI_CHAT_TIMEOUT_SECONDS, OPENAI_GROUNDING_MODEL
from src.services.langchain_chat import (
    _append_usage_record,
    _extract_usage_metadata,
    openai_prompt_cache_kwargs,
)

logger = logging.getLogger(__name__)

_MAX_CONTEXT_CHARS = 4000
_MAX_ANSWER_CHARS = 2000

# Keep imports side-effect free.  Test collection, readiness probes, and
# sparse-only deployments must not require OpenAI credentials merely because
# ``rag_service`` imports the grounding helper.  The public module variable is
# retained for tests/embedders that inject a compatible client.
_GROUNDING_LLM: Any | None = None


def _get_grounding_llm() -> Any:
    global _GROUNDING_LLM
    if _GROUNDING_LLM is None:
        _GROUNDING_LLM = ChatOpenAI(
            model=OPENAI_GROUNDING_MODEL,
            temperature=0,
            timeout=OPENAI_CHAT_TIMEOUT_SECONDS,
            max_retries=1,
            model_kwargs={
                "response_format": {"type": "json_object"},
                **openai_prompt_cache_kwargs("grounding"),
            },
        )
    return _GROUNDING_LLM


# Explicit verification outcomes.  ``grounded=None`` alone cannot distinguish
# "the checker could not run" from "nothing needed checking", so every answer
# carries one of these four values.  Only ``passed`` means a completed,
# positive grounding check.
VERIFICATION_PASSED = "passed"
VERIFICATION_FAILED = "failed"
VERIFICATION_UNAVAILABLE = "unavailable"
VERIFICATION_NOT_REQUIRED = "not_required"
VERIFICATION_STATUSES = frozenset(
    {
        VERIFICATION_PASSED,
        VERIFICATION_FAILED,
        VERIFICATION_UNAVAILABLE,
        VERIFICATION_NOT_REQUIRED,
    }
)


@dataclass
class GroundingResult:
    checked: bool
    grounded: bool | None
    score: float | None
    reason: str | None
    relevance_score: float | None = None
    status: str | None = None

    def __post_init__(self) -> None:
        if self.status is None:
            if self.checked:
                self.status = (
                    VERIFICATION_PASSED if self.grounded else VERIFICATION_FAILED
                )
            else:
                self.status = VERIFICATION_UNAVAILABLE
        if self.status not in VERIFICATION_STATUSES:
            raise ValueError(f"unknown verification status: {self.status!r}")


def _unverified(status: str) -> GroundingResult:
    """A result that must never be read as a passing check."""
    return GroundingResult(
        checked=False,
        grounded=None,
        score=None,
        reason=None,
        relevance_score=None,
        status=status,
    )


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
    """Evaluate both source grounding and question-answer relevance."""
    if not answer.strip():
        # Nothing was claimed, so there is nothing to verify.
        return _unverified(VERIFICATION_NOT_REQUIRED)
    if not context.strip():
        # A non-empty answer without context cannot be verified.
        return _unverified(VERIFICATION_UNAVAILABLE)

    try:
        messages = [
            SystemMessage(
                content=(
                    "당신은 RAG 답변의 안전성을 검증하는 평가자입니다. "
                    "답변이 컨텍스트에 근거하는지와 사용자 질문에 실제로 답하는지를 "
                    "서로 독립적으로 평가하고, 추측하지 마세요."
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
                    "grounding_score는 답변 주장 중 컨텍스트로 직접 뒷받침되는 비율, "
                    "relevance_score는 답변이 사용자 질문의 의도와 주제에 직접 답하는 정도입니다. "
                    "각각 0~1 숫자로 독립 평가하세요. 엉뚱한 질문에 대한 답은 컨텍스트 근거가 "
                    "충분해도 relevance_score를 낮게 주세요. "
                    "반드시 STRICT JSON 객체만 출력하세요: "
                    '{"grounding_score": 0.0, "relevance_score": 0.0, "reason": "..."}'
                )
            ),
        ]
        started_at = time.perf_counter()
        response = await _get_grounding_llm().ainvoke(messages)
        _append_usage_record(
            usage_collector,
            stage="grounding_check",
            provider="openai",
            model=OPENAI_GROUNDING_MODEL,
            usage=_extract_usage_metadata(response),
            latency_ms=(time.perf_counter() - started_at) * 1000,
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        parsed = json.loads(_strip_code_fence(content))
        raw_grounding_score = parsed.get(
            "grounding_score",
            parsed.get("score"),
        )
        raw_relevance_score = parsed.get(
            "relevance_score",
            raw_grounding_score,
        )
        grounding_score = max(
            0.0,
            min(1.0, float(raw_grounding_score)),
        )
        relevance_score = max(
            0.0,
            min(1.0, float(raw_relevance_score)),
        )
        score = min(grounding_score, relevance_score)
        reason = parsed.get("reason")
        grounded = score >= min_score
        return GroundingResult(
            checked=True,
            grounded=grounded,
            status=VERIFICATION_PASSED if grounded else VERIFICATION_FAILED,
            score=score,
            reason=reason.strip() if isinstance(reason, str) and reason.strip() else None,
            relevance_score=relevance_score,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Grounding check failed: %s", exc)
        return _unverified(VERIFICATION_UNAVAILABLE)


__all__ = [
    "GroundingResult",
    "VERIFICATION_FAILED",
    "VERIFICATION_NOT_REQUIRED",
    "VERIFICATION_PASSED",
    "VERIFICATION_STATUSES",
    "VERIFICATION_UNAVAILABLE",
    "check_answer_grounding",
]
