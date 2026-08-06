"""Obsidian Vault 내보내기 검증 테스트 스크립트"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# 상위 경로 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.v2.obsidian_exporter import export_obsidian_vault, DEFAULT_VAULT_DIR


def main():
    print("=" * 60)
    print("🚀 [Phase 2 - Task 2.2] Obsidian Vault Markdown 내보내기 테스트")
    print("=" * 60)

    print(f"\n1. 내보내기 타겟 디렉터리: {DEFAULT_VAULT_DIR}")

    t0 = time.time()
    stats = export_obsidian_vault()
    t_ms = (time.time() - t0) * 1000

    print(f"\n2. 내보내기 성공! (소요시간: {t_ms:.2f}ms)")
    print("-" * 60)
    print(f"  - 🏛️ 생성된 학과(Departments) 문서 수: {stats['departments']}개")
    print(f"  - 📖 생성된 교과목(Courses) 문서 수:     {stats['courses']}개")
    print(f"  - 📜 생성된 학칙/규정(Rules) 문서 수:     {stats['rules']}개")
    print(f"  - 💡 생성된 주요개념(Concepts) 문서 수:   {stats['concepts']}개")

    # 생성된 샘플 문서 내용 출력
    sample_dept = DEFAULT_VAULT_DIR / "Departments" / "경영학과.md"
    if sample_dept.exists():
        print("\n3. 생성된 샘플 문서 (`Departments/경영학과.md`) 미리보기:")
        print("=" * 60)
        with open(sample_dept, "r", encoding="utf-8") as f:
            print(f.read()[:600])

    print("=" * 60)
    print("✅ Task 2.2 Obsidian Vault Exporter 검증 완료!")
    print("=" * 60)


if __name__ == "__main__":
    main()
