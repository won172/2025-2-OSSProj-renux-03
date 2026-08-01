"""공식 규정관리시스템 현행판을 보존적으로 병합하고 rules 인덱스를 갱신한다."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import DATA_SOURCES  # noqa: E402
from src.crawlers.dongguk_rule import (  # noqa: E402
    collect_official_academic_rules,
    merge_official_rule_versions,
)


def sync_rule_source(*, output_path: Path | None = None) -> pd.DataFrame:
    path = output_path or DATA_SOURCES["rules"]
    existing = (
        pd.read_csv(path).fillna("").astype(str)
        if path.exists()
        else pd.DataFrame()
    )
    official = collect_official_academic_rules()
    merged = merge_official_rule_versions(existing, official)
    comparison_columns = [
        "source_version",
        "published_at",
        "source_url",
        "title",
        "text",
    ]
    prior_official = existing[
        existing.get("source_type", pd.Series("", index=existing.index)).eq(
            "official_rule_web"
        )
    ].copy()
    expected = official.copy()
    for frame in (prior_official, expected):
        for column in comparison_columns:
            if column not in frame.columns:
                frame[column] = ""
    prior_records = prior_official[comparison_columns].sort_values(
        "source_version", kind="stable"
    ).reset_index(drop=True)
    expected_records = expected[comparison_columns].sort_values(
        "source_version", kind="stable"
    ).reset_index(drop=True)
    changed = not prior_records.equals(expected_records)
    if changed or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(path, index=False, encoding="utf-8-sig")
    official.attrs["source_changed"] = changed
    return official


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="CSV 정본만 갱신하고 SQLite/검색 인덱스는 건드리지 않는다.",
    )
    args = parser.parse_args()
    official = sync_rule_source()
    print(f"공식 현행 규정 {len(official)}건을 정본에 병합했습니다.")
    if args.source_only:
        return

    from src.database import init_db
    from src.pipelines.ingest import ingest_rules

    init_db()
    chunks, _, _ = ingest_rules(force_source_reload=True)
    print(f"rules 인덱스 {len(chunks)}개 청크를 재구축했습니다.")


if __name__ == "__main__":
    main()
