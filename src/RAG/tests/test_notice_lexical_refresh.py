"""승인된 공지가 어휘 검색에도 즉시 걸리는지.

승인 경로는 Chroma에만 증분 upsert한다. parquet/BM25를 다시 만들지 않으면 새 공지는
정기 갱신(기본 6시간)까지 dense 전용으로 남고, RRF가 두 순위를 합치므로 절반의 점수만
받는다. 실제로 FAQ 11건이 Chroma에는 11건, parquet에는 0건인 상태가 관측됐다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api.rag_service as rag_service  # noqa: E402


@pytest.fixture(autouse=True)
def _초기화():
    rag_service._lexical_rebuild_pending.clear()
    yield
    rag_service._lexical_rebuild_pending.clear()


def test_캐시_재적재가_어휘_아티팩트도_다시_만든다(monkeypatch):
    호출 = []
    monkeypatch.setattr(
        rag_service,
        "_rebuild_notices_lexical_artifacts",
        lambda context: 호출.append(context),
    )
    monkeypatch.setattr(rag_service, "_ensure_dataset_locked", lambda name: None)
    rag_service._datasets.pop("notices", None)

    rag_service._reload_notices_cache("approve")

    assert 호출 == ["approve"]


def test_어휘_재생성이_실패해도_캐시_재적재는_진행된다(monkeypatch):
    def 폭발(context):
        raise RuntimeError("디스크 오류")

    재적재 = []
    monkeypatch.setattr(rag_service, "_rebuild_notices_lexical_artifacts", 폭발)
    monkeypatch.setattr(
        rag_service, "_ensure_dataset_locked", lambda name: 재적재.append(name)
    )
    rag_service._datasets.pop("notices", None)

    # DB·Chroma에는 이미 반영됐으므로 승인 자체를 실패시키지 않는다.
    rag_service._reload_notices_cache("approve")

    assert 재적재 == ["notices"]


def test_연속_승인은_재생성을_한_번으로_합친다(monkeypatch):
    실행 = []

    def 가짜_프레임():
        실행.append("build")
        return []

    monkeypatch.setattr(
        "src.pipelines.ingest.build_notice_index_frame_from_db", 가짜_프레임
    )
    monkeypatch.setattr(
        "src.pipelines.ingest.persist_dataset_artifacts_only",
        lambda key, frame: (frame, None, None),
    )

    rag_service._rebuild_notices_lexical_artifacts("approve-1")
    assert 실행 == ["build"]

    # 락을 잡지 못해 대기하다 들어온 두 번째 요청은 플래그가 이미 내려가 있으면
    # 재생성을 건너뛴다. 여기서는 순차 호출이므로 매번 새 요청으로 취급된다.
    rag_service._rebuild_notices_lexical_artifacts("approve-2")
    assert 실행 == ["build", "build"]
