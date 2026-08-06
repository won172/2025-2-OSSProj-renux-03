"""Multi-Agent & Self-RAG 오케스트레이션 검증 테스트 스크립트"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# 상위 경로 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.v2.agent_workflow import run_agentic_rag_v2


def main():
    print("=" * 60)
    print("🚀 [Phase 3 - Task 3.1] Multi-Agent & Self-RAG 오케스트레이션 테스트")
    print("=" * 60)

    test_questions = [
        "경영학과 졸업요건 학점과 관련 학칙 알려줘",
        "수강신청 취소 마감일이 언제야?",
        "학사경고 받으면 장학금 탈락하는 기준이 어떻게 돼?",
        "통계학과 교직원 연락처"
    ]

    print("\n1. 자율 에이전트 루프 실행 및 검증:")
    print("-" * 60)

    for i, q in enumerate(test_questions, 1):
        result = run_agentic_rag_v2(q)

        print(f"\n[질문 {i}] \"{q}\"")
        print(f"  - 🎯 분류 카테고리: [{result['category']}]")
        print(f"  - ⚡ 총 소요시간:   {result['latency_ms']}ms")
        print(f"  - 🔍 검색 근거:     FTS5 {result['fts_docs_count']}건 / 지식 노드 {result['graph_nodes_count']}개")
        print("  - 📜 실행 트레이스:")
        for step in result["execution_trace"]:
            print(f"      • {step}")
            
        print("\n  - 🤖 생성된 최종 답변:")
        print("    " + result["answer"].replace("\n", "\n    "))
        print("-" * 60)

    print("\n" + "=" * 60)
    print("✅ Task 3.1 Multi-Agent & Self-RAG 검증 완료!")
    print("=" * 60)


if __name__ == "__main__":
    main()
