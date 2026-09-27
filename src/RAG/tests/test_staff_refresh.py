from __future__ import annotations

import json

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base, PendingItem, Staff
from src.services.staff_refresh import (
    apply_staff_refresh_payload,
    build_staff_diff,
    load_staff_snapshot,
    stage_staff_refresh,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _frame(phone: str = "02-0000-0001") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "원천ID": "staff-1",
                "조직(트리)": "교무팀",
                "부서경로": "본부 > 교무팀",
                "성명": "김**",
                "직위": "팀원",
                "담당업무": "수업",
                "전화번호": phone,
                "이메일": "",
            }
        ]
    )


def test_staff_diff_recognizes_contact_change_as_same_upstream_person():
    diff = build_staff_diff(_frame(), _frame("02-0000-0002"))

    assert diff["contact_changed"] == 1
    assert diff["added"] == 0
    assert diff["removed"] == 0


def test_staff_diff_bridges_legacy_snapshot_without_upstream_ids():
    current = _frame()
    current["원천ID"] = ""

    unchanged = build_staff_diff(current, _frame())
    contact_change = build_staff_diff(current, _frame("02-0000-0002"))

    assert unchanged["changed"] is False
    assert contact_change["contact_changed"] == 1
    assert contact_change["added"] == 0
    assert contact_change["removed"] == 0


def test_staff_refresh_stages_one_immutable_review_item(tmp_path):
    session = _session()
    try:
        current = _frame().iloc[0].to_dict()
        session.add(
            Staff(
                department=current["조직(트리)"],
                name=current["성명"],
                position=current["직위"],
                role=current["담당업무"],
                phone=current["전화번호"],
                raw_data=json.dumps(current, ensure_ascii=False),
            )
        )
        session.commit()

        staged = stage_staff_refresh(
            _frame("02-0000-0002"),
            session=session,
            snapshot_dir=tmp_path,
        )
        repeated = stage_staff_refresh(
            _frame("02-0000-0002"),
            session=session,
            snapshot_dir=tmp_path,
        )

        assert staged["contact_changed"] == 1
        assert staged["pending_item_id"] == repeated["pending_item_id"]
        assert session.query(PendingItem).count() == 1
        item = session.query(PendingItem).one()
        snapshot = load_staff_snapshot(json.loads(item.data), snapshot_dir=tmp_path)
        assert snapshot["전화번호"].tolist() == ["02-0000-0002"]
    finally:
        session.close()


def test_staff_refresh_skips_review_when_snapshot_is_unchanged(tmp_path):
    session = _session()
    try:
        current = _frame().iloc[0].to_dict()
        session.add(
            Staff(
                department=current["조직(트리)"],
                name=current["성명"],
                position=current["직위"],
                role=current["담당업무"],
                phone=current["전화번호"],
                raw_data=json.dumps(current, ensure_ascii=False),
            )
        )
        session.commit()

        result = stage_staff_refresh(_frame(), session=session, snapshot_dir=tmp_path)

        assert result["changed"] is False
        assert result["pending_item_id"] is None
        assert session.query(PendingItem).count() == 0
    finally:
        session.close()


def test_approved_staff_snapshot_is_integrity_checked_before_ingest(tmp_path):
    session = _session()
    try:
        staged = stage_staff_refresh(_frame(), session=session, snapshot_dir=tmp_path)
        item = session.get(PendingItem, staged["pending_item_id"])
        payload = json.loads(item.data)
    finally:
        session.close()

    received: list[pd.DataFrame] = []

    def fake_ingest(frame: pd.DataFrame):
        received.append(frame)
        return pd.DataFrame([{"chunk_id": "staff:1"}]), None, None

    result = apply_staff_refresh_payload(
        payload,
        snapshot_dir=tmp_path,
        ingest_fn=fake_ingest,
    )

    assert result["rows"] == 1
    assert result["chunks"] == 1
    assert received[0]["원천ID"].tolist() == ["staff-1"]
