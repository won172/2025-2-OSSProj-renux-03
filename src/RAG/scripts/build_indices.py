"""CSV 데이터를 바탕으로 Chroma 인덱스를 재구축하는 CLI 헬퍼입니다."""
from __future__ import annotations

import sys
from pathlib import Path

# 스크립트로 실행할 때 프로젝트 루트를 import 경로에 올려 둔다.
sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.pipelines.ingest import (
    ingest_courses,
    ingest_notices,
    ingest_rules,
    ingest_schedule,
)
from src.database import init_db


def main() -> None:
    init_db()
    loaders = {
        "notices": ingest_notices,
        "rules": ingest_rules,
        "schedule": ingest_schedule,
        "courses": ingest_courses,
    }

    for key, loader in loaders.items():
        try:
            chunks_df, _, _ = loader()
        except FileNotFoundError as exc:
            print(f"⚠️ Skipped {key}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"❌ Failed {key}: {exc}")
            continue
        print(f"✅ {key}: {len(chunks_df)} chunks indexed to Chroma.")


if __name__ == "__main__":
    main()
