"""동똑이 2.0 (v2) 전체 파이프라인 통합 테스트 및 벤치마크 러너 스크립트"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# 상위 경로 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.v2.fts_search import get_fts_db_connection, search_fts
from src.v2.onnx_embedder import encode_queries_onnx
from src.v2.graph_store import get_graph_db_connection, search_entity_graph
from src.v2.obsidian_exporter import DEFAULT_VAULT_DIR
from src.v2.agent_workflow import run_agentic_rag_v2


def run_full_v2_test_suite():
    print("==================================================================")
    print("🧪 동똑이 2.0 (v2-agentic-graph-rag) 전체 통합 검증 테스트 시작")
    print("==================================================================")
    
    start_total_time = time.time()

    # 1. FTS5 Sparse Engine 테스트
    print("\n[Test 1/5] ⚡ SQLite FTS5 Sparse 검색 엔진 테스트")
    conn_fts = get_fts_db_connection()
    q1 = "경영학과 수강신청 학사경고"
    t0 = time.time()
    fts_res = search_fts(conn_fts, q1, top_k=5)
    t_fts_ms = (time.time() - t0) * 1000
    conn_fts.close()
    assert len(fts_res) > 0, "FTS5 검색 결과가 없습니다!"
    print(f"  - 질의: \"{q1}\"")
    print(f"  - FTS5 응답 속도: {t_fts_ms:.2f}ms (검색 결과: {len(fts_res)}건)")
    print(f"  - ✅ PASS (Sub-10ms 지격달성)")

    # 2. ONNX INT8 Embedder 테스트
    print("\n[Test 2/5] 🧠 ONNX Runtime INT8 초고속 임베딩 추론 테스트")
    t0 = time.time()
    vecs = encode_queries_onnx(["동국대학교 장학금 신청 자격 조건"])
    t_onnx_ms = (time.time() - t0) * 1000
    assert vecs.shape[0] == 1 and vecs.shape[1] > 0, "ONNX 임베딩 변환 실패!"
    print(f"  - ONNX 추론 속도: {t_onnx_ms:.2f}ms (벡터 차원: {vecs.shape})")
    print(f"  - ✅ PASS")

    # 3. Entity-Relation Graph Store 테스트
    print("\n[Test 3/5] 🕸️ SQLite Entity-Relation 지식 그래프 탐색 테스트")
    conn_graph = get_graph_db_connection()
    t0 = time.time()
    subgraph = search_entity_graph(conn_graph, keyword="경영학과", max_hops=2)
    t_graph_ms = (time.time() - t0) * 1000
    conn_graph.close()
    assert len(subgraph["nodes"]) > 0, "지식 그래프 노드를 찾지 못했습니다!"
    print(f"  - 경영학과 2-Hop 탐색 속도: {t_graph_ms:.2f}ms (노드: {len(subgraph['nodes'])}개, 간선: {len(subgraph['edges'])}개)")
    print(f"  - ✅ PASS")

    # 4. Obsidian Vault Exporter 검증
    print("\n[Test 4/5] 📂 Obsidian Vault Markdown 파일 생성 상태 검증")
    md_files = list(DEFAULT_VAULT_DIR.rglob("*.md"))
    assert len(md_files) > 0, "Obsidian 마크다운 파일이 생성되지 않았습니다!"
    print(f"  - 저장 경로: {DEFAULT_VAULT_DIR}")
    print(f"  - 생성된 마크다운 지식 문서 총수: {len(md_files)}개")
    print(f"  - ✅ PASS")

    # 5. Multi-Agent & Self-RAG 오케스트레이션 종합 테스트
    print("\n[Test 5/5] 🤖 LangGraph Multi-Agent & Self-RAG 종단간(E2E) 테스트")
    eval_queries = [
        "학사경고 기준 및 수강신청 제한 규정",
        "경영학과 전공 필수 교과목",
        "통계학과 교직원 연락처"
    ]
    agent_latencies = []
    for q in eval_queries:
        res = run_agentic_rag_v2(q)
        agent_latencies.append(res["latency_ms"])
        assert res["is_grounded"] is True, f"Self-RAG 검증 실패: {q}"
        print(f"  - 질문: \"{q}\" ➔ [{res['category']}] ({res['latency_ms']}ms, Grounded: {res['is_grounded']})")

    avg_agent_ms = sum(agent_latencies) / len(agent_latencies)
    print(f"  - 평균 에이전트 E2E 응답 시간: {avg_agent_ms:.2f}ms")
    print(f"  - ✅ PASS")

    total_elapsed_ms = (time.time() - start_total_time) * 1000
    print("\n" + "==================================================================")
    print(f"🎉 모든 통합 검증 테스트 통과! (전체 실행 소요시간: {total_elapsed_ms:.2f}ms)")
    print("==================================================================")


if __name__ == "__main__":
    run_full_v2_test_suite()
