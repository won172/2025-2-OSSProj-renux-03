from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base, SourceDocument
from src.services.canonical_lineage import build_canonical_lineage_report


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _artifact(tmp_path, frame: pd.DataFrame):
    path = tmp_path / "fixture.parquet"
    frame.to_parquet(path, index=False)
    return SimpleNamespace(chunk_path=path, collection="fixture-collection")


def test_canonical_lineage_gate_accepts_exact_parent_and_chunk_identity(tmp_path):
    session = _session()
    try:
        session.add(
            SourceDocument(
                dataset="fixture",
                source_type="fixture",
                source_id="1",
                source_url="fixture://1",
                document_key="fixture:1",
                status="active",
            )
        )
        session.commit()
        artifact = _artifact(
            tmp_path,
            pd.DataFrame(
                {
                    "chunk_id": ["chunk-1", "chunk-2"],
                    "doc_id": ["fixture:1", "fixture:1"],
                    "chunk_text": ["첫 청크", "둘째 청크"],
                }
            ),
        )
        report = build_canonical_lineage_report(
            session,
            artifacts={"fixture": artifact},
            collection_snapshot_loader=lambda _name: (
                ["chunk-1", "chunk-2"],
                [{"doc_id": "fixture:1"}, {"doc_id": "fixture:1"}],
            ),
        )
    finally:
        session.close()

    assert report["contains_user_content"] is False
    assert report["gate_passed"] is True
    assert report["violations"] == []
    assert report["datasets"][0]["counts"]["source_documents"] == 1
    assert report["datasets"][0]["counts"]["artifact_chunks"] == 2


def test_canonical_lineage_gate_reports_each_identity_boundary_without_content(tmp_path):
    session = _session()
    try:
        session.add_all(
            [
                SourceDocument(
                    dataset="fixture",
                    source_type="fixture",
                    source_id="canonical",
                    source_url="fixture://canonical",
                    document_key="fixture:canonical",
                    title="출력되면 안 되는 제목",
                    status="active",
                ),
                SourceDocument(
                    dataset="fixture",
                    source_type="fixture",
                    source_id="hidden",
                    source_url="fixture://hidden",
                    document_key="fixture:hidden",
                    title="숨김 문서",
                    status="hidden",
                ),
            ]
        )
        session.commit()
        artifact = _artifact(
            tmp_path,
            pd.DataFrame(
                {
                    "chunk_id": ["chunk-artifact", "chunk-artifact"],
                    "doc_id": ["fixture:artifact", "fixture:artifact"],
                    "chunk_text": ["민감할 수 있는 본문", "중복 본문"],
                }
            ),
        )
        report = build_canonical_lineage_report(
            session,
            artifacts={"fixture": artifact},
            collection_snapshot_loader=lambda _name: (
                ["chunk-chroma"],
                [{"doc_id": "fixture:wrong"}],
            ),
        )
    finally:
        session.close()

    rendered = str(report)
    metrics = {item["metric"] for item in report["violations"]}
    assert report["gate_passed"] is False
    assert {
        "source_missing_artifact",
        "artifact_missing_source",
        "artifact_chunk_id_duplicate",
        "artifact_missing_chroma",
        "chroma_missing_artifact",
        "chroma_metadata_doc_id_mismatch",
    } <= metrics
    assert "출력되면 안 되는 제목" not in rendered
    assert "민감할 수 있는 본문" not in rendered


def test_canonical_lineage_gate_fails_closed_on_missing_artifact(tmp_path):
    session = _session()
    try:
        artifact = SimpleNamespace(
            chunk_path=tmp_path / "missing.parquet",
            collection="fixture-collection",
        )
        report = build_canonical_lineage_report(
            session,
            artifacts={"fixture": artifact},
            collection_snapshot_loader=lambda _name: ([], []),
        )
    finally:
        session.close()

    assert report["gate_passed"] is False
    assert report["violations"] == [
        {"dataset": "fixture", "metric": "inspection_error", "count": 1}
    ]
    assert report["datasets"][0]["error"]["type"] == "FileNotFoundError"
