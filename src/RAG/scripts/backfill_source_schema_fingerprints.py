#!/usr/bin/env python3
"""Establish structural baselines from the currently accepted source snapshots."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import DATA_SOURCES  # noqa: E402
from src.database import SessionLocal, init_db  # noqa: E402
from src.services.source_schema import (  # noqa: E402
    fingerprint_dataframe,
    fingerprint_tabular_file,
    observe_source_structures,
)
from src.services.staff_refresh import load_current_staff_frame  # noqa: E402
from src.crawlers.dongguk_department_curriculum_content import (  # noqa: E402
    find_curated_curriculum_workbook,
)


def _csv(key: str) -> pd.DataFrame:
    path = DATA_SOURCES[key]
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path).fillna("")


def backfill() -> dict[str, object]:
    init_db()
    session = SessionLocal()
    try:
        rules = _csv("rules")
        if "source_type" in rules.columns:
            official = rules[rules["source_type"].astype(str) == "official_rule_web"].copy()
            if not official.empty:
                rules = official
        course_structures = fingerprint_tabular_file(
            DATA_SOURCES["courses_all"],
            source_name="courses_all",
        )
        workbook = find_curated_curriculum_workbook()
        if workbook is not None:
            course_structures.extend(
                fingerprint_tabular_file(
                    workbook,
                    source_name="curriculum_links",
                )
            )
        structures = {
            "notices": [
                fingerprint_dataframe(
                    _csv("notices"),
                    source_name="notice_boards",
                    source_format="html_projection",
                )
            ],
            "rules": [
                fingerprint_dataframe(
                    rules,
                    source_name="official_rules",
                    source_format="html_pdf_projection",
                )
            ],
            "schedule": [
                fingerprint_dataframe(
                    _csv("schedule"),
                    source_name="official_schedule_projection",
                    source_format="html_projection",
                )
            ],
            "courses": course_structures,
            "staff": [
                fingerprint_dataframe(
                    load_current_staff_frame(session),
                    source_name="staff_api",
                    source_format="json",
                )
            ],
            "meals": [
                fingerprint_dataframe(
                    _csv("meals"),
                    source_name="dining_api",
                    source_format="json",
                )
            ],
        }
        results = {
            dataset: observe_source_structures(
                session,
                dataset=dataset,
                ingestion_run_id=None,
                structures=items,
            )
            for dataset, items in structures.items()
        }
        session.commit()
        return {"schema_version": 1, "datasets": results}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> int:
    print(json.dumps(backfill(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
