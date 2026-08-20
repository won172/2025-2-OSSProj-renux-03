"""자주 묻는데 잘 답하지 못하는 질문을 FAQ 초안으로 올린다.

크롤링으로는 메울 수 없는 데이터 공백(보강 일정, 총학생회비, 학과 행정 연락처 등)은
사람이 직접 채워야 한다. 그 입력 경로는 이미 있다 — 제출 → 관리자 검토 → 승인 →
`Notice(board="학과지식")`. 막힌 곳은 편집 수단이 아니라 **무엇을 채워야 하는지**다.
그래서 실제 질문 로그에서 반복되는데 폴백하거나 근거검증에 실패한 질문을 뽑아
검토 큐에 올린다.

## 답변을 지어내지 않는다

초안의 `answer`에는 관측 결과만 적고 답은 비워 둔다. 이 목록에 오르는 질문은
정의상 **시스템이 답을 모르는 것**이라, 검색 결과를 요약해 답처럼 넣으면
사람이 검토하다 그대로 승인해 버릴 위험이 있다. 대신 몇 번 물었고 어떻게
실패했는지를 근거로 남겨 작성자가 직접 쓰게 한다.

## 왜 source_type이 custom_knowledge인가

승인 경로(`_build_notice_from_pending`)가 아는 타입은 custom_knowledge / event /
announcement 뿐이다. 다른 이름으로 넣으면 검토 큐에 뜨더라도 승인 시 None이 되어
아무것도 만들어지지 않는다.

실행: `scheduler.refresh_notices_job()` 성공 후 백그라운드 스레드에서 호출된다.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import timedelta
from typing import Dict, List

logger = logging.getLogger(__name__)

# 평가 하네스가 만든 로그. 이것을 세면 골든셋 문항이 FAQ 초안으로 올라온다.
# (api/rag_service._SYNTHETIC_REQUEST_PREFIXES와 같은 의미다.)
_SYNTHETIC_REQUEST_PREFIXES = ("eval_", "golden-")

_WHITESPACE = re.compile(r"\s+")

FAQ_LOOKBACK_DAYS = 14
FAQ_MIN_COUNT = 3
FAQ_MAX_DRAFTS = 20


def _normalize(question: str) -> str:
    """표기 차이로 같은 질문이 갈라지지 않게 최소한만 정규화한다."""
    return _WHITESPACE.sub(" ", (question or "").strip()).lower()


def _is_synthetic(request_id: str | None, as_of: str | None, created_at) -> bool:
    """평가 러너 요청이거나 기준일을 옮겨 물은 요청인지."""
    if request_id and request_id.startswith(_SYNTHETIC_REQUEST_PREFIXES):
        return True
    if as_of and created_at is not None and as_of != created_at.strftime("%Y-%m-%d"):
        return True
    return False


def collect_gap_questions(
    *, days: int = FAQ_LOOKBACK_DAYS, min_count: int = FAQ_MIN_COUNT
) -> List[Dict]:
    """반복해서 묻는데 잘 답하지 못한 질문을 빈도순으로 모은다.

    "잘 답하지 못했다"는 폴백했거나 근거검증에서 grounded=False가 난 경우다.
    한 번이라도 실패한 적 있는 질문만 남긴다 — 매번 잘 답하는 질문은 FAQ가 필요없다.
    """
    from src.database import RagQueryLog, SessionLocal, kst_now

    session = SessionLocal()
    try:
        cutoff = kst_now() - timedelta(days=days)
        rows = (
            session.query(RagQueryLog)
            .filter(RagQueryLog.created_at >= cutoff)
            .filter(RagQueryLog.question.isnot(None))
            .all()
        )
    except Exception as exc:  # noqa: BLE001 - 테이블이 없는 초기 배포도 지나가야 한다
        logger.warning("[auto_faq] 질의 로그를 읽지 못했습니다: %s", exc)
        return []
    finally:
        session.close()

    묶음: Dict[str, Dict] = defaultdict(
        lambda: {"count": 0, "fallback": 0, "ungrounded": 0, "reasons": defaultdict(int), "sample": ""}
    )
    for row in rows:
        if _is_synthetic(row.request_id, row.as_of, row.created_at):
            continue
        key = _normalize(row.question)
        if len(key) < 4:
            continue
        기록 = 묶음[key]
        기록["count"] += 1
        기록["sample"] = 기록["sample"] or row.question.strip()
        if row.fallback_triggered:
            기록["fallback"] += 1
            if row.fallback_reason:
                기록["reasons"][row.fallback_reason] += 1
        if row.grounding_checked and row.grounding_grounded is False:
            기록["ungrounded"] += 1

    후보 = [
        {
            "question": 값["sample"],
            "count": 값["count"],
            "fallback": 값["fallback"],
            "ungrounded": 값["ungrounded"],
            "reasons": dict(값["reasons"]),
            "missing_terms": missing_from_corpus(값["sample"]),
        }
        for 값 in 묶음.values()
        if 값["count"] >= min_count and (값["fallback"] or 값["ungrounded"])
    ]
    후보.sort(key=lambda d: (d["fallback"] + d["ungrounded"], d["count"]), reverse=True)
    return 후보


def _already_covered() -> set[str]:
    """이미 제출됐거나 승인된 질문. 같은 것을 반복해 올리지 않는다."""
    from src.database import Notice, PendingItem, SessionLocal

    covered: set[str] = set()
    session = SessionLocal()
    try:
        for item in session.query(PendingItem).filter(
            PendingItem.source_type == "custom_knowledge"
        ):
            try:
                data = json.loads(item.data) if isinstance(item.data, str) else (item.data or {})
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict) and data.get("question"):
                covered.add(_normalize(str(data["question"])))
        for notice in session.query(Notice).filter(Notice.board == "학과지식"):
            if notice.title:
                covered.add(_normalize(notice.title))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[auto_faq] 기존 FAQ 목록을 읽지 못했습니다: %s", exc)
    finally:
        session.close()
    return covered


def missing_from_corpus(question: str) -> List[str]:
    """질문의 내용어 중 어느 데이터셋 코퍼스에도 없는 낱말.

    "검색이 못 찾았다"와 "그런 자료가 아예 없다"는 처방이 다르다. 앞은 랭킹을
    손볼 일이고, 뒤는 사람이 자료를 채워야 한다. 폴백 로그만으로는 둘이 똑같이
    보인다 — 골든 70건에서 기대 키워드의 24.5%p가 코퍼스에 아예 없었다.

    질문어(`알려줘`, `얼마야`)와 n-gram 조각(`려줘`)은 빼고 내용어만 본다.
    그것들은 원래 코퍼스에 없는 게 정상이라, 두면 신호가 잡음에 묻힌다.
    """
    from src.pipelines.ingest import DATASET_ARTIFACTS
    from src.search.fts_index import absent_terms
    from src.search.hybrid import content_tokens

    # 색인과 같은 토크나이저로 잘라야 한다. 다르면 조사가 붙은 형태로 조회해
    # 코퍼스에 있는 낱말을 없다고 보고한다(Kiwi 전환 때 실제로 그랬다).
    내용어 = content_tokens(question)
    if not 내용어:
        return []

    남은 = set(내용어)
    for key in DATASET_ARTIFACTS:
        if not 남은:
            break
        남은 &= set(absent_terms(key, 남은))
    return [t for t in 내용어 if t in 남은]


def _observation_note(후보: Dict, days: int) -> str:
    """작성자가 답을 쓰도록 관측 근거만 남긴다. 답을 지어내지 않는다."""
    줄 = [
        "⚠️ 질문 로그에서 자동으로 올라온 초안입니다. 답변을 직접 작성한 뒤 승인하세요.",
        "",
        f"최근 {days}일간 {후보['count']}회 질문",
    ]
    if 후보["fallback"]:
        사유 = ", ".join(f"{k} {v}회" for k, v in sorted(후보["reasons"].items(), key=lambda x: -x[1]))
        줄.append(f"답변 실패(폴백) {후보['fallback']}회" + (f" — {사유}" if 사유 else ""))
    if 후보["ungrounded"]:
        줄.append(f"근거검증 실패 {후보['ungrounded']}회")
    없는말 = 후보.get("missing_terms") or []
    if 없는말:
        # 자료를 새로 써야 하는 질문과, 있는 자료를 못 찾은 질문을 구분해 준다.
        줄.append(f"코퍼스에 없는 낱말: {', '.join(없는말)} — 검색이 아니라 자료가 없는 쪽입니다.")
    return "\n".join(줄)


def _register_draft(후보: Dict, days: int) -> bool:
    from src.database import PendingItem, SessionLocal

    session = SessionLocal()
    try:
        session.add(
            PendingItem(
                source_type="custom_knowledge",
                status="pending",
                data=json.dumps(
                    {
                        "question": 후보["question"],
                        "answer": _observation_note(후보, days),
                        "suggested_by": "auto_faq",
                        "observed_count": 후보["count"],
                        "observed_fallback": 후보["fallback"],
                        "observed_ungrounded": 후보["ungrounded"],
                        "missing_terms": 후보.get("missing_terms") or [],
                    },
                    ensure_ascii=False,
                ),
            )
        )
        session.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.error("[auto_faq] 초안 등록 실패(%s): %s", 후보["question"][:24], exc)
        return False
    finally:
        session.close()


async def generate_faq_drafts(
    *, days: int = FAQ_LOOKBACK_DAYS, min_count: int = FAQ_MIN_COUNT, limit: int = FAQ_MAX_DRAFTS
) -> dict:
    """공지 수집 후 FAQ 초안을 만든다.

    scheduler가 `asyncio.run`으로 부르므로 async 시그니처를 유지한다.
    """
    결과 = {"created": 0, "skipped_existing": 0, "candidates": 0, "truncated": 0}

    후보들 = collect_gap_questions(days=days, min_count=min_count)
    결과["candidates"] = len(후보들)
    if not 후보들:
        logger.info("[auto_faq] 최근 %d일간 초안 대상 질문 없음", days)
        return 결과

    covered = _already_covered()
    남은 = [c for c in 후보들 if _normalize(c["question"]) not in covered]
    결과["skipped_existing"] = len(후보들) - len(남은)

    if len(남은) > limit:
        # 상한을 조용히 적용하면 "다 처리했다"로 읽힌다. 잘라낸 수를 남긴다.
        결과["truncated"] = len(남은) - limit
        남은 = 남은[:limit]

    for 후보 in 남은:
        if _register_draft(후보, days):
            결과["created"] += 1

    logger.info(
        "[auto_faq] 초안 %d건 등록 (후보 %d · 기존 %d · 상한 초과 %d)",
        결과["created"], 결과["candidates"], 결과["skipped_existing"], 결과["truncated"],
    )
    return 결과


async def _on_notices_refreshed() -> None:
    """refresh_notices_job() 성공 후 호출되는 콜백."""
    try:
        결과 = await generate_faq_drafts()
        logger.info("[auto_faq] 결과 → %s", json.dumps(결과, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        logger.error("[auto_faq] 자동 FAQ 생성 실패: %s", exc, exc_info=True)


if __name__ == "__main__":
    import asyncio

    print(json.dumps(asyncio.run(generate_faq_drafts()), ensure_ascii=False, indent=2))
