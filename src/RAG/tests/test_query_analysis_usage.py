"""질의분석 호출이 비용 집계에 잡히는지."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services import query_analysis as qa


class _FakeChain:
    def __init__(self, message): self._message = message
    async def ainvoke(self, _payload): return self._message


def _message(content: str):
    return SimpleNamespace(
        content=content,
        usage_metadata={"input_tokens": 620, "output_tokens": 48, "total_tokens": 668},
        response_metadata={},
    )


VALID = (
    '{"normalized_question":"수강신청 기간","intent":"schedule","entities":{},'
    '"time_focus":"none","search_queries":["수강신청 기간"],"needs_clarification":false,'
    '"clarification_reason":null,"is_compound":false,"sub_queries":[]}'
)


@pytest.mark.asyncio
async def test_successful_analysis_is_recorded(monkeypatch):
    monkeypatch.setattr(qa, "_get_analysis_chain", lambda: _FakeChain(_message(VALID)))
    usage: list[dict] = []
    result = await qa.analyze_query("수강신청 기간", usage_collector=usage)
    assert result is not None
    assert len(usage) == 1
    assert usage[0]["stage"] == "query_analysis"
    assert usage[0]["input_tokens"] == 620
    assert usage[0]["output_tokens"] == 48
    assert usage[0]["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_failed_parse_still_records_the_spend(monkeypatch):
    """파싱이 실패해도 호출은 이미 나갔다. 빼면 비용 설명이 맞지 않는다."""
    monkeypatch.setattr(qa, "_get_analysis_chain", lambda: _FakeChain(_message("not json")))
    usage: list[dict] = []
    assert await qa.analyze_query("수강신청 기간", usage_collector=usage) is None
    assert len(usage) == 1
    assert usage[0]["stage"] == "query_analysis"
    assert usage[0]["failed"] is True


@pytest.mark.asyncio
async def test_collector_is_optional(monkeypatch):
    monkeypatch.setattr(qa, "_get_analysis_chain", lambda: _FakeChain(_message(VALID)))
    assert await qa.analyze_query("수강신청 기간") is not None
