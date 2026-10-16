from __future__ import annotations

from datetime import date
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.rag_service import _build_notification_candidates_from_frames  # noqa: E402


def test_notification_candidates_filters_and_deduplicates_notice_deadlines():
    notices = pd.DataFrame(
        [
            {
                "chunk_id": "notice-1-a",
                "doc_id": "notice-1",
                "notice_id": 1,
                "position": 0,
                "title": "교외장학 서류 제출 안내",
                "topics": "장학공지",
                "category": "장학",
                "published_at": "2026-07-01",
                "apply_deadline": "2026-07-10",
                "url": "https://example.com/1",
                "chunk_text": "서류 제출 기한 안내",
            },
            {
                "chunk_id": "notice-1-b",
                "doc_id": "notice-1",
                "notice_id": 1,
                "position": 1,
                "title": "교외장학 서류 제출 안내",
                "topics": "장학공지",
                "category": "장학",
                "published_at": "2026-07-01",
                "apply_deadline": "2026-07-10",
                "url": "https://example.com/1",
                "chunk_text": "두 번째 청크",
            },
            {
                "chunk_id": "notice-old",
                "doc_id": "notice-old",
                "notice_id": 2,
                "position": 0,
                "title": "지난 공지",
                "topics": "일반공지",
                "category": "일반",
                "published_at": "2026-06-01",
                "apply_deadline": "2026-06-30",
                "url": "https://example.com/2",
                "chunk_text": "지난 마감",
            },
        ]
    )

    candidates = _build_notification_candidates_from_frames(
        notices_df=notices,
        schedule_df=pd.DataFrame(),
        start=date(2026, 7, 1),
        end=date(2026, 7, 31),
        sources={"notices"},
        limit=20,
        today=date(2026, 7, 6),
    )

    assert len(candidates) == 1
    assert candidates[0].id == "notices:1"
    assert candidates[0].topic == "scholarship"
    assert candidates[0].target_date == "2026-07-10"
    assert candidates[0].d_day == 4


def test_notification_candidates_uses_schedule_end_as_target_date():
    schedule = pd.DataFrame(
        [
            {
                "chunk_id": "schedule-1",
                "schedule_id": 11,
                "title": "수강신청 정정",
                "schedule_start": "2026-09-01",
                "schedule_end": "2026-09-05",
                "category": "학사",
                "department": "교무팀",
                "chunk_text": "학사일정: 수강신청 정정",
            }
        ]
    )

    candidates = _build_notification_candidates_from_frames(
        notices_df=pd.DataFrame(),
        schedule_df=schedule,
        start=date(2026, 9, 1),
        end=date(2026, 9, 30),
        sources={"schedule"},
        limit=20,
        today=date(2026, 9, 1),
    )

    assert len(candidates) == 1
    assert candidates[0].id == "schedule:11"
    assert candidates[0].topic == "academic_schedule"
    assert candidates[0].target_date == "2026-09-05"
    assert candidates[0].date_source == "schedule_end"


def _scoped_notices() -> pd.DataFrame:
    base = {
        "position": 0,
        "topics": "학과공지",
        "category": "장학",
        "published_at": "2026-07-01",
        "apply_deadline": "2026-07-10",
        "chunk_text": "신청 기한 안내",
    }
    return pd.DataFrame(
        [
            {
                **base,
                "chunk_id": "public-a",
                "doc_id": "public",
                "notice_id": 101,
                "title": "전체 공개 장학 신청",
                "url": "https://www.dongguk.edu/article/101",
                "visibility": "public",
                "department": "",
            },
            {
                **base,
                "chunk_id": "cs-only-a",
                "doc_id": "cs-only",
                "notice_id": 102,
                "title": "컴퓨터공학과 학생 전용 장학 신청",
                "url": "https://cse.dongguk.edu/article/102",
                "visibility": "department",
                "department": "컴퓨터공학과",
            },
            {
                **base,
                "chunk_id": "econ-only-a",
                "doc_id": "econ-only",
                "notice_id": 103,
                "title": "경제학과 학생 전용 장학 신청",
                "url": "https://econ.dongguk.edu/article/103",
                "visibility": "department",
                "department": "경제학과",
            },
            {
                **base,
                "chunk_id": "wise-notice-a",
                "doc_id": "wise-notice",
                "notice_id": 104,
                "title": "WISE캠퍼스 장학 신청 안내",
                "url": "https://www.dongguk.edu/article/104",
                "visibility": "public",
                "department": "",
            },
        ]
    )


def _scoped_schedule() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "chunk_id": "schedule-seoul",
                "schedule_id": 201,
                "title": "정규학기 학점교류 신청",
                "schedule_start": "2026-07-08",
                "schedule_end": "2026-07-09",
                "category": "학사",
                "chunk_text": "학사일정: 정규학기 학점교류 신청",
            },
            {
                "chunk_id": "schedule-wise",
                "schedule_id": 202,
                "title": "WISE캠퍼스 정규학기 학점교류 신청",
                "schedule_start": "2026-07-08",
                "schedule_end": "2026-07-09",
                "category": "학사",
                "chunk_text": "학사일정: WISE캠퍼스 정규학기 학점교류 신청",
            },
        ]
    )


def _scoped_ids(**scope) -> set[str]:
    candidates = _build_notification_candidates_from_frames(
        notices_df=_scoped_notices(),
        schedule_df=_scoped_schedule(),
        start=date(2026, 7, 1),
        end=date(2026, 7, 31),
        sources={"notices", "schedule"},
        limit=50,
        today=date(2026, 7, 6),
        **scope,
    )
    return {candidate.id for candidate in candidates}


def test_notification_candidates_default_scope_is_public_only_without_wise():
    assert _scoped_ids() == {"notices:101", "schedule:201"}


def test_notification_candidates_guest_or_unknown_major_sees_public_only():
    for major in (None, "", "Unknown", "default"):
        assert _scoped_ids(major=major) == {"notices:101", "schedule:201"}, major


def test_notification_candidates_include_own_department_notice_only():
    ids = _scoped_ids(major=" 컴퓨터공학과 ")
    assert "notices:102" in ids
    assert "notices:103" not in ids
    assert "notices:101" in ids


def test_notification_candidates_exclude_other_department_notice():
    ids = _scoped_ids(major="경제학과")
    assert "notices:103" in ids
    assert "notices:102" not in ids


def test_notification_candidates_wise_items_require_allow_wise():
    default_ids = _scoped_ids(major="컴퓨터공학과")
    assert "schedule:202" not in default_ids
    assert "notices:104" not in default_ids

    wise_ids = _scoped_ids(major="컴퓨터공학과", allow_wise=True)
    assert {"schedule:202", "notices:104", "schedule:201", "notices:101", "notices:102"} <= wise_ids
    assert "notices:103" not in wise_ids


def test_notification_candidates_department_visibility_without_department_is_public():
    notices = _scoped_notices()
    notices.loc[notices["notice_id"] == 102, "department"] = ""
    candidates = _build_notification_candidates_from_frames(
        notices_df=notices,
        schedule_df=pd.DataFrame(),
        start=date(2026, 7, 1),
        end=date(2026, 7, 31),
        sources={"notices"},
        limit=50,
        today=date(2026, 7, 6),
    )
    # normalize_notice_visibility treats an untargeted department scope as public.
    assert "notices:102" in {candidate.id for candidate in candidates}


def test_notification_candidates_endpoint_defaults_to_safe_scope(monkeypatch):
    import asyncio

    from api import rag_service

    frames = {"notices": _scoped_notices(), "schedule": _scoped_schedule()}
    monkeypatch.setattr(
        rag_service,
        "_ensure_dataset",
        lambda key: (frames[key], None, None, None),
    )

    def call(**params):
        return asyncio.run(
            rag_service.notification_candidates(
                from_date="2026-07-01",
                to="2026-07-31",
                sources="notices,schedule",
                limit=50,
                **params,
            )
        )

    default_ids = {candidate.id for candidate in call()}
    assert default_ids == {"notices:101", "schedule:201"}

    scoped_ids = {candidate.id for candidate in call(major="컴퓨터공학과", allow_wise=False)}
    assert scoped_ids == {"notices:101", "notices:102", "schedule:201"}

    wise_ids = {candidate.id for candidate in call(major="컴퓨터공학과", allow_wise=True)}
    assert "schedule:202" in wise_ids and "notices:103" not in wise_ids
