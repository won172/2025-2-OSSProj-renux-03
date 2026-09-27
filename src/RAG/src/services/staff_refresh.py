"""Reviewable staff-directory snapshot staging and application."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

import pandas as pd
from sqlalchemy.orm import Session

from src.config import ARTIFACT_DIR
from src.database import PendingItem, SessionLocal, Staff, kst_now


STAFF_REFRESH_SOURCE_TYPE = "staff_refresh"
STAFF_REVIEW_DIR = ARTIFACT_DIR / "staff_reviews"
STAFF_COLUMNS = (
    "원천ID",
    "조직(트리)",
    "부서경로",
    "성명",
    "직위",
    "담당업무",
    "전화번호",
    "이메일",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def normalize_staff_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize crawler/legacy names without discarding extra public fields."""
    normalized = frame.fillna("").astype(str).copy()
    aliases = {
        "원천ID": ("원천ID", "source_id", "staff_id", "staff_seq"),
        "조직(트리)": ("조직(트리)", "department"),
        "부서경로": ("부서경로", "department_path"),
        "성명": ("성명", "이름", "name"),
        "직위": ("직위", "position"),
        "담당업무": ("담당업무", "role", "charge"),
        "전화번호": ("전화번호", "phone", "telephone"),
        "이메일": ("이메일", "email"),
    }
    for target, candidates in aliases.items():
        if target in normalized.columns:
            normalized[target] = normalized[target].map(_text)
            continue
        source = next((name for name in candidates if name in normalized.columns), None)
        normalized[target] = "" if source is None else normalized[source].map(_text)
    normalized.drop_duplicates(subset=list(STAFF_COLUMNS), keep="last", inplace=True)
    normalized.sort_values(list(STAFF_COLUMNS), kind="stable", inplace=True)
    normalized.reset_index(drop=True, inplace=True)
    return normalized


def load_current_staff_frame(session: Session) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for staff in session.query(Staff).order_by(Staff.id.asc()).all():
        try:
            payload = json.loads(staff.raw_data or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload.update(
            {
                "조직(트리)": payload.get("조직(트리)") or staff.department or "",
                "성명": payload.get("성명") or staff.name or "",
                "직위": payload.get("직위") or staff.position or "",
                "담당업무": payload.get("담당업무") or staff.role or "",
                "전화번호": payload.get("전화번호") or staff.phone or "",
                "이메일": payload.get("이메일") or staff.email or "",
            }
        )
        rows.append(payload)
    return normalize_staff_frame(pd.DataFrame(rows)) if rows else pd.DataFrame(columns=STAFF_COLUMNS)


def _group_key(
    row: dict[str, str],
    *,
    shared_upstream_ids: set[str],
) -> tuple[str, ...]:
    upstream_id = _text(row.get("원천ID"))
    # Legacy snapshots were stored before the crawler retained ``staff_seq``.
    # During that one-time transition, using the new ID on only one side would
    # misclassify the entire directory as removed + added. Trust an upstream ID
    # only when it exists in both snapshots; otherwise bridge on public profile
    # fields. Once one reviewed snapshot is applied, future comparisons use the
    # stable upstream ID automatically.
    if upstream_id and upstream_id in shared_upstream_ids:
        return ("upstream", upstream_id)
    return (
        "fallback",
        _text(row.get("부서경로") or row.get("조직(트리)")),
        _text(row.get("성명")),
        _text(row.get("직위")),
        _text(row.get("담당업무")),
    )


def _contact(row: dict[str, str]) -> tuple[str, str]:
    return (_text(row.get("전화번호")), _text(row.get("이메일")))


def build_staff_diff(current: pd.DataFrame, incoming: pd.DataFrame) -> dict[str, Any]:
    """Compare contact multisets so masked duplicate names remain safe."""
    before = normalize_staff_frame(current)
    after = normalize_staff_frame(incoming)
    before_ids = {_text(value) for value in before["원천ID"].tolist() if _text(value)}
    after_ids = {_text(value) for value in after["원천ID"].tolist() if _text(value)}
    shared_upstream_ids = before_ids & after_ids
    before_groups: dict[tuple[str, ...], Counter] = defaultdict(Counter)
    after_groups: dict[tuple[str, ...], Counter] = defaultdict(Counter)
    for record in before[list(STAFF_COLUMNS)].to_dict(orient="records"):
        before_groups[
            _group_key(record, shared_upstream_ids=shared_upstream_ids)
        ][_contact(record)] += 1
    for record in after[list(STAFF_COLUMNS)].to_dict(orient="records"):
        after_groups[
            _group_key(record, shared_upstream_ids=shared_upstream_ids)
        ][_contact(record)] += 1

    added = 0
    removed = 0
    contact_changed = 0
    samples: list[dict[str, Any]] = []
    for key in sorted(set(before_groups) | set(after_groups)):
        old_contacts = before_groups.get(key, Counter())
        new_contacts = after_groups.get(key, Counter())
        removed_contacts = old_contacts - new_contacts
        added_contacts = new_contacts - old_contacts
        paired = min(sum(removed_contacts.values()), sum(added_contacts.values()))
        contact_changed += paired
        removed += sum(removed_contacts.values()) - paired
        added += sum(added_contacts.values()) - paired
        if (removed_contacts or added_contacts) and len(samples) < 100:
            samples.append(
                {
                    "identity": list(key),
                    "before_contacts": [list(value) for value in removed_contacts.elements()],
                    "after_contacts": [list(value) for value in added_contacts.elements()],
                }
            )

    return {
        "current_rows": len(before),
        "incoming_rows": len(after),
        "added": added,
        "removed": removed,
        "contact_changed": contact_changed,
        "changed": bool(added or removed or contact_changed),
        "samples": samples,
    }


def _snapshot_bytes(frame: pd.DataFrame) -> bytes:
    records = normalize_staff_frame(frame).to_dict(orient="records")
    return json.dumps(
        {"schema_version": 1, "records": records},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_snapshot(frame: pd.DataFrame, root: Path) -> tuple[Path, str]:
    content = _snapshot_bytes(frame)
    digest = hashlib.sha256(content).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"staff-{digest}.json"
    if target.exists():
        return target, digest
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=root)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return target, digest


def stage_staff_refresh(
    frame: pd.DataFrame,
    *,
    session: Session | None = None,
    snapshot_dir: Path | None = None,
) -> dict[str, Any]:
    """Persist one immutable candidate and create/reuse its review item."""
    owns_session = session is None
    active = session or SessionLocal()
    root = (snapshot_dir or STAFF_REVIEW_DIR).resolve()
    try:
        incoming = normalize_staff_frame(frame)
        if incoming.empty:
            raise ValueError("staff crawler returned an empty snapshot")
        current = load_current_staff_frame(active)
        diff = build_staff_diff(current, incoming)
        if not diff["changed"]:
            return {**diff, "pending_item_id": None, "snapshot_sha256": None}

        path, digest = _write_snapshot(incoming, root)
        existing_id = None
        for item in (
            active.query(PendingItem)
            .filter(
                PendingItem.source_type == STAFF_REFRESH_SOURCE_TYPE,
                PendingItem.status == "pending",
            )
            .all()
        ):
            try:
                payload = json.loads(item.data or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if payload.get("snapshot_sha256") == digest:
                existing_id = item.id
                break

        payload = {
            "schema_version": 1,
            "snapshot_file": path.name,
            "snapshot_sha256": digest,
            "created_at": kst_now().isoformat(),
            "diff": diff,
        }
        if existing_id is None:
            item = PendingItem(
                source_type=STAFF_REFRESH_SOURCE_TYPE,
                data=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                status="pending",
            )
            active.add(item)
            active.commit()
            active.refresh(item)
            existing_id = int(item.id)
        return {**diff, "pending_item_id": existing_id, "snapshot_sha256": digest}
    finally:
        if owns_session:
            active.close()


def load_staff_snapshot(payload: dict[str, Any], *, snapshot_dir: Path | None = None) -> pd.DataFrame:
    root = (snapshot_dir or STAFF_REVIEW_DIR).resolve()
    filename = Path(str(payload.get("snapshot_file") or "")).name
    if not filename or filename != str(payload.get("snapshot_file") or ""):
        raise ValueError("invalid staff snapshot filename")
    path = (root / filename).resolve()
    if path.parent != root or not path.exists():
        raise FileNotFoundError("staff snapshot is missing")
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != str(payload.get("snapshot_sha256") or ""):
        raise ValueError("staff snapshot integrity check failed")
    decoded = json.loads(content)
    records = decoded.get("records") if isinstance(decoded, dict) else None
    if not isinstance(records, list) or not records:
        raise ValueError("staff snapshot has no records")
    return normalize_staff_frame(pd.DataFrame(records))


def apply_staff_refresh_payload(
    payload: dict[str, Any],
    *,
    snapshot_dir: Path | None = None,
    ingest_fn: Callable[[pd.DataFrame], tuple] | None = None,
) -> dict[str, Any]:
    frame = load_staff_snapshot(payload, snapshot_dir=snapshot_dir)
    if ingest_fn is None:
        from src.pipelines.ingest import ingest_staff_frame

        ingest_fn = ingest_staff_frame
    chunks, _, _ = ingest_fn(frame)
    return {
        "rows": len(frame),
        "chunks": len(chunks),
        "snapshot_sha256": payload["snapshot_sha256"],
    }


__all__ = [
    "STAFF_REFRESH_SOURCE_TYPE",
    "apply_staff_refresh_payload",
    "build_staff_diff",
    "load_staff_snapshot",
    "normalize_staff_frame",
    "stage_staff_refresh",
]
