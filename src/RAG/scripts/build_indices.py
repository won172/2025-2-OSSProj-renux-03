"""CSV 데이터를 바탕으로 Chroma 인덱스를 재구축하는 CLI 헬퍼입니다.

사용법:
  python3 scripts/build_indices.py                     # 전체 재구축
  python3 scripts/build_indices.py --datasets rules    # rules만
  python3 scripts/build_indices.py --datasets rules notices  # 복수 지정
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 스크립트로 실행할 때 프로젝트 루트를 import 경로에 올려 둔다.
sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.pipelines.ingest import (
    ingest_courses,
    ingest_meals,
    ingest_notices,
    ingest_rules,
    ingest_schedule,
    ingest_staff,
)
from src.database import init_db

ALL_LOADERS: dict = {
    "notices": ingest_notices,
    "rules": ingest_rules,
    "schedule": ingest_schedule,
    "courses": ingest_courses,
    "staff": ingest_staff,
    "meals": ingest_meals,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Chroma 인덱스 재구축")
    parser.add_argument(
        "--datasets",
        nargs="+",
        choices=list(ALL_LOADERS),
        metavar="DATASET",
        help=f"재구축할 데이터셋 (기본: 전체). 선택지: {', '.join(ALL_LOADERS)}",
    )
    args = parser.parse_args()

    targets = args.datasets if args.datasets else list(ALL_LOADERS)
    loaders = {k: ALL_LOADERS[k] for k in targets}

    init_db()
    for key, loader in loaders.items():
        print(f"▶ {key} 인덱싱 중...")
        try:
            chunks_df, _, _ = loader()
        except FileNotFoundError as exc:
            print(f"⚠️  Skipped {key}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"❌ Failed {key}: {exc}")
            continue
        print(f"✅ {key}: {len(chunks_df)} chunks indexed to Chroma.")


if __name__ == "__main__":
    main()
