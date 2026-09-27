"""질의분석 호출이 비용 집계에 잡히고, structured output이 기존 계약으로 변환되는지."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services import query_analysis as qa


class _FakeChain:
    def __init__(self, response): self._response = response
    async def ainvoke(self, _payload): return self._response


def _raw():
    return SimpleNamespace(
        content="",
        usage_metadata={"input_tokens": 620, "output_tokens": 48, "total_tokens": 668},
        response_metadata={},
    )


def _output(**overrides) -> qa._QueryAnalysisOutput:
    fields = {
        "normalized_question": "수강신청 기간",
        "intent": "schedule",
        "entities": [],
        "time_focus": "none",
        "search_queries": ["수강신청 기간"],
        "needs_clarification": False,
        "clarification_reason": None,
        "is_compound": False,
        "sub_queries": [],
    }
    fields.update(overrides)
    return qa._QueryAnalysisOutput(**fields)


def _response(parsed, parsing_error=None):
    return {"raw": _raw(), "parsed": parsed, "parsing_error": parsing_error}


@pytest.mark.asyncio
async def test_successful_analysis_is_recorded(monkeypatch):
    monkeypatch.setattr(qa, "_build_analysis_chain", lambda: _FakeChain(_response(_output())))
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
    monkeypatch.setattr(
        qa,
        "_build_analysis_chain",
        lambda: _FakeChain(_response(None, ValueError("not json"))),
    )
    usage: list[dict] = []
    assert await qa.analyze_query("수강신청 기간", usage_collector=usage) is None
    assert len(usage) == 1
    assert usage[0]["stage"] == "query_analysis"
    assert usage[0]["failed"] is True


@pytest.mark.asyncio
async def test_collector_is_optional(monkeypatch):
    monkeypatch.setattr(qa, "_build_analysis_chain", lambda: _FakeChain(_response(_output())))
    assert await qa.analyze_query("수강신청 기간") is not None


@pytest.mark.asyncio
async def test_entity_groups_become_the_dict_contract(monkeypatch):
    """strict 스키마는 Dict를 못 받으므로 [{key, values}]를 받아 기존 dict로 되돌린다."""
    parsed = _output(
        intent="notices",
        normalized_question="통계학과 장학금",
        search_queries=["통계학과 장학금"],
        entities=[
            {"key": "department", "values": ["통계학과"]},
            {"key": "department", "values": ["통계학과", "경영학과"]},
            {"key": "scholarship", "values": []},
            {"key": " ", "values": ["무시"]},
        ],
    )
    monkeypatch.setattr(qa, "_build_analysis_chain", lambda: _FakeChain(_response(parsed)))
    result = await qa.analyze_query("통계학과 장학금")
    assert result is not None
    assert result.entities == {"department": ["통계학과", "경영학과"]}


def test_analysis_schema_is_strict_compatible():
    """strict 모드: 모든 객체가 닫혀 있고 모든 필드가 required여야 한다."""
    from openai.lib._parsing._completions import type_to_response_format_param

    schema = type_to_response_format_param(qa._QueryAnalysisOutput)["json_schema"]["schema"]
    objects = [schema, *schema.get("$defs", {}).values()]
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])


def test_analysis_prompt_puts_dynamic_inputs_last():
    """고정 지침이 앞, 요청마다 바뀌는 값이 뒤에 있어야 prefix 캐시가 적중한다."""
    template = qa.prompt.template
    last_static = template.index("학식/식단 질문에서")
    for variable in ("{history}", "{temporal_context}", "{query}"):
        assert template.index(variable) > last_static


def test_accessor_builds_once_and_reset_clears_the_cache(monkeypatch):
    """체인은 프로세스당 한 번만 만들어 재사용하고, reset 뒤에는 factory로 다시 만든다."""
    built: list[object] = []

    def fake_builder():
        chain = object()
        built.append(chain)
        return chain

    monkeypatch.setattr(qa, "_build_analysis_chain", fake_builder)

    first = qa._get_analysis_chain()
    assert qa._get_analysis_chain() is first
    assert built == [first]

    qa.reset_analysis_chain()
    second = qa._get_analysis_chain()
    assert second is not first
    assert built == [first, second]


def test_concurrent_first_use_builds_a_single_chain(monkeypatch):
    """동시 첫 요청도 체인을 하나만 만든다."""
    import threading
    import time

    calls: list[int] = []

    def slow_builder():
        calls.append(1)
        time.sleep(0.05)
        return object()

    monkeypatch.setattr(qa, "_build_analysis_chain", slow_builder)
    results: list[object] = []
    threads = [threading.Thread(target=lambda: results.append(qa._get_analysis_chain())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1
    assert len({id(r) for r in results}) == 1
