from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import rag_service  # noqa: E402
from src.services.direct_answer import DirectAnswer  # noqa: E402


def _direct_fixture() -> DirectAnswer:
    return DirectAnswer(
        answer="학부 수강신청은 2026년 8월 3일부터 8월 7일까지입니다.",
        kind="schedule_event",
        sources=[
            {
                "source": "schedule",
                "title": "2026학년도 2학기 학부 수강 신청",
                "published_at": "2026-08-03",
                "snippet": "2026학년도 2학기 학부 수강 신청 · 2026-08-03~2026-08-07",
                "url": None,
                "chunk_id": "schedule:47",
                "metadata": {
                    "source_type": "schedule",
                    "schedule_id": "47",
                    "schedule_start": "2026-08-03",
                    "schedule_end": "2026-08-07",
                    "audience": "undergraduate",
                    "campus_scope": "shared",
                },
            }
        ],
    )


def _patch_direct_path(monkeypatch):
    saved_logs = []
    monkeypatch.setattr(rag_service, "RAG_ALLOW_AS_OF_OVERRIDE", True)
    monkeypatch.setattr(rag_service, "USE_QUERY_ANALYSIS", False)
    monkeypatch.setattr(rag_service, "RAG_SEMANTIC_CACHE_ENABLED", False)
    monkeypatch.setattr(rag_service, "_chat_course_recommendation", lambda *_args: None)
    monkeypatch.setattr(rag_service, "_try_direct_answer", lambda *_args: _direct_fixture())
    monkeypatch.setattr(
        rag_service,
        "_save_rag_evaluation_log",
        lambda *_args, **kwargs: saved_logs.append(kwargs),
    )
    monkeypatch.setattr(rag_service, "append_manual_history", lambda *_args: None)
    return saved_logs


def test_old_year_kpi_ignores_years_explicitly_requested_by_user():
    assert rag_service._mentions_unrequested_historical_year(
        "수강신청 기간 알려줘",
        "2023학년도 일정입니다.",
        as_of=rag_service.date(2026, 7, 30),
    )
    assert not rag_service._mentions_unrequested_historical_year(
        "2023학년도 규정 알려줘",
        "2023학년도 규정입니다.",
        as_of=rag_service.date(2026, 7, 30),
    )


@pytest.mark.asyncio
async def test_nonstream_direct_answer_uses_the_normal_source_contract(monkeypatch):
    saved_logs = _patch_direct_path(monkeypatch)
    request = SimpleNamespace(state=SimpleNamespace(request_id="direct-nonstream"))
    response = await rag_service.ask(
        rag_service.AskRequest(question="수강신청 언제야?", asOf="2026-07-30"),
        request,
    )

    assert response.answer.startswith("학부 수강신청")
    assert "[문서1]" in response.answer
    assert response.citations.startswith("- [문서1]")
    assert response.route == ["schedule"]
    assert response.sources[0].source_ref.startswith("sha256:")
    assert response.sources[0].metadata["schedule_id"] == "47"
    assert response.suggested_questions == []
    assert response.suggested_question_details == []
    assert saved_logs == [{"deterministically_grounded": True}]


@pytest.mark.asyncio
async def test_stream_direct_answer_finishes_with_valid_completion_and_done(monkeypatch):
    saved_logs = _patch_direct_path(monkeypatch)
    request = SimpleNamespace(state=SimpleNamespace(request_id="direct-stream"))
    response = await rag_service.ask_stream(
        rag_service.AskRequest(question="수강신청 언제야?", asOf="2026-07-30"),
        request,
    )

    body = ""
    async for item in response.body_iterator:
        body += item.decode() if isinstance(item, bytes) else item
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]

    assert [item["type"] for item in payloads] == [
        "metadata",
        "text",
        "completion",
        "done",
    ]
    completion = payloads[-2]
    metadata = payloads[0]
    assert metadata["citations"].startswith("- [문서1]")
    assert "[문서1]" in payloads[1]["content"]
    assert completion["sources"][0]["source_ref"].startswith("sha256:")
    assert completion["resolved_intents"] == ["schedule"]
    assert completion["suggested_questions"] == []
    assert completion["suggested_question_details"] == []
    assert saved_logs == [{"deterministically_grounded": True}]
