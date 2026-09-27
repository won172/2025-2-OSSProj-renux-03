from __future__ import annotations

import asyncio
from datetime import date

import pandas as pd

from scripts.evaluate_ontology_retrieval import CompetencyCase
from scripts import evaluate_structured_retrieval as evaluator
from src.services.ontology_retrieval import OntologyShadowResult


def _case(case_id: str, question: str, route: str) -> CompetencyCase:
    return CompetencyCase(
        id=case_id, question=question, route=route,
        case_type="fixture", expected_entity_type="Course",
        expected_entity_name="자료구조", expected_predicates=("OFFERED_BY",),
    )


def test_selection_uses_runtime_strategy_and_requires_qrels():
    cases = [
        _case("c1", "CSC2007 어느 학과 과목이야?", "courses"),
        _case("c2", "자료구조에 대해 설명해줘", "courses"),
        _case("s1", "장학팀 연락처 알려줘", "staff"),
        _case("r1", "2024학번 졸업 기준 알려줘", "rules"),
    ]
    qrels = {(case.id, case.route): {f"{case.route}:doc": 2} for case in cases[:-1]}

    assert [case.id for case in evaluator.selected_cases(cases, qrels)] == ["c1", "s1"]


def test_evaluator_compares_sql_shortlist_and_no_hit_fallback(monkeypatch):
    cases = [
        _case("c1", "CSC2007 어느 학과 과목이야?", "courses"),
        _case("s1", "장학팀 연락처 알려줘", "staff"),
    ]
    qrels = {
        ("c1", "courses"): {"courses:target": 2},
        ("s1", "staff"): {"staff:target": 2},
    }
    monkeypatch.setattr(evaluator, "build_document_identity_map", lambda _session: {
        key: key for key in ("courses:target", "courses:wrong", "staff:target")
    })

    def graph(_session, question, _route, **_kwargs):
        keys = ("courses:target",) if "CSC2007" in question else ()
        return OntologyShadowResult((), (), keys, 2)

    monkeypatch.setattr(evaluator, "run_ontology_shadow", graph)
    shortlist_calls = []

    async def shortlist(case, *, structured_keys, **_kwargs):
        shortlist_calls.append((case.id, bool(structured_keys)))
        if case.id == "s1":
            return pd.DataFrame([{"document_key": "staff:target"}])
        if structured_keys:
            return pd.DataFrame([{
                "document_key": "courses:target", "structured_match": 1,
            }])
        return pd.DataFrame([{"document_key": "courses:wrong"}])

    monkeypatch.setattr(evaluator, "_production_shortlist", shortlist)
    report = asyncio.run(evaluator.evaluate(
        object(), cases, qrels, as_of=date(2026, 9, 23),
    ))

    assert report["contract"]["cases"] == 2
    assert report["contract"]["label_source"] == "canonical_structured_fixture_not_human"
    assert report["by_dataset"]["courses"]["sql_applied"] == 1
    assert report["by_dataset"]["staff"]["sql_applied"] == 0
    assert report["baseline"]["recall@shortlist"] == 0.5
    assert report["structured"]["recall@shortlist"] == 1.0
    assert report["cases"][1]["baseline_documents"] == report["cases"][1]["structured_documents"]
    assert shortlist_calls[:2] == [("c1", False), ("s1", False)]
    assert set(report["latency"]["cold_warmup_ms_by_dataset"]) == {"courses", "staff"}
