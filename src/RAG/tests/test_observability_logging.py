from __future__ import annotations

import sys
from pathlib import Path

from langchain_core.messages import AIMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.services.langchain_chat as langchain_chat  # noqa: E402
from api.rag_service import _sum_estimated_llm_cost  # noqa: E402
from src.database import RagQueryLog  # noqa: E402


def test_rag_query_log_has_observability_columns():
    columns = {column.name for column in RagQueryLog.__table__.columns}

    assert "stage_timings_json" in columns
    assert "llm_usage_json" in columns
    assert "estimated_llm_cost_usd" in columns


def test_extract_usage_metadata_from_langchain_message():
    message = AIMessage(
        content="answer",
        usage_metadata={
            "input_tokens": 12,
            "output_tokens": 7,
            "total_tokens": 19,
        },
    )

    usage = langchain_chat._extract_usage_metadata(message)

    assert usage == {"input_tokens": 12, "output_tokens": 7, "total_tokens": 19}


def test_append_usage_record_estimates_openai_cost(monkeypatch):
    monkeypatch.setattr(langchain_chat, "OPENAI_CHAT_INPUT_COST_PER_1M", 1.0)
    monkeypatch.setattr(langchain_chat, "OPENAI_CHAT_OUTPUT_COST_PER_1M", 2.0)
    records: list[dict] = []

    langchain_chat._append_usage_record(
        records,
        stage="generation",
        provider="openai",
        model="test-model",
        usage={"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500},
        latency_ms=12.345,
    )

    assert records == [
        {
            "stage": "generation",
            "provider": "openai",
            "model": "test-model",
            "latency_ms": 12.35,
            "input_tokens": 1000,
            "output_tokens": 500,
            "total_tokens": 1500,
            "estimated_cost_usd": 0.002,
        }
    ]
    assert _sum_estimated_llm_cost(records) == 0.002
