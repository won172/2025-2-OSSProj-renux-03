"""SQLite FTS5 성능 및 작동 검증 스크립트"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# 상위 경로 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.v2.fts_search import get_fts_db_connection, init_fts_table, populate_fts_index, search_fts


def main():
    print("=" * 60)
    print("🚀 [Phase 1 - Task 1.1] SQLite FTS5 검색 엔진 검증 테스트")
    print("=" * 60)

    conn = get_fts_db_connection()
    
    # 1. FTS5 테이블 초기화
    print("\n1. FTS5 테이블 초기화 중...")
    init_fts_table(conn)
    
    # 2. FTS5 색인 구축
    print("\n2. 원본 Chunks FTS5 색인 구축 중...")
    count = populate_fts_index(conn)
    print(f"✅ 총 {count}개 데이터 FTS5 색인 구축 완료!")

    # 3. 샘플 검색 테스트
    test_queries = [
        "학사경고 장학금 불이익",
        "수강신청 취소 기한",
        "경영학과 졸업요건 학점",
        "교직원 연락처 통계학과",
        "학생식당 학식 메뉴"
    ]

    print("\n3. 초고속 FTS5 BM25 검색 속도 및 결과 측정:")
    print("-" * 60)
    
    total_latency_ms = 0.0
    for i, q in enumerate(test_queries, 1):
        t0 = time.time()
        results = search_fts(conn, query=q, top_k=3)
        t_ms = (time.time() - t0) * 1000
        total_latency_ms += t_ms

        print(f"\n[질의 {i}] \"{q}\"  (소요시간: {t_ms:.2f}ms)")
        if not results:
            print("  ⚠️ 검색 결과 없음")
        else:
            for rank, r in enumerate(results, 1):
                preview = r["content"].replace("\n", " ")[:70]
                print(f"  - [{rank}] (점수: {r['score']:.4f}) [{r['dataset_type']}] {preview}...")

    avg_ms = total_latency_ms / len(test_queries)
    print("\n" + "=" * 60)
    print(f"📊 평균 검색 소요 시간: {avg_ms:.2f}ms")
    print("✅ Task 1.1 FTS5 엔진 검증 완료!")
    print("=" * 60)


if __name__ == "__main__":
    main()
