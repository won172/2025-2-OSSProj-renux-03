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
import os
from datetime import datetime, timedelta, timezone

from src.config import (
    DATA_SOURCES,
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
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("[scheduler] APScheduler 미설치 — 자동 갱신을 건너뜁니다(requirements.txt 확인).")
        return None

    scheduler = BackgroundScheduler(timezone="Asia/Seoul")
    # 부팅 시 따라잡기(catch-up) 실행을 막기 위해 misfire 유예를 짧게 둔다.
    # → 컨테이너를 켜도 '정해진 시각'이 아니면 수집하지 않는다.
    job_defaults = dict(max_instances=1, coalesce=True, misfire_grace_time=120)

    # 고정 시각(cron)에만 실행 — docker up 때마다 수집하지 않는다.
    # 기본: 공지 매일 0/6/12/18시 정각(4회), 학식 매일 04:30. env(cron 식)로 재정의 가능.
    notices_cron = os.getenv("RAG_NOTICES_REFRESH_CRON", "0 0,6,12,18 * * *")
    meals_cron = os.getenv("RAG_MEALS_REFRESH_CRON", "30 4 * * *")
    scheduler.add_job(
        refresh_notices_job,
        CronTrigger.from_crontab(notices_cron, timezone="Asia/Seoul"),
        id="refresh_notices", **job_defaults,
    )
    scheduler.add_job(
        refresh_meals_job,
        CronTrigger.from_crontab(meals_cron, timezone="Asia/Seoul"),
        id="refresh_meals", **job_defaults,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "[scheduler] 시작됨 — 공지 cron='%s', 학식 cron='%s' (부팅 시 즉시 실행 안 함)",
        notices_cron, meals_cron,
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
