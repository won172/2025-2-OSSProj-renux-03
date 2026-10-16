"""Choose a persisted collection pointer before the RAG application imports.

This startup probe uses only the standard library.  It neither initializes
Chroma nor repairs/publishes any runtime data; the application's strict lineage
and readiness checks still decide whether the selected contents are usable.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys


POINTER_ENV = "RAG_COLLECTION_POINTER_FILE"


def _targets(pointer: Path) -> dict[str, str]:
    try:
        state = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"collection pointer is unreadable: {pointer}") from exc
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise ValueError(f"unsupported collection pointer schema: {pointer}")
    records = state.get("collections")
    if not isinstance(records, dict):
        raise ValueError(f"invalid collection pointer map: {pointer}")
    targets = {}
    for logical, record in records.items():
        if (
            not isinstance(logical, str)
            or not logical.strip()
            or not isinstance(record, dict)
            or not isinstance(record.get("active"), str)
            or not record["active"].strip()
        ):
            raise ValueError(f"invalid active collection pointer: {pointer}")
        targets[logical] = record["active"].strip()
    return targets


def _catalog_names(chroma_dir: Path) -> set[str]:
    catalog = chroma_dir / "chroma.sqlite3"
    # immutable=1 prevents even WAL/SHM sidecar creation.  An uncheckpointed WAL
    # would not be visible through that connection, so never select from it.
    for suffix in ("-wal", "-journal"):
        journal = catalog.with_name(f"{catalog.name}{suffix}")
        if journal.exists() and journal.stat().st_size:
            raise ValueError("cannot select collection pointer from a journaled Chroma catalog")
    try:
        connection = sqlite3.connect(f"{catalog.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
        try:
            rows = connection.execute(
                "SELECT c.name FROM collections AS c "
                "JOIN databases AS d ON c.database_id = d.id "
                "WHERE d.name = ? AND d.tenant_id = ?",
                ("default_database", "default_tenant"),
            )
            return {row[0] for row in rows}
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ValueError(f"cannot read Chroma collection catalog: {catalog}") from exc


def select_pointer(artifact_dir: Path, explicit: str | None) -> Path | None:
    """Preserve explicit/default choices; recover only absent default targets."""
    chroma_dir = artifact_dir / "db_chroma"
    if explicit:
        pointer = Path(explicit)
        targets = _targets(pointer)
        if targets and not set(targets.values()) <= _catalog_names(chroma_dir):
            raise ValueError(f"explicit collection pointer targets are missing: {pointer}")
        return pointer

    normal = artifact_dir / "collection_pointers.json"
    recovered = chroma_dir / "collection_pointers_rebuilt.json"
    # A new checkout has no recovered data.  Keep the application's existing
    # bootstrap/readiness behavior, including reporting missing dataset files.
    if not recovered.exists():
        return None
    normal_targets = _targets(normal) if normal.exists() else {}
    if normal.exists() and not normal_targets:
        return normal
    catalog_names = _catalog_names(chroma_dir)
    if normal.exists() and set(normal_targets.values()) <= catalog_names:
        return normal

    recovered_targets = _targets(recovered)
    if not recovered_targets or not set(normal_targets) <= set(recovered_targets):
        raise ValueError(f"recovered collection pointer omits default collections: {recovered}")
    if not set(recovered_targets.values()) <= catalog_names:
        raise ValueError(f"recovered collection pointer targets are missing: {recovered}")
    print(f"RAG startup: using recovered collection pointer {recovered}", file=sys.stderr)
    return recovered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, default=Path("/app/artifacts"))
    args = parser.parse_args()
    try:
        pointer = select_pointer(args.artifact_dir, os.environ.get(POINTER_ENV))
    except (OSError, ValueError) as exc:
        print(f"RAG startup: {exc}", file=sys.stderr)
        return 1
    if pointer is not None:
        # stdout is consumed by the entrypoint; keep diagnostics on stderr.
        print(pointer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
