from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api import rag_service
from src.database import Base, PendingItem
from src.services import staff_refresh


def test_staff_approval_records_successful_ingestion_run(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine)
    with test_session() as session:
        item = PendingItem(
            source_type="staff_refresh",
            data=json.dumps({"snapshot_sha256": "snapshot-1"}),
            status="pending",
        )
        session.add(item)
        session.commit()
        item_id = int(item.id)

    finished: list[tuple[int | None, dict]] = []
    monkeypatch.setattr(rag_service, "SessionLocal", test_session)
    monkeypatch.setattr(rag_service, "refresh_runtime_dataset_state", lambda _targets: {})
    monkeypatch.setattr(rag_service, "_start_staff_approval_run", lambda: 91)
    monkeypatch.setattr(
        rag_service,
        "_finish_staff_approval_run",
        lambda run_id, **kwargs: finished.append((run_id, kwargs)),
    )
    monkeypatch.setattr(
        staff_refresh,
        "apply_staff_refresh_payload",
        lambda _payload: {
            "rows": 3,
            "chunks": 4,
            "snapshot_sha256": "snapshot-1",
        },
    )

    response = rag_service._approve_staff_refresh_item(
        item_id,
        rag_service.ReviewActionRequest(actor="admin", note="확인"),
    )

    assert response["status"] == "approved"
    assert finished == [
        (
            91,
            {
                "status": "success",
                "seen": 3,
                "outcome_code": "approved_review",
                "diagnostics": {
                    "pending_item_id": item_id,
                    "snapshot_sha256": "snapshot-1",
                    "chunks": 4,
                },
                "run_derivatives": True,
            },
        )
    ]
    with test_session() as session:
        approved = session.get(PendingItem, item_id)
        assert approved.status == "approved"
        assert approved.reviewed_by == "admin"
