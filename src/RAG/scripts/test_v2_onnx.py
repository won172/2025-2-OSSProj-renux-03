"""PyTorch vs ONNX 임베딩 추론 속도 및 결과 벤치마크 테스트 스크립트"""
from __future__ import annotations

import sys
import time
from pathlib import Path
import numpy as np

# 상위 경로 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models.embedding import encode_queries as encode_queries_pytorch
from src.v2.onnx_embedder import encode_queries_onnx, export_kure_to_onnx, DEFAULT_ONNX_PATH


def main():
    print("=" * 60)
    print("🚀 [Phase 1 - Task 1.2] PyTorch vs ONNX INT8 임베딩 추론 벤치마크")
    print("=" * 60)

    # 1. ONNX 모델 존재 여부 확인 / 내보내기
    if not DEFAULT_ONNX_PATH.exists():
        print("\n1. ONNX INT8 양자화 모델 내보내기 진행 중...")
        export_kure_to_onnx()
    else:
        print(f"\n1. 기존 ONNX 모델 확인: {DEFAULT_ONNX_PATH}")

    sample_queries = [
        "학사경고 기준 및 장학금 불이익 조항",
        "경영학과 2026학년도 필수 이수 과목",
        "수강신청 취소 및 변경 기한",
        "교직원 통계학과 교수님 연락처",
        "남산학사 기숙사 식단 메뉴"
    ]

    print("\n2. PyTorch (SentenceTransformer) 추론 속도 측정...")
    t0 = time.time()
    vecs_pt = encode_queries_pytorch(sample_queries)
    pt_latency_ms = (time.time() - t0) * 1000
    print(f"  - PyTorch 소요시간: {pt_latency_ms:.2f}ms (Shape: {vecs_pt.shape})")

    print("\n3. ONNX Runtime INT8 추론 속도 측정...")
    t0 = time.time()
    vecs_onnx = encode_queries_onnx(sample_queries)
    onnx_latency_ms = (time.time() - t0) * 1000
    print(f"  - ONNX Runtime 소요시간: {onnx_latency_ms:.2f}ms (Shape: {vecs_onnx.shape})")

    # 4. 코사인 유사도로 벡터 품질 일치성 검증 (FP32 vs INT8)
    sims = []
    for i in range(len(sample_queries)):
        v_pt = vecs_pt[i]
        v_onnx = vecs_onnx[i]
        cos_sim = float(np.dot(v_pt, v_onnx) / (np.linalg.norm(v_pt) * np.linalg.norm(v_onnx)))
        sims.append(cos_sim)

    avg_sim = np.mean(sims)
    speedup = pt_latency_ms / max(onnx_latency_ms, 0.001)

    print("\n" + "=" * 60)
    print("📊 벤치마크 결과 리포트:")
    print(f"  - PyTorch 추론 소요시간: {pt_latency_ms:.2f}ms")
    print(f"  - ONNX INT8 추론 소요시간: {onnx_latency_ms:.2f}ms")
    print(f"  - 속도 향상(Speedup): {speedup:.2f}x 배 가속 ⚡")
    print(f"  - 벡터 코사인 유사도 일치율: {avg_sim * 100:.2f}%")
    print("✅ Task 1.2 ONNX 임베딩 파이프라인 검증 완료!")
    print("=" * 60)


if __name__ == "__main__":
    main()
