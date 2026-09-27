from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import rag_service  # noqa: E402


def _payload(event: str) -> dict:
    line = next(line for line in event.splitlines() if line.startswith("data: "))
    return json.loads(line.removeprefix("data: "))


async def _collect(body) -> list[dict]:
    return [
        _payload(event)
        async for event in rag_service._stream_with_terminal_event(body, "request-1")
    ]


def test_completion_metadata_precedes_done_and_carries_persistence_fields():
    async def body():
        yield 'data: {"type":"text","content":"answer"}\n\n'
        yield rag_service._completion_stream_event(
            request_id="request-1",
            grounded=True,
            grounding_score=0.94,
            relevance_score=0.97,
            verification_status="passed",
            suggested_questions=["신청 서류는 무엇인가요?"],
            suggested_question_details=[{
                "question": "신청 서류는 무엇인가요?", "source_refs": ["sha256:source-1"],
            }],
            resolved_intents=["notices", "scholarship"],
            fallback_reason=None,
            sources=[{"source": "notices", "metadata": {"campus_scope": "seoul"}}],
        )

    payloads = asyncio.run(_collect(body()))

    assert [payload["type"] for payload in payloads] == ["text", "completion", "done"]
    completion = payloads[-2]
    assert completion == {
        "type": "completion",
        "request_id": "request-1",
        "grounded": True,
        "grounding_score": 0.94,
        "relevance_score": 0.97,
        "verification_status": "passed",
        "suggested_questions": ["신청 서류는 무엇인가요?"],
        "suggested_question_details": [{
            "question": "신청 서류는 무엇인가요?", "source_refs": ["sha256:source-1"],
        }],
        "resolved_intents": ["notices", "scholarship"],
        "fallback_reason": None,
        "sources": [{"source": "notices", "metadata": {"campus_scope": "seoul"}}],
    }


def test_error_before_completion_emits_neither_completion_nor_done():
    async def body():
        yield 'data: {"type":"text","content":"partial"}\n\n'
        raise RuntimeError("upstream failed")

    payloads = asyncio.run(_collect(body()))
    assert [payload["type"] for payload in payloads] == ["text", "error"]


def test_duplicate_completion_is_protocol_error_without_done():
    async def body():
        for _ in range(2):
            yield rag_service._completion_stream_event(
                request_id="request-1",
                grounded=True,
                grounding_score=0.94,
                suggested_questions=[],
                fallback_reason=None,
                sources=[],
            )

    payloads = asyncio.run(_collect(body()))
    types = [payload["type"] for payload in payloads]
    assert types == ["completion", "error"]
    assert types.count("completion") == 1
    assert "done" not in types


def test_cancellation_after_completion_still_emits_no_done():
    async def body():
        yield rag_service._completion_stream_event(
            request_id="request-1",
            grounded=True,
            grounding_score=0.94,
            suggested_questions=[],
            fallback_reason=None,
            sources=[],
        )
        raise asyncio.CancelledError()

    async def consume() -> list[dict]:
        seen: list[dict] = []
        try:
            async for event in rag_service._stream_with_terminal_event(body(), "request-1"):
                seen.append(_payload(event))
        except asyncio.CancelledError:
            pass
        return seen

    payloads = asyncio.run(consume())
    assert [payload["type"] for payload in payloads] == ["completion"]
