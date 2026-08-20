"""SQLite 정본 데이터를 바탕으로 Chroma 인덱스를 재구축하는 CLI 헬퍼입니다.

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
    backfill_static_source_documents,
    ingest_courses,
    ingest_meals,
    ingest_notices,
    ingest_rules,
    ingest_schedule,
    ingest_staff,
    reindex_from_db,
)
from src.database import init_db
from src.pipelines.notices_sync import (
    migrate_legacy_notice_payloads,
    rebuild_notices_from_source_documents,
)

ALL_LOADERS: dict = {
    "notices": ingest_notices,
    "rules": ingest_rules,
    "schedule": ingest_schedule,
    "courses": ingest_courses,
    "staff": ingest_staff,
    "meals": ingest_meals,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite 정본에서 Chroma/TF-IDF 인덱스 재구축")
    parser.add_argument(
        "--datasets",
        nargs="+",
        choices=list(ALL_LOADERS),
        metavar="DATASET",
        help=f"재구축할 데이터셋 (기본: 전체). 선택지: {', '.join(ALL_LOADERS)}",
    )
    parser.add_argument(
        "--import-legacy-csv",
        action="store_true",
        help="비어 있는 SQLite를 기존 CSV로 최초 이관할 때만 사용합니다.",
    )
    args = parser.parse_args()

    targets = args.datasets if args.datasets else list(ALL_LOADERS)
    init_db()
    if not args.import_legacy_csv:
        static_targets = [key for key in targets if key in {"rules", "schedule", "courses", "staff"}]
        if static_targets:
            print(f"▶ 정적 정본 payload 확인: {backfill_static_source_documents(static_targets)}")
        results = {}
        for key in targets:
            if key == "notices":
                # Notices have a normalized source-document layer, so rebuild
                # chunks from that SQLite payload rather than retaining a stale
                # `chunks` table or a historical CSV snapshot.
                summary = migrate_legacy_notice_payloads()
                print(f"▶ notices SQLite payload 확인: {summary}")
                results[key] = rebuild_notices_from_source_documents()
            elif key == "meals":
                # `ingest_meals()` reloads SourceDocument rows after an
                # optional one-time legacy bootstrap.
                results[key] = ingest_meals()
            else:
                result = reindex_from_db(key).get(key)
                if result is not None:
                    results[key] = result
        for key, (chunks_df, _, _) in results.items():
            print(f"✅ {key}: {len(chunks_df)} chunks indexed from SQLite.")
        missing = [key for key in targets if key not in results]
        if missing:
            print(f"⚠️ SQLite에 색인 가능한 데이터가 없는 항목: {', '.join(missing)}. 최초 이관은 --import-legacy-csv를 사용하세요.")
        return

    for key in targets:
        loader = ALL_LOADERS[key]
        print(f"▶ {key} 기존 CSV 이관 및 인덱싱 중...")
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
