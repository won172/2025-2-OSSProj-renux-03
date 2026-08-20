"""품질 지표에서 합성 트래픽을 걸러내는 규칙.

2026-08-04 로그 808건 중 실제 사용자 요청은 0건이었다(평가 러너 276 + 시점 이동 532).
이를 거르지 않으면 평가 하네스의 성적이 제품 품질로 보고된다. 특히 2030년 시점으로
물은 "현재 진행 중인 공모전"은 폴백이 정답인데, 섞어 세면 품질 저하로 집계된다.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.rag_service import _real_traffic_query  # noqa: E402
from src.database import Base, RagQueryLog  # noqa: E402


@pytest.fixture()
def 세션():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[RagQueryLog.__table__])
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _기록(session, *, request_id, as_of=None, created="2026-08-04 10:00:00"):
    session.add(
        RagQueryLog(
            request_id=request_id,
            session_id=f"s-{request_id}",
            question="현재 진행 중인 공모전 알려줘",
            answer="…",
            as_of=as_of,
            created_at=datetime.fromisoformat(created),
        )
    )
    session.commit()


def test_평가_러너_요청은_제외한다(세션):
    _기록(세션, request_id="eval_abc123")
    _기록(세션, request_id="golden-98f1")
    _기록(세션, request_id="req-사람")
    남은 = _real_traffic_query(세션).all()
    assert [row.request_id for row in 남은] == ["req-사람"]


def test_시점을_이동한_요청은_제외한다(세션):
    # 2030년 시점 질의는 "진행 중인 공모전 없음"이 정답이라 폴백이 의도된 결과다.
    _기록(세션, request_id="req-미래", as_of="2030-01-01", created="2026-08-04 10:00:00")
    _기록(세션, request_id="req-과거", as_of="2026-07-01", created="2026-08-04 10:00:00")
    assert _real_traffic_query(세션).count() == 0


def test_기록일과_같은_as_of는_실제_트래픽이다(세션):
    _기록(세션, request_id="req-오늘", as_of="2026-08-04", created="2026-08-04 10:00:00")
    assert [row.request_id for row in _real_traffic_query(세션).all()] == ["req-오늘"]


@pytest.mark.parametrize("빈값", [None, ""])
def test_as_of가_비어_있으면_실제_트래픽이다(세션, 빈값):
    _기록(세션, request_id="req-무시점", as_of=빈값)
    assert _real_traffic_query(세션).count() == 1


def test_request_id가_없어도_실제_트래픽으로_본다(세션):
    # 옛 로그에는 request_id가 없다. 합성이라는 근거가 없으므로 제외하지 않는다.
    _기록(세션, request_id=None)
    assert _real_traffic_query(세션).count() == 1


def test_평가_러너이면서_시점도_이동한_요청도_한_번만_제외된다(세션):
    _기록(세션, request_id="eval_x", as_of="2030-01-01")
    _기록(세션, request_id="req-사람", as_of=None)
    assert _real_traffic_query(세션).count() == 1


def test_최근_기간만_세는_조건이_추가된다(세션):
    """누적 집계는 기능이 없던 시절 데이터에 지배된다.

    근거검증 미실시율을 월별로 보면 2026-05 100% / 06 67% / 07 29% / 08 28%인데
    누적은 57%로 나온다. 그 값을 현재 품질로 읽으면 개선이 통째로 묻힌다.
    """
    from api.rag_service import RAG_METRICS_WINDOW_DAYS, _real_traffic_query

    최근 = datetime.now() - timedelta(days=1)
    오래됨 = datetime.now() - timedelta(days=RAG_METRICS_WINDOW_DAYS + 5)
    _기록(세션, request_id="req-최근", created=최근.isoformat(sep=" ", timespec="seconds"))
    _기록(세션, request_id="req-오래됨", created=오래됨.isoformat(sep=" ", timespec="seconds"))

    assert _real_traffic_query(세션).count() == 2  # 누적은 둘 다
    창 = _real_traffic_query(세션, window=True).all()
    assert [r.request_id for r in 창] == ["req-최근"]


def test_기간을_환경변수로_바꿀_수_있다():
    from api.rag_service import RAG_METRICS_WINDOW_DAYS, _metrics_window_start

    시작 = _metrics_window_start(datetime(2026, 8, 6))
    assert (datetime(2026, 8, 6) - 시작).days == RAG_METRICS_WINDOW_DAYS
