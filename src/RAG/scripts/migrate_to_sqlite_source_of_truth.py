"""Migrate legacy RAG documents to SQLite and rebuild only derived indexes.

CSV and JSON are accepted strictly as legacy input during this command.  After
it succeeds, normal collectors and reindex operations use SQLite exclusively.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parents[1]
if str(RAG_ROOT) not in sys.path:
    sys.path.insert(0, str(RAG_ROOT))

from src.database import init_db  # noqa: E402
from src.pipelines.ingest import (  # noqa: E402
    backfill_static_source_documents,
    ingest_meals,
    normalize_existing_meal_documents,
    reindex_from_db,
)
from src.pipelines.notices_sync import (  # noqa: E402
    migrate_legacy_notice_payloads,
    rebuild_notices_from_source_documents,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-notices", action="store_true")
    parser.add_argument("--skip-meals", action="store_true")
    parser.add_argument("--reindex-static", action="store_true", help="rules/schedule/courses/staff 파생 인덱스도 SQLite에서 재생성")
    args = parser.parse_args()

    init_db()
    if not args.skip_notices:
        summary = migrate_legacy_notice_payloads()
        print(f"notices payload migration: {summary}")
        chunks, _, _ = rebuild_notices_from_source_documents()
        print(f"notices SQLite rebuild: {len(chunks)} chunks")
    if not args.skip_meals:
        chunks, _, _ = ingest_meals()
        print(f"meals SQLite bootstrap/rebuild: {len(chunks)} chunks")
    print(f"meals canonical payload upgrades: {normalize_existing_meal_documents()}")
    static_summary = backfill_static_source_documents()
    print(f"static canonical payloads: {static_summary}")
    if args.reindex_static:
        for dataset in ("rules", "schedule", "courses", "staff"):
            result = reindex_from_db(dataset).get(dataset)
            print(f"{dataset} SQLite rebuild: {0 if result is None else len(result[0])} chunks")


if __name__ == "__main__":
    main()
