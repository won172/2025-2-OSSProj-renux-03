"""관리자 콘솔이 의존하는 서버 계약 테스트.

무거운 모델 로딩 없이 검증할 수 있는 부분(필터 파싱, 스케줄러 상태, 검수 기록)만 다룬다.
색인 부작용이 있는 승인/반려 경로는 별도 통합 테스트 대상이다.
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KST = timezone(timedelta(hours=9))


@pytest.fixture()
def scheduler_module():
    """모듈 전역 실행 기록이 테스트 간에 새지 않도록 매번 새로 임포트한다."""
    sys.modules.pop("src.services.scheduler", None)
    module = importlib.import_module("src.services.scheduler")
    yield module
    sys.modules.pop("src.services.scheduler", None)


def test_스케줄러_미기동시_빈_작업목록과_비활성_상태를_반환한다(scheduler_module):
    status = scheduler_module.get_scheduler_status()

    assert status["enabled"] is False
    assert status["jobs"] == []


def test_수동_실행_기록은_스케줄러가_꺼져있어도_남는다(scheduler_module):
    scheduler_module._record_run("refresh_notices", "ok", "신규 3 · 수정 1 · 실패 0")

    status = scheduler_module.get_scheduler_status()
    jobs = {job["id"]: job for job in status["jobs"]}

    assert "refresh_notices" in jobs
    assert jobs["refresh_notices"]["name"] == "공지 수집"
    assert jobs["refresh_notices"]["last_status"] == "ok"
    assert jobs["refresh_notices"]["last_message"] == "신규 3 · 수정 1 · 실패 0"
    # 스케줄러가 없으면 다음 실행 시각을 알 수 없으므로 None을 명시한다(0이나 과거 시각 아님).
    assert jobs["refresh_notices"]["next_run_at"] is None


def test_실패한_실행은_사유와_함께_기록된다(scheduler_module):
    scheduler_module._record_run("refresh_meals", "failed", "connection timed out")

    jobs = {job["id"]: job for job in scheduler_module.get_scheduler_status()["jobs"]}

    assert jobs["refresh_meals"]["last_status"] == "failed"
    assert jobs["refresh_meals"]["last_message"] == "connection timed out"


def test_학식_수집_0건은_부분실패와_원인코드로_기록한다(scheduler_module, monkeypatch):
    """0건은 기존 인덱스를 보존하되 운영 경보가 필요한 부분 실패다."""
    import pandas as pd

    monkeypatch.setitem(
        sys.modules,
        "src.crawlers.dongguk_meals",
        type(sys)("src.crawlers.dongguk_meals"),
    )
    sys.modules["src.crawlers.dongguk_meals"].crawl_meals = lambda **_: pd.DataFrame()
    monkeypatch.setitem(sys.modules, "src.pipelines.ingest", type(sys)("src.pipelines.ingest"))
    sys.modules["src.pipelines.ingest"].ingest_meals = lambda df: (df, None, None)
    finished: list[dict] = []
    monkeypatch.setattr(scheduler_module, "_start_ingestion_run", lambda _dataset: 1)
    monkeypatch.setattr(
        scheduler_module,
        "_finish_ingestion_run",
        lambda _run_id, **kwargs: finished.append(kwargs),
    )

    scheduler_module.refresh_meals_job()

    jobs = {job["id"]: job for job in scheduler_module.get_scheduler_status()["jobs"]}
    assert jobs["refresh_meals"]["last_status"] == "partial"
    assert "empty_source" in jobs["refresh_meals"]["last_message"]
    assert len(finished) == 1
    assert finished[0]["status"] == "partial"
    assert finished[0]["error"] == "empty_source: 수집 0건 — 기존 인덱스 보존"
    assert finished[0]["outcome_code"] == "empty_source"
    assert finished[0]["diagnostics"] == {}
    assert finished[0]["source_structures"][0].source_name == "dining_api"


# ---------------------------------------------------------------- 필터 파싱

@pytest.mark.parametrize("empty", [None, "", "   "])
def test_빈_기간_조건은_필터를_적용하지_않는다(empty):
    from src.utils.admin_filters import parse_admin_datetime

    assert parse_admin_datetime(empty, "from") is None


def test_프런트가_보내는_Z_접미사_ISO_문자열을_해석한다():
    """toISOString()이 만드는 'Z'를 fromisoformat이 3.10 이하에서 못 받는다."""
    from src.utils.admin_filters import parse_admin_datetime

    parsed = parse_admin_datetime("2026-07-23T15:00:00.000Z", "from")

    assert parsed is not None
    assert parsed.astimezone(timezone.utc) == datetime(2026, 7, 23, 15, 0, tzinfo=timezone.utc)


def test_오프셋이_붙은_ISO_문자열도_그대로_해석한다():
    from src.utils.admin_filters import parse_admin_datetime

    parsed = parse_admin_datetime("2026-07-23T09:00:00+09:00", "to")

    assert parsed == datetime(2026, 7, 23, 9, 0, tzinfo=KST)


def test_형식이_틀린_기간_조건은_조용히_무시하지_않고_오류를_낸다():
    """무시하면 사용자는 필터가 걸린 줄 알고 잘못된 결과를 신뢰하게 된다."""
    from src.utils.admin_filters import AdminFilterError, parse_admin_datetime

    with pytest.raises(AdminFilterError) as error:
        parse_admin_datetime("어제", "from")

    assert error.value.field == "from"
    assert "from" in str(error.value)


# ---------------------------------------------------------------- 검수 기록

class _FakeItem:
    """PendingItem의 검수 기록 필드만 흉내 낸다(DB 없이 검증하기 위함)."""
    review_note = None
    reviewed_by = None
    reviewed_at = None


def test_검수_기록은_공백만_있는_사유를_저장하지_않는다():
    """공백을 그대로 두면 학과 화면에 '반려 사유: ' 뒤가 비어, 적은 것과 구분되지 않는다."""
    from src.utils.admin_filters import apply_review_record

    item = _FakeItem()
    apply_review_record(item, note="   ", actor="  ")

    assert item.review_note is None
    assert item.reviewed_by is None
    assert item.reviewed_at is not None


def test_검수_기록은_사유와_처리자를_트림해_남긴다():
    from src.utils.admin_filters import apply_review_record

    item = _FakeItem()
    apply_review_record(item, note="  날짜를 확인해주세요.  ", actor=" 조준용 ")

    assert item.review_note == "날짜를 확인해주세요."
    assert item.reviewed_by == "조준용"


def test_사유가_없어도_처리_시각은_남긴다():
    """승인은 사유가 필수가 아니지만, '언제 처리했는가'는 감사를 위해 항상 남아야 한다."""
    from src.utils.admin_filters import apply_review_record

    item = _FakeItem()
    fixed_now = datetime(2026, 7, 30, 14, 32, tzinfo=KST)
    apply_review_record(item, note=None, actor=None, now=fixed_now)

    assert item.review_note is None
    assert item.reviewed_at == fixed_now
