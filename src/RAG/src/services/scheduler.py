"""공지/학식/교과과정 데이터의 주기적 자동 갱신 스케줄러 (rag-service 프로세스 내부).

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

import httpx
import pandas as pd

from src.config import (
    RAG_NOTICES_REFRESH_MAX_PAGES,
    RAG_SCHEDULER_ALERT_TIMEOUT_SECONDS,
    RAG_SCHEDULER_ALERT_WEBHOOK_URL,
    RAG_SCHEDULER_ENABLED,
    RAG_SCHEDULER_REQUEST_RETRIES,
    RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS,
)
from src.database import IngestionRun, SessionLocal, kst_now
from src.services.ingest_runtime import ingestion_run_context

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))

_scheduler = None  # 단일 인스턴스 보관(중복 시작 방지)

# 작업별 마지막 실행 기록. 관리자 화면에서 "돌긴 돌았나"를 로그를 뒤지지 않고 확인하기 위한 것.
# 프로세스 메모리에만 두므로 재시작하면 비어 있고, 그 경우 화면은 '기록 없음'으로 표시한다.
_LAST_RUNS: dict[str, dict[str, str | None]] = {}

JOB_LABELS = {
    "refresh_notices": "공지 수집",
    "refresh_rules": "현행 규정 수집",
    "refresh_schedule": "학사일정 수집",
    "refresh_meals": "학식 수집",
    "refresh_courses": "교과과정 수집",
}


def _send_scheduler_alert(job_id: str, status: str, message: str | None) -> None:
    """Send a content-free operations alert without affecting ingestion."""
    if not RAG_SCHEDULER_ALERT_WEBHOOK_URL or status not in {"partial", "failed"}:
        return
    occurred_at = datetime.now(KST).isoformat()
    label = JOB_LABELS.get(job_id, job_id)
    text = f"[동똑이 RAG] {label} {status}: {message or '상세 없음'}"
    payload = {
        "schema_version": 1,
        "service": "dongttok-rag",
        "event": "scheduler_run",
        "job_id": job_id,
        "job_name": label,
        "status": status,
        "message": message,
        "occurred_at": occurred_at,
        # Slack incoming webhooks consume `text`; generic receivers can use the
        # structured fields above.
        "text": text,
    }
    try:
        response = httpx.post(
            RAG_SCHEDULER_ALERT_WEBHOOK_URL,
            json=payload,
            timeout=RAG_SCHEDULER_ALERT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - observability must not break ingestion.
        logger.warning("[scheduler] 운영 경보 전송 실패 job=%s status=%s: %s", job_id, status, exc)


def _record_run(job_id: str, status: str, message: str | None = None) -> None:
    _LAST_RUNS[job_id] = {
        "last_run_at": datetime.now(KST).isoformat(),
        "last_status": status,
        "last_message": message,
    }
    _send_scheduler_alert(job_id, status, message)


def _start_ingestion_run(dataset: str) -> int | None:
    """실행 기록을 열고 id를 돌려준다. 기록에 실패하면 None.

    기록은 관측 수단이므로 갱신 작업 자체를 막아서는 안 된다. 테이블이 아직 없거나
    DB가 잠긴 상황에서 예외가 새어 나가면, 공지·학식 수집이 기록 때문에 중단된다.
    """
    session = SessionLocal()
    try:
        run = IngestionRun(dataset=dataset, status="running")
        session.add(run)
        session.commit()
        session.refresh(run)
        return int(run.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scheduler] 실행 기록 시작 실패(%s): %s", dataset, exc)
        session.rollback()
        return None
    finally:
        session.close()


def _finish_ingestion_run(
    run_id: int | None,
    *,
    status: str,
    seen: int = 0,
    failed: int = 0,
    error: str | None = None,
) -> None:
    """실행 기록을 닫는다. 시작 기록이 없었으면(None) 조용히 넘어간다."""
    if run_id is None:
        return
    session = SessionLocal()
    try:
        run = session.query(IngestionRun).filter(IngestionRun.id == run_id).first()
        if run is None:
            return
        run.status = status
        run.finished_at = kst_now()
        run.documents_seen = seen
        run.documents_failed = failed
        run.error_summary = error
        session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scheduler] 실행 기록 종료 실패(run_id=%s): %s", run_id, exc)
        session.rollback()
    finally:
        session.close()


def get_scheduler_status() -> dict:
    """등록된 자동 작업의 다음 실행 시각과 마지막 결과를 돌려준다."""
    jobs = []
    scheduler = _scheduler
    if scheduler is not None:
        for job in scheduler.get_jobs():
            history = _LAST_RUNS.get(job.id, {})
            next_run = getattr(job, "next_run_time", None)
            jobs.append({
                "id": job.id,
                "name": JOB_LABELS.get(job.id, job.id),
                "next_run_at": next_run.isoformat() if next_run else None,
                "trigger": str(job.trigger),
                "last_run_at": history.get("last_run_at"),
                "last_status": history.get("last_status"),
                "last_message": history.get("last_message"),
            })
    else:
        # 스케줄러가 꺼져 있어도 수동 실행 기록은 보여 준다.
        for job_id, history in _LAST_RUNS.items():
            jobs.append({
                "id": job_id,
                "name": JOB_LABELS.get(job_id, job_id),
                "next_run_at": None,
                "trigger": None,
                "last_run_at": history.get("last_run_at"),
                "last_status": history.get("last_status"),
                "last_message": history.get("last_message"),
            })

    return {"enabled": bool(RAG_SCHEDULER_ENABLED and scheduler is not None), "jobs": jobs}


def _refresh_runtime_dataset_state(dataset: str) -> None:
    """Make scheduler-written artifacts visible to the serving process at once."""
    try:
        import importlib

        rag_service = importlib.import_module("api.rag_service")

        snapshot = rag_service.refresh_runtime_dataset_state([dataset])
        if dataset == "schedule":
            context_builder = getattr(
                rag_service,
                "_build_temporal_context_for_date",
                None,
            )
            cache_clear = getattr(context_builder, "cache_clear", None)
            if callable(cache_clear):
                cache_clear()
        logger.info(
            "[scheduler] 런타임 캐시 갱신 dataset=%s chunks=%s dense=%s",
            dataset,
            snapshot.get("counts", {}).get(dataset),
            snapshot.get("dense_counts", {}).get(dataset),
        )
    except Exception as exc:  # noqa: BLE001 - sync itself succeeded; expose reload failure in logs
        logger.error("[scheduler] 런타임 캐시 갱신 실패 dataset=%s: %s", dataset, exc, exc_info=True)


def refresh_notices_job() -> None:
    """공지 게시판 최근 페이지를 크롤링해 증분 동기화 + 인덱스 갱신한다."""
    from src.crawlers.dongguk_notices import crawl_notices
    from src.pipelines.notices_sync import (
        load_known_article_ids_by_board,
        record_notice_ingestion_failure,
        sync_notices,
    )

    start = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    logger.info(
        "[scheduler] 공지 갱신 시작 (%s) request_timeout=%ss request_retries=%s",
        start,
        RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS,
        RAG_SCHEDULER_REQUEST_RETRIES,
    )
    try:
        try:
            known_ids_by_board = load_known_article_ids_by_board()
        except Exception:  # noqa: BLE001 — 기존 수집 ID 로드는 조기 중단 최적화일 뿐이다.
            known_ids_by_board = None
        try:
            df = crawl_notices(
                known_ids_by_board=known_ids_by_board,
                max_pages=RAG_NOTICES_REFRESH_MAX_PAGES,
                delay=0.2,
                request_timeout=RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS,
                request_retries=RAG_SCHEDULER_REQUEST_RETRIES,
            )
        except Exception as exc:  # noqa: BLE001 - 수집 전 실패도 durable history에 남긴다.
            try:
                record_notice_ingestion_failure(exc, stage="scheduled_crawl")
            except Exception as audit_exc:  # noqa: BLE001
                logger.warning("[scheduler] 공지 실패 실행 기록 저장 실패: %s", audit_exc)
            raise
        crawl_attrs = dict(df.attrs)
        try:
            from src.crawlers.dongguk_library_hours import fetch_library_operation_times
            from src.services.library_hours import (
                normalize_library_operation_times,
                to_notice_shaped_records,
            )

            library_fetch = fetch_library_operation_times(
                timeout=RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS,
            )
            library_rows = to_notice_shaped_records(
                normalize_library_operation_times(library_fetch)
            )
            if library_rows:
                df = pd.concat([df, pd.DataFrame(library_rows)], ignore_index=True, sort=False)
                df.attrs.update(crawl_attrs)
        except Exception as exc:  # noqa: BLE001 - 공지 전체 갱신은 도서관 API와 독립적이다.
            logger.warning("[scheduler] 도서관 운영시간 병합 실패 — 공지만 갱신: %s", exc)
        summary = sync_notices(df, allow_missing_detection=False, mode="full-sync")
        _refresh_runtime_dataset_state("notices")
        logger.info(
            "[scheduler] 공지 갱신 완료 seen=%s new=%s updated=%s deleted=%s failed=%s incomplete_boards=%s",
            summary.get("seen"), summary.get("new"), summary.get("updated"),
            summary.get("deleted"), summary.get("failed"), summary.get("incomplete_boards"),
        )
        _record_run(
            "refresh_notices",
            "partial" if summary.get("incomplete_boards") else "ok",
            f"신규 {summary.get('new', 0)} · 수정 {summary.get('updated', 0)} · "
            f"실패 {summary.get('failed', 0)} · 미완료 게시판 {summary.get('incomplete_boards', 0)}",
        )
        _start_faq_draft_worker()
    except Exception as exc:  # noqa: BLE001 — 한 번의 실패가 스케줄러를 죽이지 않도록
        logger.error("[scheduler] 공지 갱신 실패: %s", exc, exc_info=True)
        _record_run("refresh_notices", "failed", str(exc))


def _start_faq_draft_worker() -> None:
    """공지 갱신이 **성공한 뒤** FAQ 초안 생성을 별도 스레드에서 돌린다.

    이 호출은 원래 except 블록 안에 있어 공지 갱신이 실패했을 때만 돌았다.
    주석은 "수집 완료 시"였지만 동작은 정반대였다.

    스레드로 빼는 이유는 초안 생성이 질의 로그 전수를 훑기 때문이다. 스케줄러
    작업을 붙잡아 다음 갱신 주기를 밀면 안 된다. 실패해도 공지 갱신 결과에
    영향을 주지 않는다.
    """
    import asyncio
    import threading

    def _worker() -> None:
        async def _run() -> None:
            from src.services.auto_faq import generate_faq_drafts

            결과 = await generate_faq_drafts()
            logger.info(
                "[auto_faq] 초안 %d건 등록 (후보 %d · 기존 %d · 상한 초과 %d)",
                결과["created"], 결과["candidates"],
                결과["skipped_existing"], 결과["truncated"],
            )

        try:
            asyncio.run(_run())
        except Exception as exc:  # noqa: BLE001 - 관측 기능이 본 작업을 대신 죽이지 않는다
            logger.error("[auto_faq] 자동 FAQ 초안 생성 실패: %s", exc, exc_info=True)

    threading.Thread(target=_worker, daemon=True, name="auto_faq").start()


def refresh_rules_job() -> None:
    """공식 학사 규정 현행판을 보존적으로 병합하고 인덱스를 교체한다."""
    from scripts.sync_official_rules import sync_rule_source
    from src.pipelines.ingest import ingest_rules

    start = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    logger.info("[scheduler] 현행 규정 갱신 시작 (%s)", start)
    run_id = _start_ingestion_run("rules")
    try:
        official = sync_rule_source()
        if official.empty:
            raise RuntimeError("공식 현행 규정 수집 결과가 비었습니다.")
        expected_versions = set(official["source_version"].astype(str))
        session = SessionLocal()
        try:
            from src.database import Rule

            indexed_versions = {
                str(value)
                for (value,) in session.query(Rule.source_version)
                .filter(Rule.source_version.isnot(None))
                .all()
                if str(value or "")
            }
        finally:
            session.close()
        if not official.attrs.get("source_changed") and expected_versions <= indexed_versions:
            message = f"변경 없음 · 공식 {len(official)}건"
            _record_run("refresh_rules", "ok", message)
            _finish_ingestion_run(run_id, status="success", seen=len(official))
            logger.info("[scheduler] 현행 규정 변경 없음 — 재임베딩 건너뜀")
            return
        with ingestion_run_context("rules", run_id):
            chunks, _, _ = ingest_rules(force_source_reload=True)
        _refresh_runtime_dataset_state("rules")
        _record_run(
            "refresh_rules",
            "ok",
            f"공식 {len(official)}건 · {len(chunks)} chunks",
        )
        _finish_ingestion_run(
            run_id,
            status="success",
            seen=len(official),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[scheduler] 현행 규정 갱신 실패: %s", exc, exc_info=True)
        _record_run("refresh_rules", "failed", str(exc))
        _finish_ingestion_run(run_id, status="failed", failed=1, error=str(exc))


def refresh_meals_job() -> None:
    """학식 식단을 크롤링해 canonical DB에 저장 후 meals 인덱스를 재구축한다."""
    from src.crawlers.dongguk_meals import crawl_meals
    from src.pipelines.ingest import ingest_meals

    start = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    logger.info(
        "[scheduler] 학식 갱신 시작 (%s) request_timeout=%ss request_retries=%s",
        start,
        RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS,
        RAG_SCHEDULER_REQUEST_RETRIES,
    )
    run_id = _start_ingestion_run("meals")
    try:
        df = crawl_meals(
            days_ahead=13,
            request_timeout=RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS,
            request_retries=RAG_SCHEDULER_REQUEST_RETRIES,
        )
        if df.empty:
            logger.warning("[scheduler] 학식 수집 0건 — 기존 인덱스 보존(갱신 건너뜀)")
            _record_run("refresh_meals", "skipped", "수집 0건 — 기존 인덱스 보존")
            _finish_ingestion_run(
                run_id,
                status="partial",
                error="수집 0건 — 기존 인덱스 보존",
            )
            return
        with ingestion_run_context("meals", run_id):
            chunks_df, _, _ = ingest_meals(df)
        _refresh_runtime_dataset_state("meals")
        logger.info("[scheduler] 학식 갱신 완료: %s행 → %s chunks", len(df), len(chunks_df))
        _record_run("refresh_meals", "ok", f"{len(df)}행 → {len(chunks_df)} chunks")
        _finish_ingestion_run(run_id, status="success", seen=len(chunks_df))
    except Exception as exc:  # noqa: BLE001
        logger.error("[scheduler] 학식 갱신 실패: %s", exc, exc_info=True)
        _record_run("refresh_meals", "failed", str(exc))
        _finish_ingestion_run(run_id, status="failed", error=str(exc))


def _merge_schedule_snapshots(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    """수집된 학년도만 교체하고 다른 연도의 과거 일정은 보존한다."""
    if existing.empty or "학년도" not in existing.columns or "학년도" not in incoming.columns:
        return incoming.copy()
    incoming_years = {
        str(value).strip()
        for value in incoming["학년도"].tolist()
        if str(value).strip()
    }
    existing_years = existing["학년도"].astype(str).str.strip()
    preserved = existing[~existing_years.isin(incoming_years)]
    if incoming_years:
        # 초기/구버전 정본에는 학년도가 비어 있는 행이 있다. 새 공식
        # 수집본에 학년도가 붙어 있으면 이 공백 스냅샷을 과거 연도로
        # 보존하지 말고 교체해야 동일 일정이 두 번 들어가지 않는다.
        preserved = preserved[existing_years.loc[preserved.index] != ""]
    merged = pd.concat([preserved, incoming], ignore_index=True)
    identity = [
        name
        for name in ("학년도", "내용", "start", "end", "주관부서")
        if name in merged.columns
    ]
    return merged.drop_duplicates(subset=identity or None, keep="last")


def refresh_schedule_job() -> None:
    """공식 학사일정 표를 다시 수집하고 schedule 인덱스를 재구축한다."""
    from src.crawlers.dongguk_schedule import (
        SCHEDULE_URL,
        fetch_schedule_html,
        parse_schedule,
    )
    from src.pipelines.ingest import (
        ingest_schedule,
        load_canonical_source_frame,
    )

    start = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    logger.info("[scheduler] 학사일정 갱신 시작 (%s)", start)
    run_id = _start_ingestion_run("schedule")
    try:
        html = fetch_schedule_html(
            SCHEDULE_URL,
            timeout=RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS,
        )
        frame = parse_schedule(html)
        if frame.empty:
            logger.warning("[scheduler] 학사일정 수집 0건 — 기존 인덱스 보존")
            _record_run("refresh_schedule", "skipped", "수집 0건 — 기존 인덱스 보존")
            _finish_ingestion_run(
                run_id,
                status="partial",
                error="수집 0건 — 기존 인덱스 보존",
            )
            return
        output = frame[["학년도", "구분", "내용", "주관부서", "start", "end"]].copy()
        # Past academic years are merged from canonical SourceDocument payloads.
        # The legacy schedule CSV is not consulted during a scheduled refresh.
        session = SessionLocal()
        try:
            canonical = load_canonical_source_frame(session, "schedule")
        finally:
            session.close()
        if not canonical.empty and "academic_year" in canonical.columns:
            existing = pd.DataFrame({
                "학년도": canonical.get("academic_year", ""),
                "구분": canonical.get("category", ""),
                "내용": canonical.get("content", ""),
                "주관부서": canonical.get("department", ""),
                "start": canonical.get("start_date", ""),
                "end": canonical.get("end_date", ""),
            }).fillna("").astype(str)
            output = _merge_schedule_snapshots(existing, output)
        with ingestion_run_context("schedule", run_id):
            chunks_df, _, _ = ingest_schedule(output, refresh_from_csv=True)
        _refresh_runtime_dataset_state("schedule")
        logger.info(
            "[scheduler] 학사일정 갱신 완료: %s행 → %s chunks",
            len(output),
            len(chunks_df),
        )
        _record_run(
            "refresh_schedule",
            "ok",
            f"{len(output)}행 → {len(chunks_df)} chunks",
        )
        _finish_ingestion_run(
            run_id,
            status="success",
            seen=len(output),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[scheduler] 학사일정 갱신 실패: %s", exc, exc_info=True)
        _record_run("refresh_schedule", "failed", str(exc))
        _finish_ingestion_run(
            run_id,
            status="partial",
            failed=1,
            error=str(exc),
        )


def refresh_courses_job() -> None:
    """학과별 교과과정을 다시 수집하고 courses 인덱스를 갱신합니다."""
    from src.crawlers.dongguk_department_curriculum_content import main as crawl_courses
    from src.pipelines.ingest import ingest_courses

    start = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    logger.info("[scheduler] 교과과정 갱신 시작 (%s)", start)
    # `_record_run`은 메모리 딕셔너리라 재시작하면 사라진다. 이 잡이 실패해도 흔적이
    # 남지 않아, 색인이 45개 학과에 멈춰 있는 동안 아무도 알아채지 못했다(수집 CSV에는
    # 71개 학과가 있었다). rules·schedule처럼 ingestion_runs에도 남긴다.
    run_id = _start_ingestion_run("courses")
    try:
        crawl_courses()
        with ingestion_run_context("courses", run_id):
            chunks_df, _, _ = ingest_courses(refresh_from_csv=True)
        _refresh_runtime_dataset_state("courses")
        logger.info("[scheduler] 교과과정 갱신 완료: %s chunks", len(chunks_df))
        _record_run("refresh_courses", "ok", f"{len(chunks_df)} chunks")
        _finish_ingestion_run(run_id, status="success", seen=len(chunks_df))
    except Exception as exc:  # noqa: BLE001
        logger.error("[scheduler] 교과과정 갱신 실패: %s", exc, exc_info=True)
        _record_run("refresh_courses", "failed", str(exc))
        _finish_ingestion_run(run_id, status="failed", error=str(exc))


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
    # 기본: 공지 매일 0/6/12/18시, 규정 매주 일요일 02:00, 학사일정 매일 05:00,
    # 학식 매일 04:30, 교과과정 매주 일요일 03:00.
    notices_cron = os.getenv("RAG_NOTICES_REFRESH_CRON", "0 0,6,12,18 * * *")
    rules_cron = os.getenv("RAG_RULES_REFRESH_CRON", "0 2 * * 0")
    schedule_cron = os.getenv("RAG_SCHEDULE_REFRESH_CRON", "0 5 * * *")
    meals_cron = os.getenv("RAG_MEALS_REFRESH_CRON", "30 4 * * *")
    courses_cron = os.getenv("RAG_COURSES_REFRESH_CRON", "0 3 * * 0")
    scheduler.add_job(
        refresh_notices_job,
        CronTrigger.from_crontab(notices_cron, timezone="Asia/Seoul"),
        id="refresh_notices", **job_defaults,
    )
    scheduler.add_job(
        refresh_rules_job,
        CronTrigger.from_crontab(rules_cron, timezone="Asia/Seoul"),
        id="refresh_rules", **job_defaults,
    )
    scheduler.add_job(
        refresh_schedule_job,
        CronTrigger.from_crontab(schedule_cron, timezone="Asia/Seoul"),
        id="refresh_schedule", **job_defaults,
    )
    scheduler.add_job(
        refresh_meals_job,
        CronTrigger.from_crontab(meals_cron, timezone="Asia/Seoul"),
        id="refresh_meals", **job_defaults,
    )
    scheduler.add_job(
        refresh_courses_job,
        CronTrigger.from_crontab(courses_cron, timezone="Asia/Seoul"),
        id="refresh_courses", **job_defaults,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "[scheduler] 시작됨 — 공지 cron='%s', 규정 cron='%s', 학사일정 cron='%s', 학식 cron='%s', 교과과정 cron='%s' (부팅 시 즉시 실행 안 함)",
        notices_cron, rules_cron, schedule_cron, meals_cron, courses_cron,
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


__all__ = [
    "start_scheduler",
    "shutdown_scheduler",
    "get_scheduler_status",
    "refresh_notices_job",
    "refresh_rules_job",
    "refresh_schedule_job",
    "refresh_meals_job",
    "refresh_courses_job",
]
