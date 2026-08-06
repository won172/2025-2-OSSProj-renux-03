"""v2 FastAPI 엔드포인트 HTTP 검증 테스트 스크립트"""
from __future__ import annotations

import sys
from pathlib import Path

# 상위 경로 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from api.rag_service import app


def main():
    print("=" * 60)
    print("🚀 [Phase 4] FastAPI v2 API 엔드포인트 HTTP 테스트")
    print("=" * 60)

    client = TestClient(app)

    # 1. GET /api/v2/vault/stats
    print("\n1. GET /api/v2/vault/stats 테스트...")
    res1 = client.get("/api/v2/vault/stats")
    assert res1.status_code == 200, f"HTTP Error: {res1.status_code}"
    data1 = res1.json()
    print(f"  - Status: {data1['status']}")
    print(f"  - Total Documents: {data1['total_documents']}개")
    print("  - ✅ PASS")

    # 2. GET /api/v2/graph?keyword=경영학과
    print("\n2. GET /api/v2/graph?keyword=경영학과&max_hops=2 테스트...")
    res2 = client.get("/api/v2/graph", params={"keyword": "경영학과", "max_hops": 2})
    assert res2.status_code == 200, f"HTTP Error: {res2.status_code}"
    data2 = res2.json()
    print(f"  - Keyword: {data2['keyword']}")
    print(f"  - Nodes Count: {len(data2['nodes'])}개")
    print(f"  - Edges Count: {len(data2['edges'])}개")
    print("  - ✅ PASS")

    # 3. POST /api/v2/chat
    print("\n3. POST /api/v2/chat 테스트...")
    res3 = client.post("/api/v2/chat", json={"question": "경영학과 졸업요건 학점 알려줘"})
    assert res3.status_code == 200, f"HTTP Error: {res3.status_code}"
    data3 = res3.json()
    print(f"  - Category: [{data3['category']}]")
    print(f"  - Latency: {data3['latency_ms']}ms")
    print(f"  - Grounded: {data3['is_grounded']}")
    print("  - ✅ PASS")

    print("\n" + "=" * 60)
    print("🎉 FastAPI v2 API 엔드포인트 검증 100% 성공!")
    print("=" * 60)


if __name__ == "__main__":
    main()
