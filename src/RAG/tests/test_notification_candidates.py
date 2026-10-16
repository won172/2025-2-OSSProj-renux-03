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
                "visibility": "public",
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
                "visibility": "public",
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
                "visibility": "public",
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


def _notice_ids(notices: pd.DataFrame, major: str | None = None) -> set[str]:
    candidates = _build_notification_candidates_from_frames(
        notices_df=notices,
        schedule_df=pd.DataFrame(),
        start=date(2026, 7, 1),
        end=date(2026, 7, 31),
        sources={"notices"},
        limit=50,
        today=date(2026, 7, 6),
        major=major,
    )
    return {candidate.id for candidate in candidates}


def test_notification_candidates_department_visibility_without_department_is_excluded():
    notices = _scoped_notices()
    notices.loc[notices["notice_id"] == 102, "department"] = ""
    for major in (None, "컴퓨터공학과", ""):
        assert "notices:102" not in _notice_ids(notices, major), major


def test_notification_candidates_visibility_fails_closed_for_missing_blank_unknown():
    notices = _scoped_notices()
    notices["visibility"] = notices["visibility"].astype(object)
    notices.loc[notices["notice_id"] == 102, "visibility"] = float("nan")
    notices.loc[notices["notice_id"] == 103, "visibility"] = "   "
    notices.loc[notices["notice_id"] == 104, "visibility"] = "전체공개"
    notices.loc[notices["notice_id"] == 104, "title"] = "통계학과 장학 신청"
    notices.loc[notices["notice_id"] == 104, "department"] = "통계학과"
    for major in (None, "컴퓨터공학과", "경제학과", "통계학과"):
        assert _notice_ids(notices, major) == {"notices:101"}, major


def test_notification_candidates_nan_visibility_with_department_hidden_from_guest():
    notices = _scoped_notices().iloc[[2]].copy()
    notices["visibility"] = [float("nan")]
    notices["department"] = ["통계학과"]
    assert _notice_ids(notices) == set()
    assert _notice_ids(notices, "통계학과") == set()


def test_notification_candidates_missing_visibility_column_excludes_notices(caplog):
    notices = _scoped_notices().drop(columns=["visibility"])
    with caplog.at_level("WARNING"):
        assert _notice_ids(notices) == set()
        assert _notice_ids(notices, "컴퓨터공학과") == set()
    assert "notification_candidates_visibility_column_missing" in caplog.text


def test_notification_candidates_keep_original_row_label_after_campus_boundary():
    # No id/url columns: the candidate id falls back to the original row label.
    schedule = pd.DataFrame(
        [
            {
                "title": "WISE캠퍼스 정규학기 학점교류 신청",
                "schedule_start": "2026-07-08",
                "schedule_end": "2026-07-09",
                "chunk_text": "학사일정: WISE캠퍼스 정규학기 학점교류 신청",
            },
            {
                "title": "정규학기 학점교류 신청",
                "schedule_start": "2026-07-08",
                "schedule_end": "2026-07-09",
                "chunk_text": "학사일정: 정규학기 학점교류 신청",
            },
        ],
        index=[40, 41],
    )
    candidates = _build_notification_candidates_from_frames(
        notices_df=pd.DataFrame(),
        schedule_df=schedule,
        start=date(2026, 7, 1),
        end=date(2026, 7, 31),
        sources={"schedule"},
        limit=50,
        today=date(2026, 7, 6),
    )
    assert [candidate.id for candidate in candidates] == ["schedule:41"]
    assert candidates[0].source_id == "41"

    wise_candidates = _build_notification_candidates_from_frames(
        notices_df=pd.DataFrame(),
        schedule_df=schedule,
        start=date(2026, 7, 1),
        end=date(2026, 7, 31),
        sources={"schedule"},
        limit=50,
        today=date(2026, 7, 6),
        allow_wise=True,
    )
    assert {candidate.id for candidate in wise_candidates} == {"schedule:40", "schedule:41"}


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


def _event_notice(notice_id: int, chunk_text: str, apply_deadline: str, position: int = 0) -> dict:
    return {
        "chunk_id": f"notice-{notice_id}-{position}",
        "doc_id": f"notice-{notice_id}",
        "notice_id": notice_id,
        "position": position,
        "title": f"장학 신청 안내 {notice_id}",
        "topics": "장학공지",
        "category": "장학",
        "published_at": "2026-10-05",
        "apply_deadline": apply_deadline,
        "url": f"https://example.com/{notice_id}",
        "chunk_text": chunk_text,
        "visibility": "public",
    }


def _event_candidates(rows: list[dict]) -> dict[str, object]:
    candidates = _build_notification_candidates_from_frames(
        notices_df=pd.DataFrame(rows),
        schedule_df=pd.DataFrame(),
        start=date(2026, 10, 1),
        end=date(2026, 10, 31),
        sources={"notices"},
        limit=20,
        today=date(2026, 10, 6),
    )
    return {item.source_id: item for item in candidates}


def test_notice_candidates_expose_period_evidence_and_status_at_top_level():
    by_id = _event_candidates(
        [
            # 근거가 두 번째 청크에만 있어도 같은 마감일을 내는 청크에서 찾는다.
            _event_notice(10, "지원 자격: 재학생", "2026-10-16", position=0),
            _event_notice(10, "신청기간: 2026. 10. 12.(월) ~ 10. 16.(금)", "2026-10-16", position=1),
            _event_notice(
                11,
                "1차 신청기간: 2026. 10. 12.(월) ~ 10. 14.(수)\n2차 신청기간: 2026. 10. 26.(월) ~ 10. 28.(수)",
                "2026-10-14",
            ),
        ]
    )

    confirmed = by_id["10"]
    assert (confirmed.start_date, confirmed.end_date, confirmed.target_date) == ("2026-10-12", "2026-10-16", "2026-10-16")
    assert confirmed.date_status == "confirmed"
    assert confirmed.evidence == "신청기간: 2026. 10. 12.(월) ~ 10. 16"

    multi = by_id["11"]
    assert multi.date_status == "unverified"
    assert multi.target_date == "2026-10-14"

    dumped = confirmed.model_dump()
    assert {"evidence", "date_status", "start_date"} <= set(dumped)
    assert "evidence" not in dumped["metadata"] and "date_status" not in dumped["metadata"]


def test_notice_candidates_leave_fields_empty_when_chunks_do_not_reproduce_stored_deadline():
    # 저장된 마감일은 원문 전체에서 정해졌지만 청크에는 근거가 없다: 추측하지 않는다.
    by_id = _event_candidates([_event_notice(12, "자세한 일정은 첨부파일 참고", "2026-10-20")])

    candidate = by_id["12"]
    assert candidate.target_date == "2026-10-20"
    assert (candidate.start_date, candidate.evidence, candidate.date_status) == (None, None, None)


def test_notice_candidates_do_not_attach_evidence_for_a_different_deadline():
    # 청크에서 다른 마감일(10.22)이 나오면 저장된 마감일(10.20)의 근거로 쓰지 않는다.
    by_id = _event_candidates([_event_notice(13, "신청기간: 2026. 10. 15.(목) ~ 10. 22.(목)", "2026-10-20")])

    candidate = by_id["13"]
    assert candidate.target_date == "2026-10-20"
    assert (candidate.start_date, candidate.evidence, candidate.date_status) == (None, None, None)
