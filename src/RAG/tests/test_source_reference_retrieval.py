"""Synthetic source re-references exercise the real planner and evidence filters."""
import json
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api import rag_service as service
from src.database import Base, SourceDocument
from src.services import langchain_chat as chat
from src.services import query_analysis
from src.services.conversation import rewrite_with_context
from src.services.grounding import GroundingResult
from tests.test_conversation_continuity import install_local_history, run_api


AS_OF = date(2026, 10, 17)
TITLE = "응용수리학과 - 담당X"
QUERY = "[문서2] 자세히 알려줘"
ORIGINAL = "응용수리학과 사무실 연락처 알려줘"


def rows_for(dataset="staff"):
    return pd.DataFrame([
        {"dataset": dataset, "doc_id": f"{dataset}:synthetic:{name}",
         "chunk_id": f"synthetic-{name}", "title": TITLE,
         "chunk_text": f"CURRENT {name} evidence", "position": 0,
         "url": f"https://www.dongguk.edu/synthetic/{name}",
         "campus_scope": "seoul", "audience": "common",
         "visibility": "public", "department": "응용수리학과",
         "hybrid_score": 0.9, "vector_score": 0.9, "sparse_score": 0.9}
        for name in ("alpha", "beta")
    ])


@pytest.fixture
def reference_case(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'canonical.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(service, "SessionLocal", factory)
    frame = rows_for()
    with factory() as session:
        for row in frame.to_dict("records"):
            session.add(SourceDocument(
                dataset=row["dataset"], source_type="synthetic", source_id=row["doc_id"],
                document_key=row["doc_id"], title=row["title"], status="active",
                normalized_payload_json=json.dumps(row), source_url=row["url"]))
        session.commit()
    store = install_local_history(monkeypatch)
    sources = [{"source": row["dataset"], "title": row["title"],
                "chunk_id": row["chunk_id"], "source_ref": f"sha256:synthetic-{index}",
                "metadata": {"doc_id": row["doc_id"], "campus_scope": "seoul"},
                "citation_number": index, "snippet": "OUTDATED MEMORY"}
               for index, row in enumerate(frame.to_dict("records"), 1)]
    chat.append_manual_history("a", ORIGINAL, "OUTDATED ANSWER", sources)
    monkeypatch.setattr(service, "_request_temporal_context", lambda _req: service.TemporalContext(
        as_of=AS_OF, academic_year=2026, semester=2, phase="학기중"))
    monkeypatch.setattr(service, "_chat_course_recommendation", lambda *_a: None)
    monkeypatch.setattr(service, "_try_direct_answer", lambda *_a: None)
    monkeypatch.setattr(service, "_try_future_unannounced_answer", lambda *_a: None)
    monkeypatch.setattr(service, "RAG_SEMANTIC_CACHE_ENABLED", False)
    monkeypatch.setattr(service.rag_config, "RAG_STRUCTURED_RETRIEVAL_ENABLED", False)
    monkeypatch.setattr(service.rag_config, "RAG_ONTOLOGY_SHADOW_ENABLED", False)
    monkeypatch.setattr(service.rag_config, "RAG_ONTOLOGY_CANDIDATES_ENABLED", False)
    monkeypatch.setattr(service, "_ensure_dataset", lambda _dataset: (frame.copy(), None, None, None))
    monkeypatch.setattr(service, "_prime_query_embedding", lambda *_a: False)
    monkeypatch.setattr(service, "hybrid_search_with_meta", lambda **_kw: frame.copy())
    monkeypatch.setattr(service, "_save_rag_evaluation_log", lambda *_a, **_kw: None)
    monkeypatch.setattr(service, "_update_grounding_log", lambda *_a: None)
    monkeypatch.setattr(service, "_update_observability_log", lambda *_a: None)
    monkeypatch.setattr(service, "_apply_cross_encoder_rerank", lambda merged, _q: merged)
    monkeypatch.setattr(service, "RAG_GROUNDING_CHECK_ENABLED", True)
    generated = []
    async def generate(**kw):
        generated.append(kw)
        assert "OUTDATED" not in kw["context"]
        assert "CURRENT beta evidence" in kw["context"]
        assert "CURRENT alpha evidence" not in kw["context"]
        return "CURRENT beta evidence [문서1]"
    async def stream(**kw):
        yield await generate(**kw)
    async def check(*args, **_kw):
        assert "CURRENT beta evidence" in args[2] and "CURRENT alpha evidence" not in args[2]
        return GroundingResult(checked=True, grounded=True, score=1.0, reason="synthetic", status="passed")
    async def select(_q, shortlist, *_a, **_kw):
        shortlist = shortlist.copy()
        shortlist["evidence_group"] = 1
        shortlist["citation_number"] = 1
        return shortlist, False
    monkeypatch.setattr(service, "generate_langchain_answer", generate)
    monkeypatch.setattr(service, "generate_langchain_answer_stream", stream)
    monkeypatch.setattr(service, "check_answer_grounding", check)
    monkeypatch.setattr(service, "_select_answer_evidence", select)
    yield SimpleNamespace(frame=frame, factory=factory, store=store, generated=generated)
    engine.dispose()


def analysis_mode(monkeypatch, mode):
    monkeypatch.setattr(service, "USE_QUERY_ANALYSIS", mode != "disabled")
    async def analyze(query, *_a, **_kw):
        return None if mode == "failed" else service.QueryAnalysisResult(
            normalized_question=query, intent="unknown")
    monkeypatch.setattr(service, "analyze_query", analyze)


async def plan(query=QUERY, major=None):
    return await service._plan_query(
        req=service.AskRequest(question=query, major=major), raw_query=query,
        temporal_context=service.TemporalContext(as_of=AS_OF, academic_year=2026, semester=2, phase="학기중"),
        request_id="synthetic", session_id="a",
        stage_timings={}, llm_usage=[], mode="ask")


def reference_hits(result):
    return service._retrieve_referenced_source_frames(
        reference=result.source_reference, query=result.query_for_retrieval,
        final_where_filter=result.filters["where"],
        notice_visibility_filter=result.filters["notice_visibility"],
        notice_board_filter=result.notice_board_filter, date_filter=result.filters["date"],
        entry_year=result.entry_year, allow_wise=result.allow_wise, as_of=result.filters["as_of"])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unknown", "failed", "disabled"])
async def test_masked_staff_reference_keeps_domain_and_exact_route(monkeypatch, reference_case, mode):
    analysis_mode(monkeypatch, mode)
    result = await plan()
    assert result.direct_handler is None
    assert result.route == ["staff"]
    assert result.structured_document_keys_by_dataset == {"staff": ("staff:synthetic:beta",)}
    assert QUERY in result.query_for_retrieval
    assert "연락처" in result.query_for_retrieval
    assert ORIGINAL not in result.query_for_retrieval


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ask", "stream"])
async def test_same_masked_title_citation2_uses_only_its_current_evidence(monkeypatch, reference_case, mode):
    analysis_mode(monkeypatch, "disabled")
    response = await run_api(mode, query=QUERY)
    if mode == "stream":
        assert sum(event["type"] == "completion" for event in response) == 1
        assert sum(event["type"] == "done" for event in response) == 1
        response = next(event for event in response if event["type"] == "completion")
    assert response["verification_status"] == "passed" and response["grounded"] is True
    assert len(response["sources"]) == 1
    assert response["sources"][0]["metadata"]["doc_id"] == "staff:synthetic:beta"
    assert response["sources"][0]["chunk_id"] == "synthetic-beta"
    assert response["sources"][0]["url"].endswith("/beta")
    assert reference_case.store.get("a").messages[-2].content == QUERY


@pytest.mark.asyncio
@pytest.mark.parametrize("analysis", ["unknown", "failed"])
@pytest.mark.parametrize("mode", ["ask", "stream"])
async def test_masked_reference_api_survives_analysis_fallback(monkeypatch, reference_case, analysis, mode):
    analysis_mode(monkeypatch, analysis)
    response = await run_api(mode, query=QUERY)
    if mode == "stream":
        response = next(event for event in response if event["type"] == "completion")
    assert response["grounded"] is True and response["verification_status"] == "passed"
    assert {source["metadata"]["doc_id"] for source in response["sources"]} == {"staff:synthetic:beta"}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ask", "stream"])
@pytest.mark.parametrize("state", ["missing", "hidden", "wise", "missing_chunk", "wrong_chunk_campus"])
async def test_unavailable_selected_source_never_uses_memory_or_lookalike(monkeypatch, reference_case, state, mode):
    analysis_mode(monkeypatch, "disabled")
    with reference_case.factory() as session:
        document = session.query(SourceDocument).filter_by(document_key="staff:synthetic:beta").one()
        if state == "missing":
            session.delete(document)
        elif state == "hidden":
            document.status = "hidden"
        elif state == "wise":
            payload = json.loads(document.normalized_payload_json)
            payload["campus_scope"] = "wise"
            document.normalized_payload_json = json.dumps(payload)
        session.commit()
    if state == "missing_chunk":
        reference_case.frame.drop(index=1, inplace=True)
    elif state == "wrong_chunk_campus":
        reference_case.frame.loc[1, "campus_scope"] = "wise"
    response = await run_api(mode, query=QUERY)
    if mode == "stream":
        assert next(event for event in response if event["type"] == "metadata")["fallback_triggered"]
        response = next(event for event in response if event["type"] == "completion")
    else:
        assert response["fallback_triggered"]
        assert "OUTDATED" not in response["answer"]
    assert response["sources"] == []
    assert response["fallback_reason"] != "out_of_domain"
    assert not reference_case.generated


def change_dataset(case, dataset):
    case.frame["dataset"] = dataset
    case.frame["doc_id"] = [f"{dataset}:synthetic:{name}" for name in ("alpha", "beta")]
    with case.factory() as session:
        for row in case.frame.to_dict("records"):
            session.add(SourceDocument(
                dataset=dataset, source_type="synthetic", source_id=row["doc_id"],
                document_key=row["doc_id"], title=row["title"], status="active",
                normalized_payload_json=json.dumps(row), source_url=row["url"]))
        session.commit()
    message = case.store.get("a").messages[-1]
    for cue in message.additional_kwargs["source_cues"]:
        cue["source"] = dataset
        cue["doc_id"] = cue["doc_id"].replace("staff:", f"{dataset}:")


@pytest.mark.asyncio
@pytest.mark.parametrize("dataset", ["staff", "rules", "notices", "courses", "schedule", "meals"])
@pytest.mark.parametrize("analysis", ["unknown", "failed", "disabled"])
async def test_all_supported_source_types_have_bounded_domain_and_route(monkeypatch, reference_case, dataset, analysis):
    if dataset != "staff":
        change_dataset(reference_case, dataset)
    analysis_mode(monkeypatch, analysis)
    result = await plan()
    assert result.direct_handler is None and result.route == [dataset]
    assert result.source_reference.dataset == dataset
    assert result.structured_document_keys_by_dataset == {dataset: (f"{dataset}:synthetic:beta",)}
    frames, _, unavailable = reference_hits(result)
    assert not unavailable
    assert {key for frame in frames for key in frame["doc_id"]} == {f"{dataset}:synthetic:beta"}


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["other", "staff injected", "STAFF"])
async def test_unrecognized_source_does_not_supply_domain_permission(monkeypatch, reference_case, source):
    analysis_mode(monkeypatch, "unknown")
    reference_case.store.get("a").messages[-1].additional_kwargs["source_cues"][1]["source"] = source
    result = await plan()
    assert result.direct_handler == "clarification"
    assert "학교 교직원 연락처" not in rewrite_with_context(QUERY, chat.get_recent_history_text("a"))


@pytest.mark.asyncio
async def test_new_topic_still_hits_original_out_of_domain_guard(monkeypatch, reference_case):
    analysis_mode(monkeypatch, "unknown")
    result = await plan("고흐에 대해 알려줘")
    assert result.direct_handler == "out_of_domain" and result.source_reference is None


@pytest.mark.asyncio
@pytest.mark.parametrize("major,allowed", [(None, False), ("타학과", False), ("응용수리학과", True)])
async def test_current_notice_visibility_uses_authenticated_major(monkeypatch, reference_case, major, allowed):
    change_dataset(reference_case, "notices")
    analysis_mode(monkeypatch, "unknown")
    # Artifacts still say public. Current canonical permissions are authoritative.
    with reference_case.factory() as session:
        document = session.query(SourceDocument).filter_by(document_key="notices:synthetic:beta").one()
        payload = json.loads(document.normalized_payload_json)
        payload["visibility"] = "department"
        document.normalized_payload_json = json.dumps(payload)
        session.commit()
    result = await plan(QUERY + " 응용수리학과 기준으로", major=major)
    assert result.filters["notice_visibility"] == service._notice_visibility_where_filter(major)
    frames, _, _ = reference_hits(result)
    assert bool(frames) is allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value,query", [
    ("title", "2025학년도 안내", QUERY + " 2026학년도 기준으로"),
    ("title", "2026학년도 1학기 안내", QUERY + " 2026학년도 2학기 기준으로"),
    ("entry_year", "2024", QUERY + " 2026학번 기준으로"),
    ("campus_scope", "bmc", QUERY + " 서울캠퍼스 기준으로"),
    ("audience", "graduate", QUERY + " 학부생 기준으로"),
])
async def test_reference_cannot_override_current_scope(monkeypatch, reference_case, field, value, query):
    change_dataset(reference_case, "notices")
    analysis_mode(monkeypatch, "disabled")
    reference_case.frame.loc[1, field] = value
    result = await plan(query)
    assert result.direct_handler is None
    frames, _, _ = reference_hits(result)
    assert frames == []


@pytest.mark.asyncio
async def test_saved_chunk_identity_alone_resolves_one_current_document(monkeypatch, reference_case):
    analysis_mode(monkeypatch, "unknown")
    cue = reference_case.store.get("a").messages[-1].additional_kwargs["source_cues"][1]
    cue.pop("doc_id")
    result = await plan()
    assert result.source_reference.document_key is None
    frames, _, _ = reference_hits(result)
    assert frames[0]["doc_id"].tolist() == ["staff:synthetic:beta"]
    reference_case.frame.loc[1, "chunk_id"] = "changed-chunk"
    assert reference_hits(result)[0] == []


def test_parent_context_does_not_reintroduce_unfiltered_siblings(monkeypatch):
    row = rows_for().iloc[1].copy()
    row["source_reference_match"] = 1
    sibling = row.copy()
    sibling["position"] = 1
    sibling["chunk_text"] = "FORBIDDEN SIBLING"
    monkeypatch.setitem(service._datasets, "staff", SimpleNamespace(chunks=pd.DataFrame([row, sibling])))
    monkeypatch.setattr(service, "PARENT_CONTEXT_ENABLED", True)
    assert service._expand_chunk_with_neighbors(row) == "CURRENT beta evidence"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_real_analysis_adapter_unknown_and_exception_are_safe(monkeypatch, reference_case, failure):
    class Chain:
        async def ainvoke(self, payload):
            assert "OUTDATED" not in payload["history"]
            assert "synthetic:beta" in payload["history"] and "synthetic:alpha" not in payload["history"]
            if failure:
                raise RuntimeError("synthetic analysis failure")
            return {"raw": None, "parsed": query_analysis._QueryAnalysisOutput(
                normalized_question=QUERY, intent="unknown", entities=[], time_focus="none",
                search_queries=[], needs_clarification=False, clarification_reason=None,
                is_compound=False, sub_queries=[])}
    monkeypatch.setattr(query_analysis, "_build_analysis_chain", Chain)
    monkeypatch.setattr(service, "USE_QUERY_ANALYSIS", True)
    monkeypatch.setattr(service, "analyze_query", query_analysis.analyze_query)
    result = await plan()
    assert result.analysis_meta.failed is failure
    assert result.direct_handler is None and result.route == ["staff"]
    assert reference_hits(result)[0][0]["doc_id"].tolist() == ["staff:synthetic:beta"]


@pytest.mark.asyncio
async def test_real_crawled_notice_payload_without_visibility_remains_public(monkeypatch, reference_case):
    change_dataset(reference_case, "notices")
    analysis_mode(monkeypatch, "unknown")
    with reference_case.factory() as session:
        document = session.query(SourceDocument).filter_by(document_key="notices:synthetic:beta").one()
        document.source_type = "html_notice"
        payload = json.loads(document.normalized_payload_json)
        payload.pop("visibility")
        payload["board_name"] = "일반공지"
        document.normalized_payload_json = json.dumps(payload)
        session.commit()
    result = await plan()
    assert reference_hits(result)[0][0]["doc_id"].tolist() == ["notices:synthetic:beta"]


@pytest.mark.asyncio
async def test_canonical_course_department_name_uses_normal_major_filter(monkeypatch, reference_case):
    change_dataset(reference_case, "courses")
    analysis_mode(monkeypatch, "disabled")
    reference_case.frame["major"] = "응용수리학과"
    with reference_case.factory() as session:
        document = session.query(SourceDocument).filter_by(document_key="courses:synthetic:beta").one()
        payload = json.loads(document.normalized_payload_json)
        payload["department_name"] = "응용수리학과"
        document.normalized_payload_json = json.dumps(payload)
        session.commit()
    result = await plan(major="응용수리학과")
    assert result.filters["where"] == {"major": {"$eq": "응용수리학과"}}
    assert reference_hits(result)[0][0]["doc_id"].tolist() == ["courses:synthetic:beta"]


@pytest.mark.asyncio
@pytest.mark.parametrize("title,query", [
    ("2026-1 안내", QUERY + " 2026학년도 2학기 기준으로"),
    ("2026학년도 겨울 계절학기 안내", QUERY + " 2026학년도 2학기 기준으로"),
    ("2024학번 안내", QUERY + " 2026학번 기준으로"),
])
async def test_compact_seasonal_and_cohort_conflicts_are_terminal(monkeypatch, reference_case, title, query):
    analysis_mode(monkeypatch, "disabled")
    reference_case.frame.loc[1, "title"] = title
    result = await plan(query)
    assert reference_hits(result)[0] == []


@pytest.mark.asyncio
async def test_explicit_source_is_not_replaced_by_direct_answer_or_enrichment(monkeypatch, reference_case):
    analysis_mode(monkeypatch, "disabled")
    monkeypatch.setattr(service, "_try_direct_answer", lambda *_a: pytest.fail("must re-retrieve selected source"))
    monkeypatch.setattr(service, "_enrich_staff_lookup_frames", lambda **_kw: pytest.fail("must not add another document"))
    response = await run_api("ask", query=QUERY)
    assert response["grounded"] is True
    assert len(response["sources"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("analysis", ["unknown", "failed", "disabled"])
async def test_legacy_title_without_identity_cannot_answer_from_lookalike(monkeypatch, reference_case, analysis):
    analysis_mode(monkeypatch, analysis)
    reference_case.store.get("a").messages[-1].additional_kwargs["source_cues"] = [
        {"title": TITLE, "citation_number": 1}]
    monkeypatch.setattr(service, "hybrid_search_with_meta", lambda **_kw: pytest.fail("title alone is not an identity"))
    response = await run_api("ask", query="그 문서 자세히 알려줘")
    assert response["fallback_triggered"] and response["sources"] == []
    assert response["fallback_reason"] != "out_of_domain"
    assert not reference_case.generated


@pytest.mark.asyncio
async def test_named_source_with_department_preserves_exact_document(monkeypatch, reference_case):
    analysis_mode(monkeypatch, "unknown")
    cue = reference_case.store.get("a").messages[-1].additional_kwargs["source_cues"][1]
    cue["title"] += " 별도안내"
    result = await plan("아까 " + cue["title"] + " 자세히 알려줘")
    assert result.source_reference.document_key == "staff:synthetic:beta"
    assert reference_hits(result)[0][0]["doc_id"].tolist() == ["staff:synthetic:beta"]


@pytest.mark.asyncio
async def test_scope_correction_without_document_selection_does_not_pin_old_identity(monkeypatch, reference_case):
    analysis_mode(monkeypatch, "disabled")
    reference_case.store.get("a").messages[-1].additional_kwargs["source_cues"] = [
        reference_case.store.get("a").messages[-1].additional_kwargs["source_cues"][1]]
    result = await plan("아니 2026학번 기준으로")
    assert result.source_reference is None and not result.source_reference_required
    assert result.direct_handler is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ask", "stream"])
@pytest.mark.parametrize("tree,path,allowed", [
    ("서울캠퍼스 > 이과대학 > 응용수리학과", "서울캠퍼스 > 이과대학 > 응용수리학과", True),
    ("WISE캠퍼스 > 이과대학 > 응용수리학과", "WISE캠퍼스 > 이과대학 > 응용수리학과", False),
    ("응용수리학과", "WISE캠퍼스 > 이과대학 > 응용수리학과", False),
    ("WISE캠퍼스 > 이과대학 > 응용수리학과", "응용수리학과", False),
])
async def test_korean_canonical_staff_paths_override_stale_seoul_projection(
    monkeypatch, reference_case, mode, tree, path, allowed,
):
    analysis_mode(monkeypatch, "unknown")
    # Operational payload shape, entirely synthetic values: no English scope
    # metadata, and a generic canonical title/URL with no campus signal.
    payload = {
        "db_id": 2, "담당업무": "합성 안내", "부서경로": path, "성명": "담당X",
        "원천ID": "synthetic-beta", "이메일": "fixture@example.invalid",
        "전화번호": "000-000-0000", "조직(트리)": tree, "직위": "합성 담당",
    }
    with reference_case.factory() as session:
        document = session.query(SourceDocument).filter_by(document_key="staff:synthetic:beta").one()
        document.source_type = "staff_directory"
        document.normalized_payload_json = json.dumps(payload, ensure_ascii=False)
        session.commit()
    assert reference_case.frame["campus_scope"].tolist() == ["seoul", "seoul"]
    monkeypatch.setattr(service, "hybrid_search_with_meta", lambda **_kw: pytest.fail("no title/sibling fallback"))
    monkeypatch.setattr(service, "_enrich_staff_lookup_frames", lambda **_kw: pytest.fail("no sibling enrichment"))
    monkeypatch.setattr(service, "_try_direct_answer", lambda *_a: pytest.fail("no direct replacement"))
    result = await plan()
    assert result.direct_handler is None and result.route == ["staff"]
    frames, _, unavailable = reference_hits(result)
    assert not unavailable
    assert bool(frames) is allowed
    if allowed:
        assert frames[0]["doc_id"].tolist() == ["staff:synthetic:beta"]
    response = await run_api(mode, query=QUERY)
    if mode == "stream":
        assert sum(event["type"] == "completion" for event in response) == 1
        assert sum(event["type"] == "done" for event in response) == 1
        response = next(event for event in response if event["type"] == "completion")
    if allowed:
        assert response["grounded"] is True and response["verification_status"] == "passed"
        assert [source["metadata"]["doc_id"] for source in response["sources"]] == ["staff:synthetic:beta"]
    else:
        assert response["sources"] == [] and response["fallback_reason"] == "no_results"
        assert not reference_case.generated
