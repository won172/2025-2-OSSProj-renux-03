from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.evaluate_rag import summarize_results, write_markdown_report  # noqa: E402


def test_summarize_results_computes_core_rag_metrics():
    results = pd.DataFrame(
        [
            {
                "question": "장학금은?",
                "expected_dataset": "notices",
                "hit": True,
                "context_recall_proxy": True,
                "keyword_score": 1.0,
                "fallback": False,
                "grounding_grounded": True,
                "grounding_score": 0.8,
                "top_hybrid_score": 0.5,
            },
            {
                "question": "졸업요건은?",
                "expected_dataset": "rules",
                "hit": False,
                "context_recall_proxy": False,
                "keyword_score": 0.5,
                "fallback": True,
                "grounding_grounded": False,
                "grounding_score": 0.3,
                "top_hybrid_score": 0.1,
            },
        ]
    )

    summary = summarize_results(
        results,
        {
            "route_hit_rate": 0.8,
            "context_recall_proxy": 0.8,
            "answer_relevancy_proxy": 0.8,
            "fallback_rate": 0.2,
        },
    )

    assert summary["metrics"]["route_hit_rate"] == 0.5
    assert summary["metrics"]["context_recall_proxy"] == 0.5
    assert summary["metrics"]["answer_relevancy_proxy"] == 0.75
    assert summary["metrics"]["faithfulness_proxy"] == 0.5
    assert summary["metrics"]["fallback_rate"] == 0.5
    assert summary["by_dataset"]["notices"]["count"] == 1
    assert any("route_hit_rate" in warning for warning in summary["warnings"])
    assert any("fallback_rate" in warning for warning in summary["warnings"])


def test_write_markdown_report_includes_gate_warnings(tmp_path):
    results = pd.DataFrame(
        [
            {
                "question": "장학금은?",
                "expected_dataset": "notices",
                "actual_route": "rules",
                "source_datasets": "rules",
                "hit": False,
                "context_recall_proxy": False,
                "fallback": True,
            }
        ]
    )
    summary = {
        "total_questions": 1,
        "metrics": {"route_hit_rate": 0.0, "context_recall_proxy": 0.0, "fallback_rate": 1.0},
        "by_dataset": {"notices": {"count": 1, "route_hit_rate": 0.0, "context_recall_proxy": 0.0, "answer_relevancy_proxy": None, "fallback_rate": 1.0}},
        "warnings": ["route_hit_rate 0.00% below threshold 80.00%"],
    }
    report_path = tmp_path / "report.md"

    write_markdown_report(summary, results, report_path)

    report = report_path.read_text(encoding="utf-8")
    assert "RAG Evaluation Report" in report
    assert "Gate Warnings" in report
    assert "route_hit_rate" in report
    assert "Miss Samples" in report
