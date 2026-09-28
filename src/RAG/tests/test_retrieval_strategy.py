from __future__ import annotations

import asyncio
import inspect

import pandas as pd

import api.rag_service as rag_service
from src.services.retrieval_strategy import choose_retrieval_strategy
from src.services.router import keyword_route
from scripts.evaluate_ontology_retrieval import load_cases, load_document_qrels
from src.services.ontology_retrieval import OntologyShadowResult


def test_strategy_uses_sql_only_for_scoped_single_dataset_questions():
    assert choose_retrieval_strategy("컴퓨터·AI학부 사무실 전화번호", ["staff"]).dataset == "staff"
    assert choose_retrieval_strategy("CSC2007 어느 학과에서 개설해?", ["courses"]).dataset == "courses"
    assert choose_retrieval_strategy("22학번 통계학과 졸업요건", ["rules"]).dataset == "rules"
    assert choose_retrieval_strategy("22학번 조기졸업 요건", ["rules"]).mode == "hybrid"
    assert choose_retrieval_strategy("자료구조 과목 설명", ["courses"]).mode == "hybrid"
    assert choose_retrieval_strategy("졸업 준비 절차와 담당자", ["rules", "staff"]).mode == "hybrid"


def test_course_code_and_offering_questions_reach_course_route():
    assert keyword_route("CSC2007 어느 학과에서 개설해?") == ["courses"]
    assert keyword_route("자료구조는 어느 학과에서 개설해?") == ["courses"]


def test_structural_qrels_reach_the_same_production_route_as_the_evaluator():
    meta = rag_service.QueryAnalysisMeta(result=None)
    qrels = load_document_qrels()
    cases = [
        case for case in load_cases()
        if (case.id, case.route) in qrels
        and choose_retrieval_strategy(case.question, [case.route]).mode == "structured"
    ]

    assert len(cases) == 45
    assert all(
        rag_service._resolve_retrieval_route(case.question, meta) == [case.route]
        for case in cases
    )


def test_scoped_cohort_rules_skip_analysis_but_compound_and_operational_queries_do_not():
    question = "2024학번 교양 이수 기준을 알려줘"
    assert keyword_route(question) == ["rules"]
    assert rag_service._resolve_retrieval_route(
        question, rag_service.QueryAnalysisMeta(result=None),
    ) == ["rules"]
    assert rag_service._can_skip_query_analysis(
        question, rag_service._query_for_analysis(question), "",
    )
    assert not rag_service._can_skip_query_analysis(
        question, rag_service._query_for_analysis(question), "직전 대화",
    )
    for compound in (
        "2024학번 졸업 기준과 담당자 전화번호",
        "2024학번 졸업 신청 공지 알려줘",
        "2024학번 조기졸업 요건 알려줘",
    ):
        route = rag_service._resolve_retrieval_route(
            compound, rag_service.QueryAnalysisMeta(result=None),
        )
        assert route != ["rules"]
        assert not rag_service._can_skip_query_analysis(
            compound, rag_service._query_for_analysis(compound), "",
        )


def test_current_cohort_rules_preserve_notice_companion_and_analysis_intent():
    for question in (
        "2024학번 현재 졸업 기준 변경 공지 알려줘",
        "2024학번 현재 교양 기준 알려줘",
    ):
        route = rag_service._resolve_retrieval_route(
            question, rag_service.QueryAnalysisMeta(result=None),
        )
        assert "rules" in route and "notices" in route
        assert not rag_service._can_skip_query_analysis(
            question, rag_service._query_for_analysis(question), "",
        )

    analyzed = rag_service.QueryAnalysisMeta(result=rag_service.QueryAnalysisResult(
        normalized_question="2024학번 졸업 기준",
        intent="notices",
    ))
    assert "notices" in rag_service._resolve_retrieval_route(
        "2024학번 졸업 기준", analyzed,
    )


def test_both_endpoints_use_the_shared_revision_checked_retrieval_plan():
    planner = inspect.getsource(rag_service._plan_query)
    assert "await _plan_retrieval(" in planner
    for endpoint in (rag_service.ask, rag_service.ask_stream):
        source = inspect.getsource(endpoint)
        assert "await _plan_query(" in source
        assert "execute_query(" in source
    execution = inspect.getsource(rag_service._execute_retrieval_steps)
    assert "structured_document_keys_by_dataset=structured_document_keys_by_dataset" in execution


def test_retrieval_plan_respects_structured_flag_and_returns_exact_rule_keys(monkeypatch):
    calls = []
    monkeypatch.setattr(rag_service.rag_config, "RAG_ONTOLOGY_SHADOW_ENABLED", False)
    monkeypatch.setattr(rag_service.rag_config, "RAG_ONTOLOGY_CANDIDATES_ENABLED", False)
    monkeypatch.setattr(rag_service.rag_config, "RAG_STRUCTURED_RETRIEVAL_ENABLED", True)
    monkeypatch.setattr(
        rag_service, "_execute_ontology_shadow",
        lambda _request_id, _session_id, query, route: (
            calls.append((query, route))
            or OntologyShadowResult((), (), ("rules:2024", "courses:other"), 2)
        ),
    )
    kwargs = {
        "raw_query": "2024학번 졸업 기준 알려줘",
        "query_for_retrieval": "2024학번 졸업 기준 알려줘",
        "analysis_meta": rag_service.QueryAnalysisMeta(result=None),
        "request_id": "plan-test",
        "session_id": "plan-test-session",
        "stage_timings": {},
    }

    plan = asyncio.run(rag_service._plan_retrieval(**kwargs))

    assert plan.route == ["rules"]
    assert plan.strategy.mode == "structured"
    assert plan.structured_document_keys_by_dataset == {"rules": ("rules:2024",)}
    assert calls == [(kwargs["raw_query"], ["rules"])]

    monkeypatch.setattr(rag_service.rag_config, "RAG_STRUCTURED_RETRIEVAL_ENABLED", False)
    hybrid = asyncio.run(rag_service._plan_retrieval(**kwargs))
    assert hybrid.strategy.mode == "hybrid"
    assert hybrid.structured_document_keys_by_dataset == {}
    assert len(calls) == 1


def _chunks(dataset: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "chunk_id": "right",
                "document_key": f"{dataset}:right",
                "title": "컴퓨터·AI학부 학사 담당" if dataset == "staff" else "2022학번 졸업기준",
                "chunk_text": "학사 담당 전화 02-1234-5678" if dataset == "staff" else "2022학번 졸업 기준",
                "staff_phone": "02-1234-5678",
                "position": 0,
                "major": "컴퓨터·AI학부",
            },
            {
                "chunk_id": "wrong",
                "document_key": f"{dataset}:wrong",
                "title": "다른 대상의 문서",
                "chunk_text": "의미만 비슷한 다른 학번 또는 학과",
                "staff_phone": "",
                "position": 0,
                "major": "다른학과",
            },
        ]
    )


def test_staff_sql_candidate_precedes_vector_and_keyword(monkeypatch):
    chunks = _chunks("staff")
    monkeypatch.setattr(rag_service, "_ensure_dataset", lambda _: (chunks, None, None, None))
    monkeypatch.setattr(
        rag_service,
        "hybrid_search_with_meta",
        lambda **_: (_ for _ in ()).throw(AssertionError("hybrid search must not run")),
    )

    frames, _, unavailable = asyncio.run(rag_service._retrieve_frames(
        route=["staff"], query="컴퓨터·AI학부 전화번호",
        final_where_filter={}, notice_board_filter=None, date_filter=None,
        entry_year=None, request_id="structured-staff",
        structured_document_keys_by_dataset={"staff": ("staff:right",)},
    ))

    assert unavailable == []
    assert frames[0]["chunk_id"].tolist() == ["right"]
    assert frames[0]["structured_match"].tolist() == [1]


def test_rules_sql_scope_excludes_similar_wrong_year_without_vector_search(monkeypatch):
    chunks = _chunks("rules")
    chunks["doc_id"] = chunks.pop("document_key")
    monkeypatch.setattr(rag_service, "_ensure_dataset", lambda _: (chunks, None, None, None))
    monkeypatch.setattr(
        rag_service,
        "hybrid_search_with_meta",
        lambda **_: (_ for _ in ()).throw(AssertionError("vector search must not run")),
    )

    frames, _, _ = asyncio.run(rag_service._retrieve_frames(
        route=["rules"], query="22학번 통계학과 졸업요건",
        final_where_filter={}, notice_board_filter=None, date_filter=None,
        entry_year=2022, request_id="structured-rules",
        structured_document_keys_by_dataset={"rules": ("rules:right",)},
    ))

    assert set(frames[0]["doc_id"]) == {"rules:right"}
    assert frames[0]["structured_match"].eq(1).all()


def test_rule_passages_are_reranked_with_lexical_scores_inside_sql_scope(monkeypatch):
    chunks = _chunks("rules").iloc[[0]].copy()
    chunks = pd.concat([
        chunks.assign(chunk_id="first", position=0, chunk_text="일반 안내"),
        chunks.assign(chunk_id="answer", position=1, chunk_text="졸업 최저이수학점 기준표"),
    ], ignore_index=True)
    hits = rag_service._ontology_candidate_hits(
        chunks_df=chunks, dataset="rules", document_keys=("rules:right",),
        query="22학번 졸업학점", where_filter=None, date_filter=None,
        chunks_per_document=0,
    )
    class Matrix:
        shape = (2, 1)
    monkeypatch.setattr(rag_service, "score_lexical_query", lambda *_: [0.1, 0.9])

    ranked = rag_service._rank_structured_rule_hits(
        hits, chunks_df=chunks, vectorizer=object(), matrix=Matrix(),
        tfidf_chunk_ids=["first", "answer"], query="22학번 졸업학점",
    )

    assert ranked["chunk_id"].tolist()[0] == "answer"


def test_sql_candidate_filtered_out_falls_back_to_hybrid(monkeypatch):
    chunks = _chunks("courses")
    monkeypatch.setattr(rag_service, "_ensure_dataset", lambda _: (chunks, None, None, None))
    hybrid = chunks.iloc[[1]].copy()
    hybrid["hybrid_score"] = 0.7
    called = []
    monkeypatch.setattr(rag_service, "hybrid_search_with_meta", lambda **_: called.append(True) or hybrid)

    frames, _, _ = asyncio.run(rag_service._retrieve_frames(
        route=["courses"], query="CSC2007 개설학과",
        final_where_filter={"major": {"$eq": "다른학과"}},
        notice_board_filter=None, date_filter=None, entry_year=None,
        request_id="structured-fallback",
        structured_document_keys_by_dataset={"courses": ("courses:right",)},
    ))

    assert called == [True]
    assert frames[0]["chunk_id"].tolist() == ["wrong"]


def test_successful_sql_scope_does_not_repeat_unrestricted_query_expansion(monkeypatch):
    calls = []

    async def retrieve(**kwargs):
        calls.append(kwargs["query"])
        assert kwargs["query"] == "22학번 통계학과 졸업요건"
        return [pd.DataFrame([{
            "chunk_id": "scoped",
            "dataset": "rules",
            "structured_match": 1,
        }])], False, []

    monkeypatch.setattr(rag_service, "_retrieve_frames", retrieve)
    frames, _, _ = asyncio.run(rag_service._retrieve_frames_for_queries(
        route=["rules"],
        queries=["22학번 통계학과 졸업요건", "통계학과 졸업 학점"],
        final_where_filter={}, notice_board_filter=None, date_filter=None,
        entry_year=2022, request_id="structured-single-pass",
        structured_document_keys_by_dataset={"rules": ("rules:right",)},
    ))

    assert calls == ["22학번 통계학과 졸업요건"]
    assert len(frames) == 1
