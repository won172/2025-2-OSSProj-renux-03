"""반복해서 묻는데 답하지 못한 질문을 FAQ 초안으로 올리는 규칙.

이 기능은 `scheduler.refresh_notices_job()` 성공 후 돌지만, 이전 구현은 모든
단계가 죽어 있었다 — 존재하지 않는 `ChatMessage` 모델을 조회하고,
`build_retrieval_context`를 실제와 다른 시그니처로 불렀다. 예외를 전부
삼키는 구조라 초안 0건이 조용히 유지됐다. 아래 테스트가 각 단계를 고정한다.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.database import Base, Notice, PendingItem, RagQueryLog  # noqa: E402
from src.services import auto_faq  # noqa: E402


@pytest.fixture
def 세션(monkeypatch):
    엔진 = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(엔진)
    Factory = sessionmaker(bind=엔진)
    import src.database as db

    monkeypatch.setattr(db, "SessionLocal", Factory)
    return Factory()


def _질의(세션, question, *, n=1, fallback=False, reason=None,
         grounded=None, request_id="req-사람", as_of=None, days_ago=1):
    for i in range(n):
        세션.add(RagQueryLog(
            request_id=f"{request_id}-{i}",
            question=question,
            as_of=as_of,
            fallback_triggered=fallback,
            fallback_reason=reason,
            grounding_checked=grounded is not None,
            grounding_grounded=grounded,
            created_at=datetime.now() - timedelta(days=days_ago),
        ))
    세션.commit()


# --- 후보 선정 ----------------------------------------------------------------


def test_반복되고_실패한_질문만_후보가_된다(세션):
    _질의(세션, "보강 일정 어디서 봐?", n=4, fallback=True, reason="no_results")
    _질의(세션, "오늘 학식 뭐야?", n=5, grounded=True)          # 잘 답함 → 제외
    _질의(세션, "가끔 묻는 질문", n=1, fallback=True)             # 빈도 미달 → 제외

    후보 = auto_faq.collect_gap_questions(days=30, min_count=3)
    assert [c["question"] for c in 후보] == ["보강 일정 어디서 봐?"]
    assert 후보[0]["count"] == 4
    assert 후보[0]["reasons"] == {"no_results": 4}


def test_근거검증_실패도_후보로_잡는다(세션):
    """폴백하지 않아도 근거 없는 답을 했으면 FAQ가 필요하다."""
    _질의(세션, "총학생회비 얼마야?", n=3, grounded=False)
    후보 = auto_faq.collect_gap_questions(days=30, min_count=3)
    assert 후보[0]["ungrounded"] == 3
    assert 후보[0]["fallback"] == 0


def test_평가_트래픽은_세지_않는다(세션):
    """골든셋 문항이 FAQ 초안으로 올라오면 안 된다."""
    _질의(세션, "평가용 질문", n=5, fallback=True, request_id="eval_x")
    _질의(세션, "골든 질문", n=5, fallback=True, request_id="golden-1")
    assert auto_faq.collect_gap_questions(days=30, min_count=3) == []


def test_기준일을_옮겨_물은_요청도_제외한다(세션):
    """as_of를 미래로 준 질의는 폴백이 정답이라 실패로 세면 안 된다."""
    _질의(세션, "현재 진행 중인 공모전", n=5, fallback=True, as_of="2030-01-01")
    assert auto_faq.collect_gap_questions(days=30, min_count=3) == []


def test_기간_밖의_질문은_세지_않는다(세션):
    _질의(세션, "오래된 질문", n=5, fallback=True, days_ago=60)
    assert auto_faq.collect_gap_questions(days=14, min_count=3) == []


def test_띄어쓰기만_다른_질문은_같은_것으로_묶는다(세션):
    _질의(세션, "보강 일정 알려줘", n=2, fallback=True)
    _질의(세션, "보강  일정   알려줘", n=2, fallback=True)
    후보 = auto_faq.collect_gap_questions(days=30, min_count=3)
    assert len(후보) == 1 and 후보[0]["count"] == 4


def test_실패가_많은_순으로_정렬한다(세션):
    """빈도보다 실패 횟수를 먼저 본다 — 자주 물어도 잘 답하면 FAQ가 덜 급하다."""
    _질의(세션, "많이 묻지만 덜 실패", n=18, grounded=True, request_id="req-a")
    _질의(세션, "많이 묻지만 덜 실패", n=2, fallback=True, request_id="req-b")
    _질의(세션, "적게 묻지만 다 실패", n=6, fallback=True, request_id="req-c")

    후보 = auto_faq.collect_gap_questions(days=30, min_count=3)
    assert [c["question"] for c in 후보] == ["적게 묻지만 다 실패", "많이 묻지만 덜 실패"]
    assert 후보[1]["count"] == 20 and 후보[1]["fallback"] == 2


# --- 중복 제거 ----------------------------------------------------------------


def test_이미_승인된_학과지식은_다시_올리지_않는다(세션):
    _질의(세션, "등록금 고지서 출력 어디서해?", n=4, fallback=True)
    세션.add(Notice(board="학과지식", title="등록금 고지서 출력 어디서해?", content="..."))
    세션.commit()

    import asyncio
    결과 = asyncio.run(auto_faq.generate_faq_drafts(days=30, min_count=3))
    assert 결과["created"] == 0
    assert 결과["skipped_existing"] == 1


def test_이미_제출된_초안은_다시_올리지_않는다(세션):
    _질의(세션, "보강 일정 어디서 봐?", n=4, fallback=True)
    세션.add(PendingItem(
        source_type="custom_knowledge", status="pending",
        data=json.dumps({"question": "보강 일정 어디서 봐?", "answer": "x"}, ensure_ascii=False),
    ))
    세션.commit()

    import asyncio
    결과 = asyncio.run(auto_faq.generate_faq_drafts(days=30, min_count=3))
    assert 결과["created"] == 0 and 결과["skipped_existing"] == 1


# --- 초안의 모양 --------------------------------------------------------------


def test_답을_지어내지_않고_관측만_남긴다(세션):
    """이 목록에 오르는 질문은 정의상 시스템이 답을 모르는 것이다.

    검색 결과를 요약해 답처럼 넣으면 검토자가 그대로 승인해 버릴 수 있다.
    """
    _질의(세션, "총학생회비 얼마야?", n=4, fallback=True, reason="no_results")
    import asyncio
    asyncio.run(auto_faq.generate_faq_drafts(days=30, min_count=3))

    item = 세션.query(PendingItem).one()
    data = json.loads(item.data)
    assert data["question"] == "총학생회비 얼마야?"
    assert "직접 작성한 뒤 승인" in data["answer"]
    assert "4회 질문" in data["answer"]
    assert "no_results" in data["answer"]
    assert data["suggested_by"] == "auto_faq"


def test_승인_가능한_source_type으로_등록한다(세션):
    """승인 경로가 아는 타입은 custom_knowledge/event/announcement 뿐이다.

    다른 이름으로 넣으면 검토 큐에 떠도 승인 시 아무것도 만들어지지 않는다.
    """
    from api.rag_service import _SUBMIT_REQUIRED_FIELDS

    _질의(세션, "보강 일정", n=4, fallback=True)
    import asyncio
    asyncio.run(auto_faq.generate_faq_drafts(days=30, min_count=3))

    item = 세션.query(PendingItem).one()
    assert item.source_type in _SUBMIT_REQUIRED_FIELDS
    assert item.status == "pending"
    data = json.loads(item.data)
    for 필수 in _SUBMIT_REQUIRED_FIELDS[item.source_type]:
        assert data.get(필수), f"승인에 필요한 '{필수}'가 비어 있다"


def test_상한을_넘으면_잘라낸_수를_보고한다(세션):
    """조용히 자르면 '다 처리했다'로 읽힌다."""
    for i in range(5):
        _질의(세션, f"답 없는 질문 {i}", n=3, fallback=True)
    import asyncio
    결과 = asyncio.run(auto_faq.generate_faq_drafts(days=30, min_count=3, limit=2))
    assert 결과["created"] == 2
    assert 결과["truncated"] == 3
    assert 결과["candidates"] == 5


# --- 견고성 -------------------------------------------------------------------


def test_로그_테이블이_없어도_수집을_막지_않는다(monkeypatch):
    """이 기능은 공지 수집 뒤에 붙어 돈다. 여기서 터지면 본 작업이 죽는다."""
    import src.database as db

    빈_엔진 = create_engine("sqlite:///:memory:")  # 테이블 없음
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=빈_엔진))
    assert auto_faq.collect_gap_questions(days=30, min_count=3) == []


# --- 스케줄러 연결 -------------------------------------------------------------


def test_초안_생성은_공지_갱신_성공_뒤에_돈다(monkeypatch):
    """이 호출은 원래 except 블록 안에 있어 실패했을 때만 돌았다.

    주석은 "수집 완료 시"였지만 동작은 정반대여서, 정상 운영에서는
    초안이 한 건도 만들어지지 않았다.
    """
    import inspect

    import src.services.scheduler as scheduler

    소스 = inspect.getsource(scheduler.refresh_notices_job)
    # 함수 안에 except가 둘이다(도서관 병합용이 먼저). 바깥 핸들러 기준으로 가른다.
    본문, _, 예외부 = 소스.rpartition("except Exception")
    assert "_start_faq_draft_worker()" in 본문, "성공 경로에서 호출되지 않는다"
    assert "_start_faq_draft_worker" not in 예외부, "실패 경로에서 호출되면 안 된다"


def test_초안_생성_실패가_공지_갱신을_막지_않는다(monkeypatch, caplog):
    """관측 성격의 후속 작업이 본 수집을 죽여서는 안 된다."""
    import logging

    import src.services.auto_faq as mod
    import src.services.scheduler as scheduler

    async def 폭발(**_kwargs):
        raise RuntimeError("초안 생성 실패")

    monkeypatch.setattr(mod, "generate_faq_drafts", 폭발)
    with caplog.at_level(logging.ERROR):
        scheduler._start_faq_draft_worker()
        for _ in range(50):
            if "초안 생성 실패" in caplog.text:
                break
            __import__("time").sleep(0.02)
    assert "초안 생성 실패" in caplog.text
