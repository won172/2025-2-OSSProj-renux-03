"""Entity-Relation 지식 그래프 구축 및 서브그래프 탐색 테스트 스크립트"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# 상위 경로 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.v2.graph_store import get_graph_db_connection, init_graph_tables, populate_entity_graph, search_entity_graph


def main():
    print("=" * 60)
    print("🚀 [Phase 2 - Task 2.1] Entity-Relation 지식 그래프 구축 및 탐색 테스트")
    print("=" * 60)

    conn = get_graph_db_connection()

    # 1. 테이블 초기화
    print("\n1. 지식 그래프 스키마 초기화 중...")
    init_graph_tables(conn)

    # 2. 그래프 구축
    print("\n2. 원본 DB로부터 엔티티 및 관계 추출 중...")
    n_nodes, n_edges = populate_entity_graph(conn)
    print(f"✅ 총 {n_nodes}개 노드(Entities) 및 {n_edges}개 간선(Relations) 구축 완료!")

    # 3. 서브그래프 2-Hop 탐색 테스트
    test_keywords = [
        "경영학과",
        "통계학과",
        "학사경고",
        "장학금"
    ]

    print("\n3. 키워드 기반 옵시디언 2-Hop 서브그래프 탐색:")
    print("-" * 60)

    for i, kw in enumerate(test_keywords, 1):
        t0 = time.time()
        subgraph = search_entity_graph(conn, keyword=kw, max_hops=2)
        t_ms = (time.time() - t0) * 1000

        nodes = subgraph["nodes"]
        edges = subgraph["edges"]
        print(f"\n[탐색 {i}] 키워드: \"{kw}\"  (소요시간: {t_ms:.2f}ms)")
        print(f"  - 추출된 노드 수: {len(nodes)}개, 연결 간선 수: {len(edges)}개")

        if nodes:
            sample_nodes = [f"{n['name']}({n['type']})" for n in nodes[:5]]
            print(f"  - 주요 관련 노드: {', '.join(sample_nodes)}")
        if edges:
            sample_edges = [f"{e['source']} --[{e['relation']}]--> {e['target']}" for e in edges[:3]]
            print(f"  - 주요 관계 경로: {sample_edges[0]}")

    print("\n" + "=" * 60)
    print("✅ Task 2.1 Entity-Relation 지식 그래프 구축 검증 완료!")
    print("=" * 60)


if __name__ == "__main__":
    main()
