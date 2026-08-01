from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pipelines import ingest  # noqa: E402
from src.services.retrieval_context import enrich_retrieval_fields  # noqa: E402


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "chunk_id": "notice-1",
                "chunk_text": "신청 기간은 8월 3일부터 8월 7일까지입니다.",
                "title": "2026학년도 2학기 학부 수강 신청 안내",
                "published_at": "2026-07-22",
                "source": "notices",
            }
        ]
    )


def test_context_header_is_attached_to_every_retrieval_chunk():
    enriched = enrich_retrieval_fields(_frame())
    row = enriched.iloc[0]

    assert row["title_norm"] == "2026학년도2학기학부수강신청안내"
    assert row["audience"] == "undergraduate"
    assert "문서: 2026학년도 2학기 학부 수강 신청 안내" in row["retrieval_context"]
    assert "기준일: 2026-07-22" in row["retrieval_context"]
    assert "학사시기: 2026학년도 2학기" in row["retrieval_context"]
    assert "대상: 학부" in row["retrieval_context"]
    assert row["retrieval_text"].endswith(row["chunk_text"])


def test_context_enrichment_is_idempotent():
    once = enrich_retrieval_fields(_frame())
    twice = enrich_retrieval_fields(once)
    assert twice.loc[0, "retrieval_text"] == once.loc[0, "retrieval_text"]
    assert twice.loc[0, "retrieval_text"].count("[문서:") == 1


def test_artifact_only_persistence_trains_bm25_on_contextual_text(
    monkeypatch,
    tmp_path,
):
    captured: dict[str, object] = {}

    def fake_train(identifier, corpus, chunk_ids=None):
        captured["identifier"] = identifier
        captured["corpus"] = list(corpus)
        captured["chunk_ids"] = list(chunk_ids or [])
        return object(), np.empty((1, 0), dtype=np.float32)

    monkeypatch.setitem(
        ingest.DATASET_ARTIFACTS,
        "notices",
        ingest.DatasetArtifacts(
            key="notices",
            collection="test",
            chunk_path=tmp_path / "notices.parquet",
        ),
    )
    monkeypatch.setattr(ingest, "train_bm25", fake_train)

    persisted, _, _ = ingest.persist_dataset_artifacts_only(
        "notices",
        _frame(),
    )

    assert persisted.loc[0, "audience"] == "undergraduate"
    assert captured["chunk_ids"] == ["notice-1"]
    assert str(captured["corpus"][0]).startswith("[문서:")
    assert (tmp_path / "notices.parquet").exists()
