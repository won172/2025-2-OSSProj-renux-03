"""공지/학식 데이터의 주기적 자동 갱신 스케줄러 (rag-service 프로세스 내부).

별도 워커 컨테이너 대신 서빙 프로세스 안에서 APScheduler로 돌린다:
- 이미 로드된 임베딩 모델을 재사용 → 추가 메모리 없음.
- Chroma 클라이언트를 단일 프로세스가 소유 → 멀티프로세스 동시 접근 위험 없음.

작업은 BackgroundScheduler의 워커 스레드에서 실행되어 asyncio 이벤트 루프(서빙)를 막지 않는다.
재진입 방지(max_instances=1)·중복 누적 방지(coalesce=True)를 적용한다.

기본 비활성(RAG_SCHEDULER_ENABLED=0). 배포 환경에서 env로 켠다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from src.config import (
    DATA_SOURCES,
    RAG_MEALS_REFRESH_HOURS,
    RAG_NOTICES_REFRESH_HOURS,
    RAG_NOTICES_REFRESH_MAX_PAGES,
    RAG_SCHEDULER_ENABLED,
)

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))

_scheduler = None  # 단일 인스턴스 보관(중복 시작 방지)


def refresh_notices_job() -> None:
    """공지 게시판 최근 페이지를 크롤링해 증분 동기화 + 인덱스 갱신한다."""
    from src.crawlers.dongguk_notices import crawl_notices
    from src.pipelines.notices_sync import sync_notices

    start = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    logger.info("[scheduler] 공지 갱신 시작 (%s)", start)
    try:
        df = crawl_notices(max_pages=RAG_NOTICES_REFRESH_MAX_PAGES, delay=0.2)
        summary = sync_notices(df, allow_missing_detection=False, mode="full-sync")
        logger.info(
            "[scheduler] 공지 갱신 완료 seen=%s new=%s updated=%s deleted=%s failed=%s",
            summary.get("seen"), summary.get("new"), summary.get("updated"),
            summary.get("deleted"), summary.get("failed"),
        )
    except Exception as exc:  # noqa: BLE001 — 한 번의 실패가 스케줄러를 죽이지 않도록
        logger.error("[scheduler] 공지 갱신 실패: %s", exc, exc_info=True)


def refresh_meals_job() -> None:
    """학식 식단을 크롤링해 CSV 저장 후 meals 인덱스를 재구축한다."""
    from src.crawlers.dongguk_meals import crawl_meals
    from src.pipelines.ingest import ingest_meals

    start = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    logger.info("[scheduler] 학식 갱신 시작 (%s)", start)
    try:
        df = crawl_meals(days_ahead=13)
        if df.empty:
            logger.warning("[scheduler] 학식 수집 0건 — 기존 인덱스 보존(갱신 건너뜀)")
            return
        out_path = DATA_SOURCES["meals"]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        chunks_df, _, _ = ingest_meals()
        logger.info("[scheduler] 학식 갱신 완료: %s행 → %s chunks", len(df), len(chunks_df))
    except Exception as exc:  # noqa: BLE001
        logger.error("[scheduler] 학식 갱신 실패: %s", exc, exc_info=True)


def start_scheduler():
    """RAG_SCHEDULER_ENABLED=1 이면 백그라운드 스케줄러를 시작한다. 시작된 인스턴스를 반환(없으면 None)."""
    global _scheduler
    if not RAG_SCHEDULER_ENABLED:
        logger.info("[scheduler] 비활성(RAG_SCHEDULER_ENABLED=0) — 데이터 자동 갱신을 건너뜁니다.")
        return None
    if _scheduler is not None:
        return _scheduler

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        logger.warning("[scheduler] APScheduler 미설치 — 자동 갱신을 건너뜁니다(requirements.txt 확인).")
        return None

    scheduler = BackgroundScheduler(timezone="Asia/Seoul")
    job_defaults = dict(max_instances=1, coalesce=True, misfire_grace_time=3600)

    # 시작 직후 한 번 실행(콜드 배포 시 데이터 신선화) + 이후 주기 실행.
    now = datetime.now(KST)
    scheduler.add_job(
        refresh_notices_job,
        IntervalTrigger(hours=RAG_NOTICES_REFRESH_HOURS, start_date=now + timedelta(seconds=60)),
        id="refresh_notices", **job_defaults,
    )
    scheduler.add_job(
        refresh_meals_job,
        IntervalTrigger(hours=RAG_MEALS_REFRESH_HOURS, start_date=now + timedelta(seconds=120)),
        id="refresh_meals", **job_defaults,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "[scheduler] 시작됨 — 공지 %sh 주기, 학식 %sh 주기",
        RAG_NOTICES_REFRESH_HOURS, RAG_MEALS_REFRESH_HOURS,
    )
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass
        _scheduler = None


__all__ = ["start_scheduler", "shutdown_scheduler", "refresh_notices_job", "refresh_meals_job"]
