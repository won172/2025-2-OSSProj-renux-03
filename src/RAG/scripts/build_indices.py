"""CLI helper to rebuild the Chroma index from CSV data."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is on the import path when running as a script.
sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.pipelines.ingest import (
    ingest_courses,
    ingest_notices,
    ingest_rules,
    ingest_schedule,
)


def main() -> None:
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
