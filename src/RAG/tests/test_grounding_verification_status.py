"""Explicit 4-state grounding verification status.

``grounded=None`` must never be read as success: every answer carries one of
passed / failed / unavailable / not_required, and only ``passed`` is a
completed, positive grounding check.  All LLMs are mocked; no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import rag_service  # noqa: E402
from src.services import grounding  # noqa: E402
from src.services.direct_answer import DirectAnswer  # noqa: E402
from src.services.grounding import (  # noqa: E402
    VERIFICATION_FAILED,
    VERIFICATION_NOT_REQUIRED,
    VERIFICATION_PASSED,
    VERIFICATION_UNAVAILABLE,
    GroundingResult,
    check_answer_grounding,
)


class _FakeJudge:
    def __init__(self, content=None, exc: Exception | None = None):
        self._content = content
        self._exc = exc
        self.calls = 0

    async def ainvoke(self, _messages):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return SimpleNamespace(content=self._content, usage_metadata=None)


def _assert_not_passing(result: GroundingResult) -> None:
    assert result.checked is False
    assert result.grounded is None
    assert result.score is None
    assert result.relevance_score is None


# --- grounding.check_answer_grounding ---------------------------------------


@pytest.mark.asyncio
async def test_empty_answer_is_not_required_and_judge_is_not_called(monkeypatch):
    judge = _FakeJudge(content="{}")
    monkeypatch.setattr(grounding, "_GROUNDING_LLM", judge)

    result = await check_answer_grounding("질문", "   ", "컨텍스트", min_score=0.5)

    assert result.status == VERIFICATION_NOT_REQUIRED
    _assert_not_passing(result)
    assert judge.calls == 0


@pytest.mark.asyncio
async def test_answer_without_context_is_unavailable(monkeypatch):
    judge = _FakeJudge(content="{}")
    monkeypatch.setattr(grounding, "_GROUNDING_LLM", judge)

    result = await check_answer_grounding("질문", "답변입니다.", "  ", min_score=0.5)

    assert result.status == VERIFICATION_UNAVAILABLE
    _assert_not_passing(result)
    assert judge.calls == 0


@pytest.mark.asyncio
async def test_checker_exception_is_unavailable_not_pass(monkeypatch):
    monkeypatch.setattr(
        grounding, "_GROUNDING_LLM", _FakeJudge(exc=TimeoutError("judge timeout"))
    )

    result = await check_answer_grounding("질문", "답변", "컨텍스트", min_score=0.5)

    assert result.status == VERIFICATION_UNAVAILABLE
    _assert_not_passing(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        '{"reason": "점수 누락"}',
        '["grounding_score", 1.0]',
        '{"grounding_score": "높음", "relevance_score": 1.0}',
    ],
)
async def test_unparseable_judge_output_is_unavailable(monkeypatch, content):
    monkeypatch.setattr(grounding, "_GROUNDING_LLM", _FakeJudge(content=content))

    result = await check_answer_grounding("질문", "답변", "컨텍스트", min_score=0.5)

    assert result.status == VERIFICATION_UNAVAILABLE
    _assert_not_passing(result)


@pytest.mark.asyncio
async def test_judge_pass_is_passed(monkeypatch):
    monkeypatch.setattr(
        grounding,
        "_GROUNDING_LLM",
        _FakeJudge(content='{"grounding_score": 0.9, "relevance_score": 0.8, "reason": "ok"}'),
    )

    result = await check_answer_grounding("질문", "답변", "컨텍스트", min_score=0.5)

    assert result.status == VERIFICATION_PASSED
    assert result.checked is True
    assert result.grounded is True
    assert result.score == pytest.approx(0.8)
    assert result.relevance_score == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_judge_below_threshold_is_failed(monkeypatch):
    monkeypatch.setattr(
        grounding,
        "_GROUNDING_LLM",
        _FakeJudge(content='```json\n{"grounding_score": 0.2, "relevance_score": 0.9}\n```'),
    )

    result = await check_answer_grounding("질문", "답변", "컨텍스트", min_score=0.5)

    assert result.status == VERIFICATION_FAILED
    assert result.checked is True
    assert result.grounded is False
    assert result.score == pytest.approx(0.2)


def test_status_is_derived_for_legacy_constructors_and_validated():
    assert GroundingResult(checked=True, grounded=True, score=0.9, reason=None).status == VERIFICATION_PASSED
    assert GroundingResult(checked=True, grounded=False, score=0.1, reason=None).status == VERIFICATION_FAILED
    assert GroundingResult(checked=False, grounded=None, score=None, reason=None).status == VERIFICATION_UNAVAILABLE
    with pytest.raises(ValueError):
        GroundingResult(checked=True, grounded=True, score=1.0, reason=None, status="verified")


# --- rag_service helpers ----------------------------------------------------


def test_generated_answer_without_result_is_unavailable():
    assert rag_service._verification_status_from_grounding(None) == VERIFICATION_UNAVAILABLE
    unchecked = GroundingResult(checked=False, grounded=None, score=None, reason=None)
    assert rag_service._verification_status_from_grounding(unchecked) == VERIFICATION_UNAVAILABLE


def test_cache_rejects_everything_but_passed():
    def allowed(status, grounded):
        return rag_service._should_cache_answer(
            ["rules"], False, False, grounded, "답변", verification_status=status
        )

    assert allowed(VERIFICATION_PASSED, True) is True
    assert allowed(VERIFICATION_UNAVAILABLE, None) is False
    assert allowed(VERIFICATION_NOT_REQUIRED, None) is False
    assert allowed(VERIFICATION_FAILED, False) is False
    # Legacy callers that do not pass a status are rejected as well.
    assert rag_service._should_cache_answer(["rules"], False, False, None, "답변") is False


def test_legacy_cache_hit_status_is_derived_conservatively():
    assert rag_service._cached_verification_status({"grounded": True}) == VERIFICATION_PASSED
    assert rag_service._cached_verification_status({"grounded": False}) == VERIFICATION_FAILED
    assert rag_service._cached_verification_status({"grounded": None}) == VERIFICATION_UNAVAILABLE
    assert rag_service._cached_verification_status({}) == VERIFICATION_UNAVAILABLE
    assert (
        rag_service._cached_verification_status(
            {"grounded": True, "verification_status": "bogus"}
        )
        == VERIFICATION_PASSED
    )


def test_stale_direct_answers_do_not_claim_grounded():
    for kind in ("meal_stale", "schedule_stale"):
        assert rag_service._direct_answer_grounding(
            DirectAnswer(answer="최신 데이터를 확인하지 못했습니다.", kind=kind)
        ) == (None, None)
    assert rag_service._direct_answer_grounding(
        DirectAnswer(answer="수강신청은 8월 3일입니다.", kind="schedule_event")
    ) == (True, 1.0)


# --- endpoint parity ---------------------------------------------------------


async def _stream_payloads(response) -> list[dict]:
    body = ""
    async for item in response.body_iterator:
        body += item.decode() if isinstance(item, bytes) else item
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


async def _run_both(question: str, *, as_of: str | None = None):
    kwargs = {"question": question}
    if as_of is not None:
        kwargs["asOf"] = as_of
    nonstream = await rag_service.ask(
        rag_service.AskRequest(**kwargs),
        SimpleNamespace(state=SimpleNamespace(request_id="parity-nonstream")),
    )
    stream = await rag_service.ask_stream(
        rag_service.AskRequest(**kwargs),
        SimpleNamespace(state=SimpleNamespace(request_id="parity-stream")),
    )
    payloads = await _stream_payloads(stream)
    assert [item["type"] for item in payloads][-2:] == ["completion", "done"]
    return nonstream, payloads[-2], payloads


def _patch_common(monkeypatch):
    saved_logs = []
    monkeypatch.setattr(rag_service, "USE_QUERY_ANALYSIS", False)
    monkeypatch.setattr(rag_service, "RAG_SEMANTIC_CACHE_ENABLED", False)
    monkeypatch.setattr(rag_service, "RAG_ALLOW_AS_OF_OVERRIDE", True)
    monkeypatch.setattr(rag_service, "_chat_course_recommendation", lambda *_args: None)
    monkeypatch.setattr(
        rag_service,
        "_save_rag_evaluation_log",
        lambda *args, **kwargs: saved_logs.append((args, kwargs)),
    )
    monkeypatch.setattr(rag_service, "append_manual_history", lambda *_args: None)
    monkeypatch.setattr(rag_service, "get_recent_history_text", lambda *_args, **_kw: "")
    return saved_logs


@pytest.mark.asyncio
async def test_fallback_terminal_path_parity_is_not_required(monkeypatch):
    _patch_common(monkeypatch)

    nonstream, completion, _ = await _run_both("WISE캠퍼스 휴학 규정 알려줘")

    assert nonstream.fallback_reason == rag_service.FALLBACK_REASON_CAMPUS_OUT_OF_SCOPE
    assert nonstream.verification_status == VERIFICATION_NOT_REQUIRED
    assert completion["verification_status"] == VERIFICATION_NOT_REQUIRED
    assert nonstream.grounded is None and completion["grounded"] is None
    assert nonstream.relevance_score is None and completion["relevance_score"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["meal_stale", "schedule_stale"])
async def test_stale_direct_answer_parity_is_not_grounded(monkeypatch, kind):
    saved_logs = _patch_common(monkeypatch)
    stale = DirectAnswer(
        answer="최신 학식 데이터를 확인하지 못해 현재 식단을 안내할 수 없습니다.",
        kind=kind,
    )
    monkeypatch.setattr(rag_service, "_try_direct_answer", lambda *_args: stale)

    nonstream, completion, payloads = await _run_both("오늘 학식 뭐야?", as_of="2026-07-30")

    assert nonstream.grounded is None and completion["grounded"] is None
    assert nonstream.grounding_score is None and completion["grounding_score"] is None
    assert nonstream.verification_status == VERIFICATION_NOT_REQUIRED
    assert completion["verification_status"] == VERIFICATION_NOT_REQUIRED
    _assert_terminal_fallback(nonstream, completion, payloads, saved_logs, "stale_data")
    assert [kwargs for _, kwargs in saved_logs] == [
        {"deterministically_grounded": False},
        {"deterministically_grounded": False},
    ]


def _assert_terminal_fallback(nonstream, completion, payloads, saved_logs, reason):
    assert nonstream.fallback_triggered is True
    assert nonstream.fallback_reason == completion["fallback_reason"] == reason
    metadata = next(item for item in payloads if item["type"] == "metadata")
    assert metadata["fallback_triggered"] is True
    assert metadata["fallback_reason"] == reason
    assert nonstream.verification_status == completion["verification_status"] == VERIFICATION_NOT_REQUIRED
    assert len(saved_logs) == 2
    assert all(args[6:8] == (True, reason) for args, _ in saved_logs)


@pytest.mark.asyncio
async def test_future_unannounced_fallback_parity(monkeypatch):
    saved_logs = _patch_common(monkeypatch)
    monkeypatch.setattr(
        rag_service, "_try_future_unannounced_answer",
        lambda *_args: DirectAnswer(answer="아직 공지되지 않았습니다.", kind="future_unannounced"),
    )
    nonstream, completion, payloads = await _run_both("2028학년도 신입생 모집요강 알려줘")
    _assert_terminal_fallback(nonstream, completion, payloads, saved_logs, "future_unannounced")


@pytest.mark.asyncio
async def test_clarification_fallback_parity(monkeypatch):
    saved_logs = _patch_common(monkeypatch)
    monkeypatch.setattr(rag_service, "_try_future_unannounced_answer", lambda *_args: None)
    monkeypatch.setattr(rag_service, "_try_direct_answer", lambda *_args: None)
    nonstream, completion, payloads = await _run_both("그 강의 신청해도 돼?")
    _assert_terminal_fallback(nonstream, completion, payloads, saved_logs, "clarification_needed")


@pytest.mark.asyncio
async def test_out_of_domain_fallback_parity_with_mock_analysis(monkeypatch):
    saved_logs = _patch_common(monkeypatch)
    monkeypatch.setattr(rag_service, "USE_QUERY_ANALYSIS", True)
    monkeypatch.setattr(rag_service, "_can_skip_query_analysis", lambda *_args: False)
    monkeypatch.setattr(rag_service, "_try_future_unannounced_answer", lambda *_args: None)
    monkeypatch.setattr(rag_service, "_try_direct_answer", lambda *_args: None)

    async def analyze(*_args, **_kwargs):
        return rag_service.QueryAnalysisResult(normalized_question="샤갈은 누구야?", intent="unknown")

    monkeypatch.setattr(rag_service, "analyze_query", analyze)
    nonstream, completion, payloads = await _run_both("샤갈은 누구야?")
    _assert_terminal_fallback(nonstream, completion, payloads, saved_logs, "out_of_domain")


def _patch_generated_path(monkeypatch, grounding_result: GroundingResult | None):
    _patch_common(monkeypatch)
    monkeypatch.setattr(rag_service, "_try_direct_answer", lambda *_args: None)
    monkeypatch.setattr(rag_service, "RAG_GROUNDING_CHECK_ENABLED", True)
    monkeypatch.setattr(rag_service, "RAG_STREAM_BUFFER_UNTIL_GROUNDED", False)
    monkeypatch.setattr(rag_service, "_update_grounding_log", lambda *_args: None)
    monkeypatch.setattr(rag_service, "_update_observability_log", lambda *_args: None)

    frame = pd.DataFrame(
        [
            {
                "dataset": "rules",
                "evidence_group": 1,
                "hybrid_score": 0.9,
                "structured_match": 1,
                "chunk_id": "rules:1",
                "title": "학칙 휴학 조항",
                "url": "https://www.dongguk.edu/rule/1",
                "text": "휴학은 학기 개시 전까지 신청한다.",
            }
        ]
    )

    async def plan(**_kwargs):
        return SimpleNamespace(
            route=["rules"],
            strategy=rag_service.RetrievalStrategy("hybrid"),
            ontology_document_keys_by_dataset={},
            structured_document_keys_by_dataset={},
        )

    async def retrieve(**_kwargs):
        return [frame.copy()], False, []

    async def enrich(**kwargs):
        return kwargs["frames"], []

    async def select(_query, merged, *_args, **_kwargs):
        return merged, False

    async def generate(**_kwargs):
        return "휴학은 학기 개시 전까지 신청합니다 [문서1]."

    async def generate_stream(**_kwargs):
        yield "휴학은 학기 개시 전까지 신청합니다 [문서1]."

    async def fake_check(*_args, **_kwargs):
        if grounding_result is None:
            raise AssertionError("grounding must not run")
        return grounding_result

    monkeypatch.setattr(rag_service, "_plan_retrieval", plan)
    monkeypatch.setattr(rag_service, "_retrieve_frames_for_queries", retrieve)
    monkeypatch.setattr(rag_service, "_enrich_staff_lookup_frames", enrich)
    monkeypatch.setattr(rag_service, "_build_balanced_shortlist", lambda frames, **_kw: pd.concat(frames, ignore_index=True))
    monkeypatch.setattr(rag_service, "_apply_cross_encoder_rerank", lambda merged, _query: merged)
    monkeypatch.setattr(rag_service, "_select_answer_evidence", select)
    monkeypatch.setattr(rag_service, "_build_selected_evidence_context", lambda *_a, **_kw: "[문서1] 휴학은 학기 개시 전까지 신청한다.")
    monkeypatch.setattr(rag_service, "format_citations", lambda _merged: "- [문서1] 학칙 휴학 조항")
    monkeypatch.setattr(
        rag_service,
        "_source_chunk_from_row",
        lambda row: rag_service.SourceChunk(
            source="rules",
            metadata={},
            snippet=str(row["text"]),
            chunk_id=str(row["chunk_id"]),
            title=str(row["title"]),
            url=str(row["url"]),
        ),
    )
    monkeypatch.setattr(rag_service, "generate_langchain_answer", generate)
    monkeypatch.setattr(rag_service, "generate_langchain_answer_stream", generate_stream)
    monkeypatch.setattr(rag_service, "check_answer_grounding", fake_check)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selector_refused", "expected_reason"),
    [(True, "selector_refused"), (False, "no_results")],
)
async def test_selector_terminal_reason_parity_and_existing_no_results(
    monkeypatch, selector_refused, expected_reason
):
    _patch_generated_path(monkeypatch, None)
    saved_logs = []
    monkeypatch.setattr(
        rag_service,
        "_save_rag_evaluation_log",
        lambda *args, **kwargs: saved_logs.append((args, kwargs)),
    )

    async def select(_query, merged, *_args, **_kwargs):
        selected = merged.iloc[:0].copy()
        if selector_refused:
            selected.attrs["selector_refused"] = True
        return selected, selector_refused

    monkeypatch.setattr(rag_service, "_select_answer_evidence", select)
    nonstream, completion, payloads = await _run_both("휴학 신청 방법 알려줘")
    _assert_terminal_fallback(nonstream, completion, payloads, saved_logs, expected_reason)


@pytest.mark.asyncio
async def test_stale_direct_answer_rescued_after_retrieval_fallback_parity(monkeypatch):
    _patch_generated_path(monkeypatch, None)
    saved_logs = []
    monkeypatch.setattr(
        rag_service,
        "_save_rag_evaluation_log",
        lambda *args, **kwargs: saved_logs.append((args, kwargs)),
    )
    calls = 0

    def direct(*_args):
        nonlocal calls
        calls += 1
        if calls % 2 == 1:
            return None
        return DirectAnswer(answer="최신 일정을 확인하지 못했습니다.", kind="schedule_stale")

    async def select(_query, merged, *_args, **_kwargs):
        return merged.iloc[:0].copy(), False

    monkeypatch.setattr(rag_service, "_try_direct_answer", direct)
    monkeypatch.setattr(rag_service, "_select_answer_evidence", select)
    nonstream, completion, payloads = await _run_both("오늘 학사일정 알려줘")
    assert calls == 4
    _assert_terminal_fallback(nonstream, completion, payloads, saved_logs, "stale_data")


def test_existing_fallback_reason_values_are_unchanged():
    assert {
        rag_service.FALLBACK_REASON_NO_RESULTS,
        rag_service.FALLBACK_REASON_DATE_FILTER_ELIMINATED_ALL,
        rag_service.FALLBACK_REASON_ACTIVE_DEADLINE_ELIMINATED_ALL,
        rag_service.FALLBACK_REASON_DATASET_UNAVAILABLE,
        rag_service.FALLBACK_REASON_SCORE_BELOW_THRESHOLD,
        rag_service.FALLBACK_REASON_CAMPUS_OUT_OF_SCOPE,
    } == {
        "no_results",
        "date_filter_eliminated_all",
        "active_deadline_filter_eliminated_all",
        "dataset_unavailable",
        "score_below_threshold",
        "campus_out_of_scope",
    }


@pytest.mark.asyncio
async def test_generated_path_parity_unavailable_checker(monkeypatch):
    _patch_generated_path(
        monkeypatch,
        GroundingResult(
            checked=False,
            grounded=None,
            score=None,
            reason=None,
            status=VERIFICATION_UNAVAILABLE,
        ),
    )
    original_plan = rag_service._plan_query
    plans = []

    async def capture_plan(**kwargs):
        plan = await original_plan(**kwargs)
        plans.append(plan)
        return plan

    monkeypatch.setattr(rag_service, "_plan_query", capture_plan)

    nonstream, completion, payloads = await _run_both("휴학 신청 방법 알려줘")

    assert len(plans) == 2 and plans[0] == plans[1]
    assert plans[0].direct_handler is None
    assert nonstream.fallback_triggered is False
    assert nonstream.verification_status == VERIFICATION_UNAVAILABLE
    assert completion["verification_status"] == VERIFICATION_UNAVAILABLE
    # grounded=None is not success and is not reported as grounded.
    assert nonstream.grounded is None and completion["grounded"] is None
    assert nonstream.grounding_score is None and completion["grounding_score"] is None
    assert nonstream.relevance_score is None and completion["relevance_score"] is None
    assert not any(item["type"] == "grounding" for item in payloads)
    # Source attribution is unchanged.
    assert nonstream.sources[0].url == "https://www.dongguk.edu/rule/1"
    assert completion["sources"][0]["url"] == "https://www.dongguk.edu/rule/1"


@pytest.mark.asyncio
async def test_generated_path_parity_passed(monkeypatch):
    _patch_generated_path(
        monkeypatch,
        GroundingResult(
            checked=True,
            grounded=True,
            score=0.8,
            reason=None,
            relevance_score=0.85,
        ),
    )

    nonstream, completion, _ = await _run_both("휴학 신청 방법 알려줘")

    assert nonstream.verification_status == VERIFICATION_PASSED
    assert completion["verification_status"] == VERIFICATION_PASSED
    assert nonstream.grounded is True and completion["grounded"] is True
    assert nonstream.relevance_score == completion["relevance_score"] == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_generated_path_parity_failed(monkeypatch):
    _patch_generated_path(
        monkeypatch,
        GroundingResult(
            checked=True,
            grounded=False,
            score=0.2,
            reason="근거 부족",
            relevance_score=0.9,
        ),
    )

    nonstream, completion, payloads = await _run_both("휴학 신청 방법 알려줘")

    assert nonstream.verification_status == VERIFICATION_FAILED
    assert completion["verification_status"] == VERIFICATION_FAILED
    assert nonstream.grounded is False and completion["grounded"] is False
    assert any(item["type"] == "grounding" for item in payloads)


@pytest.mark.asyncio
async def test_generated_path_with_grounding_disabled_is_unavailable(monkeypatch):
    _patch_generated_path(monkeypatch, None)
    monkeypatch.setattr(rag_service, "RAG_GROUNDING_CHECK_ENABLED", False)

    nonstream, completion, _ = await _run_both("휴학 신청 방법 알려줘")

    assert nonstream.verification_status == VERIFICATION_UNAVAILABLE
    assert completion["verification_status"] == VERIFICATION_UNAVAILABLE
    assert nonstream.grounded is None and completion["grounded"] is None


@pytest.mark.asyncio
async def test_unavailable_generated_answer_is_not_semantically_cached(monkeypatch):
    _patch_generated_path(
        monkeypatch,
        GroundingResult(
            checked=False,
            grounded=None,
            score=None,
            reason=None,
            status=VERIFICATION_UNAVAILABLE,
        ),
    )
    monkeypatch.setattr(rag_service, "RAG_SEMANTIC_CACHE_ENABLED", True)
    puts = []
    monkeypatch.setattr(rag_service.semantic_cache, "get", lambda *_args: None)
    monkeypatch.setattr(rag_service.semantic_cache, "put", lambda *args: puts.append(args))

    nonstream, completion, _ = await _run_both("휴학 신청 방법 알려줘")

    assert nonstream.verification_status == VERIFICATION_UNAVAILABLE
    assert completion["verification_status"] == VERIFICATION_UNAVAILABLE
    assert puts == []


@pytest.mark.asyncio
async def test_passed_generated_answer_is_cached_with_status(monkeypatch):
    _patch_generated_path(
        monkeypatch,
        GroundingResult(checked=True, grounded=True, score=0.8, reason=None, relevance_score=0.8),
    )
    monkeypatch.setattr(rag_service, "RAG_SEMANTIC_CACHE_ENABLED", True)
    puts = []
    monkeypatch.setattr(rag_service.semantic_cache, "get", lambda *_args: None)
    monkeypatch.setattr(rag_service.semantic_cache, "put", lambda *args: puts.append(args))

    await _run_both("휴학 신청 방법 알려줘")

    assert len(puts) == 2
    for _query, _ns, payload in puts:
        assert payload["verification_status"] == VERIFICATION_PASSED
        assert payload["relevance_score"] == pytest.approx(0.8)
