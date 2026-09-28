"""정기 증분 수집의 공지 삭제 감지 회귀 테스트.

네트워크 없이 가짜 HTTP 세션/probe만 사용한다. 삭제 판단 규칙:
302(다른 곳으로)/404 → missing, 200 → present, 5xx/예외 → unknown(삭제 안 함),
두 번의 실행에서 연속 missing이어야 deleted, 목록 재등장·200은 표식 해제,
안전 상한 초과 시 아무것도 적용하지 않는다.
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.crawlers import dongguk_library_hours, dongguk_notices  # noqa: E402
from src.database import Base, IngestionRun, SourceDocument, kst_now  # noqa: E402
from src.pipelines import notices_sync  # noqa: E402
from src.services import ingest_runtime  # noqa: E402

BASE = "https://www.dongguk.edu/article/GENERALNOTICES/detail"
LIST_URL = "https://www.dongguk.edu/article/GENERALNOTICES/list"


# ---------------------------------------------------------------- crawler probe
class FakeResponse:
    def __init__(self, status_code: int, location: str | None = None):
        self.status_code = status_code
        self.headers = {"Location": location} if location else {}
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, responder):
        self.responder = responder
        self.calls: list[dict] = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        result = self.responder(url)
        if isinstance(result, Exception):
            raise result
        return result


def test_probe_redirect_to_list_is_missing_and_never_follows_redirects():
    response = FakeResponse(302, "/article/GENERALNOTICES/list")
    session = FakeSession(lambda _url: response)

    outcome = dongguk_notices.probe_notice_detail(f"{BASE}/101", session=session, timeout=3.0)

    assert outcome == ("missing", 302)
    assert session.calls[0]["allow_redirects"] is False
    assert session.calls[0]["timeout"] == 3.0
    assert response.closed is True


@pytest.mark.parametrize(
    "location",
    [
        "/article/GENERALNOTICES/list",
        "/article/generalnotices/list",  # 대소문자 무시
        "/article/GeneralNotices/list/?pageIndex=1",
        "https://www.dongguk.edu/article/GENERALNOTICES/list",
        "http://WWW.DONGGUK.EDU/article/GENERALNOTICES/list",
    ],
)
def test_probe_redirect_to_same_board_list_is_missing(location):
    session = FakeSession(lambda _url: FakeResponse(302, location))
    assert dongguk_notices.probe_notice_detail(f"{BASE}/101", session=session)[0] == "missing"


def test_probe_200_is_present():
    session = FakeSession(lambda _url: FakeResponse(200))
    assert dongguk_notices.probe_notice_detail(f"{BASE}/101", session=session) == ("present", 200)


def test_probe_404_is_missing():
    session = FakeSession(lambda _url: FakeResponse(404))
    assert dongguk_notices.probe_notice_detail(f"{BASE}/101", session=session) == ("missing", 404)


@pytest.mark.parametrize(
    "result",
    [
        FakeResponse(500),
        FakeResponse(503),
        FakeResponse(429),
        FakeResponse(403),
        requests.exceptions.Timeout("slow"),
        requests.exceptions.ConnectionError("down"),
        FakeResponse(302),  # Location 없음
        FakeResponse(301, f"{BASE}/101/"),  # 같은 글로 가는 정규화 리다이렉트
        FakeResponse(302, "http://www.dongguk.edu/article/GENERALNOTICES/detail/101"),
        FakeResponse(302, "/login?returnUrl=%2Farticle%2FGENERALNOTICES%2Fdetail%2F101"),
        FakeResponse(302, "https://sso.dongguk.edu/login?service=x"),
        FakeResponse(302, "/maintenance.html"),
        FakeResponse(302, "/error/blocked"),
        FakeResponse(302, "/"),
        FakeResponse(302, "/article/HAKSANOTICE/list"),  # 다른 게시판
        FakeResponse(302, "https://evil.example/article/GENERALNOTICES/list"),
    ],
)
def test_probe_errors_and_ambiguous_redirects_are_unknown(result):
    session = FakeSession(lambda _url: result)
    outcome, _status = dongguk_notices.probe_notice_detail(f"{BASE}/101", session=session)
    assert outcome == "unknown"


def test_probe_skips_non_official_urls_without_request():
    session = FakeSession(lambda _url: FakeResponse(302, "/"))
    for url in (
        "manual://notice/1",
        "https://library.dongguk.edu/hours#1",
        "https://www.dongguk.edu/article/UNKNOWNBOARD/detail/1",
        f"{BASE}/101?x=1",
    ):
        assert dongguk_notices.probe_notice_detail(url, session=session)[0] == "unknown"
    assert session.calls == []


# ---------------------------------------------------------------- pipeline
@pytest.fixture
def db(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(notices_sync, "SessionLocal", factory)
    original_lock = ingest_runtime.maintenance_lock
    monkeypatch.setattr(
        ingest_runtime,
        "maintenance_lock",
        lambda *, blocking: original_lock(path=tmp_path / "maintenance.lock", blocking=blocking),
    )
    return factory


def _recent(days: int = 10) -> str:
    return (kst_now().date() - timedelta(days=days)).isoformat()


def _seed(factory, article_ids, *, published_at=None, miss_count=0, status="active", board="GENERALNOTICES"):
    session = factory()
    try:
        for article_id in article_ids:
            source_id = f"{board}:{article_id}"
            session.add(
                SourceDocument(
                    dataset="notices",
                    source_type="html_notice",
                    source_id=source_id,
                    source_url=f"https://www.dongguk.edu/article/{board}/detail/{article_id}",
                    document_key=f"notices:{source_id}",
                    title=f"공지 {article_id}",
                    category="일반",
                    published_at=published_at or _recent(),
                    status=status,
                    miss_count=miss_count,
                )
            )
        session.commit()
    finally:
        session.close()


def _docs(factory) -> dict[str, SourceDocument]:
    session = factory()
    try:
        return {doc.source_id: doc for doc in session.query(SourceDocument).all()}
    finally:
        session.close()


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["게시판", "게시판코드", "원문글ID", "상세URL"])


def _settings(**overrides) -> notices_sync.NoticeDeletionCheckSettings:
    values = dict(
        mode="enforce",
        window_months=6,
        budget=50,
        delay_seconds=0,
        max_consecutive_unknown=5,
        max_deletions=20,
        max_fraction=0.5,
        min_sample=4,
        max_total_unknown=0,
        max_seconds=0,
        strike_max_age_days=7,
        request_timeout=1.0,
    )
    values.update(overrides)
    return notices_sync.NoticeDeletionCheckSettings(**values)


def _probe_by_article(outcomes: dict[int, str], calls: list[str] | None = None):
    def probe(url: str):
        if calls is not None:
            calls.append(url)
        article_id = int(url.rsplit("/", 1)[1])
        result = outcomes.get(article_id, "present")
        if isinstance(result, Exception):
            raise result
        status = {"present": 200, "missing": 302, "unknown": 503}[result]
        return result, status

    return probe


def _collect_and_check(frame=None, **kwargs):
    result = notices_sync.collect_notice_documents(frame if frame is not None else _empty_frame())
    keys, summary = notices_sync.run_notice_deletion_check(result, **kwargs)
    return result, keys, summary


def _run_diagnostics(factory, run_id: int) -> dict:
    session = factory()
    try:
        return json.loads(session.get(IngestionRun, run_id).diagnostics_json)
    finally:
        session.close()


def _doc_ids(factory, article_ids, board="GENERALNOTICES") -> dict[int, int]:
    session = factory()
    try:
        rows = session.query(SourceDocument).all()
        by_source = {doc.source_id: int(doc.id) for doc in rows}
    finally:
        session.close()
    return {article_id: by_source[f"{board}:{article_id}"] for article_id in article_ids}


def _plant_probe_strikes(factory, article_ids, *, mode="enforce", age_days: float = 0.5):
    """이전 실행의 상세 확인 missing 표식을 진단 원장으로 심는다."""
    ids = _doc_ids(factory, article_ids)
    at = (kst_now().replace(tzinfo=None) - timedelta(days=age_days)).isoformat()
    ledger = {
        str(doc_id): {
            "run_id": 0,
            "at": at,
            "document_key": f"notices:GENERALNOTICES:{article_id}",
            "source_url": f"{BASE}/{article_id}",
        }
        for article_id, doc_id in ids.items()
    }
    session = factory()
    try:
        session.add(
            IngestionRun(
                dataset="notices",
                status="success",
                diagnostics_json=json.dumps(
                    {"deletion_check": {"mode": mode, "probe_strike_ledger": ledger}}
                ),
            )
        )
        session.commit()
    finally:
        session.close()


def _ledger(summary) -> set[int]:
    return {int(key) for key in summary["probe_strike_ledger"]}


def test_two_strike_flow_across_two_runs(db):
    _seed(db, [1, 2, 3, 4])
    probe = _probe_by_article({1: "missing"})
    doc1 = _doc_ids(db, [1])[1]

    _, keys, summary = _collect_and_check(settings=_settings(), probe=probe)
    assert keys == []
    assert summary["missing_strike_1"] == 1
    assert summary["confirmed_deleted"] == 0
    assert _ledger(summary) == {doc1}
    docs = _docs(db)
    assert docs["GENERALNOTICES:1"].status == "active"
    assert docs["GENERALNOTICES:1"].miss_count == 0  # 전체 수집 경로의 컬럼은 건드리지 않는다

    result, keys, summary = _collect_and_check(settings=_settings(), probe=probe)
    assert keys == ["notices:GENERALNOTICES:1"]
    assert summary["confirmed_deleted"] == 1
    assert _ledger(summary) == set()
    docs = _docs(db)
    assert docs["GENERALNOTICES:1"].status == "deleted"
    assert all(docs[f"GENERALNOTICES:{i}"].status == "active" for i in (2, 3, 4))
    diagnostics = _run_diagnostics(db, result.run_id)["deletion_check"]
    assert diagnostics["checked"] == 4
    assert diagnostics["present"] == 3
    assert diagnostics["confirmed_deleted"] == 1
    assert diagnostics["deleted_document_keys"] == ["notices:GENERALNOTICES:1"]
    session = db()
    try:
        assert session.get(IngestionRun, result.run_id).documents_deleted == 1
    finally:
        session.close()


def test_full_crawl_miss_count_is_not_a_probe_strike(db):
    """고정글 유예·레거시로 miss_count>0인 글도 상세 확인 두 번이 필요하다."""
    _seed(db, [1], miss_count=1)  # 전체 수집 유예(고정글)
    _seed(db, [2], miss_count=3)  # 레거시 값
    probe = _probe_by_article({1: "missing", 2: "missing"})

    _, keys, summary = _collect_and_check(settings=_settings(), probe=probe)
    assert keys == []
    assert summary["missing_strike_1"] == 2
    docs = _docs(db)
    assert docs["GENERALNOTICES:1"].status == "active"
    assert docs["GENERALNOTICES:1"].miss_count == 1
    assert docs["GENERALNOTICES:2"].miss_count == 3

    _, keys, _ = _collect_and_check(settings=_settings(), probe=probe)
    assert sorted(keys) == ["notices:GENERALNOTICES:1", "notices:GENERALNOTICES:2"]


def test_expired_probe_strike_does_not_confirm(db):
    _seed(db, [1])
    _plant_probe_strikes(db, [1], age_days=30)

    _, keys, summary = _collect_and_check(settings=_settings(), probe=_probe_by_article({1: "missing"}))

    assert keys == []
    assert summary["missing_strike_1"] == 1
    assert _docs(db)["GENERALNOTICES:1"].status == "active"


def test_dry_run_strike_does_not_confirm_enforce(db):
    _seed(db, [1])
    _plant_probe_strikes(db, [1], mode="dry_run")

    _, keys, summary = _collect_and_check(settings=_settings(), probe=_probe_by_article({1: "missing"}))

    assert keys == []
    assert summary["missing_strike_1"] == 1


def test_present_response_clears_strike(db):
    _seed(db, [1])
    _plant_probe_strikes(db, [1])

    _, keys, summary = _collect_and_check(settings=_settings(), probe=_probe_by_article({1: "present"}))

    assert keys == []
    assert summary["strikes_cleared"] == 1
    assert _ledger(summary) == set()


def test_reappearance_in_listing_clears_strike_and_skips_probe(db):
    _seed(db, [7])
    _plant_probe_strikes(db, [7])
    frame = pd.DataFrame(
        [
            {
                "게시판": "일반공지",
                "게시판코드": "GENERALNOTICES",
                "원문글ID": 7,
                "상세URL": f"{BASE}/7",
                "제목": "다시 보이는 공지",
                "게시일": _recent(),
                "본문": "본문 내용이 충분히 길어 품질 경고가 나지 않도록 채운 공지 본문입니다.",
            }
        ]
    )
    calls: list[str] = []

    _, keys, summary = _collect_and_check(
        frame, settings=_settings(), probe=_probe_by_article({7: "missing"}, calls)
    )

    assert keys == []
    assert calls == []
    assert summary["checked"] == 0
    assert _ledger(summary) == set()
    assert _docs(db)["GENERALNOTICES:7"].status in {"active", "updated"}

    # 다음 실행에서 missing이어도 새 첫 표식일 뿐 삭제되지 않는다.
    _, keys, summary = _collect_and_check(settings=_settings(), probe=_probe_by_article({7: "missing"}))
    assert keys == []
    assert summary["missing_strike_1"] == 1


def test_unknown_never_deletes_and_keeps_strike(db):
    _seed(db, [1, 2])
    _plant_probe_strikes(db, [1, 2])
    probe = _probe_by_article({1: "unknown", 2: requests.exceptions.Timeout("slow")})

    _, keys, summary = _collect_and_check(settings=_settings(), probe=probe)

    assert keys == []
    assert summary["unknown"] == 2
    assert _ledger(summary) == set(_doc_ids(db, [1, 2]).values())
    docs = _docs(db)
    assert docs["GENERALNOTICES:1"].status == "active"
    assert docs["GENERALNOTICES:2"].status == "active"


def test_consecutive_unknown_aborts_check(db):
    _seed(db, range(1, 11))
    calls: list[str] = []
    probe = _probe_by_article({i: "unknown" for i in range(1, 11)}, calls)

    _, _, summary = _collect_and_check(settings=_settings(max_consecutive_unknown=3), probe=probe)

    assert len(calls) == 3
    assert summary["aborted_reason"] == "consecutive_unknown"


def test_total_unknown_aborts_check(db):
    _seed(db, range(1, 11))
    calls: list[str] = []
    outcomes = {i: ("unknown" if i % 2 else "present") for i in range(1, 11)}

    _, _, summary = _collect_and_check(
        settings=_settings(max_consecutive_unknown=5, max_total_unknown=3),
        probe=_probe_by_article(outcomes, calls),
    )

    assert len(calls) == 5  # 1(u) 2 3(u) 4 5(u) → 누적 3에서 중단
    assert summary["aborted_reason"] == "total_unknown"


def test_wall_clock_budget_aborts_check(db):
    _seed(db, range(1, 11))
    calls: list[str] = []
    ticks = iter(range(0, 1000, 50))

    _, _, summary = _collect_and_check(
        settings=_settings(max_seconds=120),
        probe=_probe_by_article({}, calls),
        clock=lambda: next(ticks),
    )

    # clock: start=0, 확인 전 50, 100 → 두 건 확인, 다음 150 ≥ 120 → 중단
    assert len(calls) == 2
    assert summary["aborted_reason"] == "time_budget"


def test_cap_by_fraction_prevents_mass_deletion(db):
    _seed(db, range(1, 11))
    _plant_probe_strikes(db, range(1, 11))
    probe = _probe_by_article({i: "missing" for i in range(1, 11)})

    result, keys, summary = _collect_and_check(
        settings=_settings(max_fraction=0.1, min_sample=5), probe=probe
    )

    assert keys == []
    assert summary["capped"] is True
    assert summary["would_delete"] == 10
    assert summary["confirmed_deleted"] == 0
    assert all(doc.status == "active" for doc in _docs(db).values())
    session = db()
    try:
        run = session.get(IngestionRun, result.run_id)
        assert run.status == "partial_success"
        assert "capped" in run.error_summary
        assert run.documents_deleted == 0
    finally:
        session.close()


def test_min_sample_is_clamped_to_budget(db):
    _seed(db, range(1, 11))
    _plant_probe_strikes(db, [1, 2, 3])
    probe = _probe_by_article({1: "missing", 2: "missing", 3: "missing"})

    _, keys, summary = _collect_and_check(
        settings=_settings(budget=10, min_sample=20, max_fraction=0.2), probe=probe
    )

    assert summary["checked"] == 10
    assert summary["would_delete"] == 3
    assert summary["capped"] is True
    assert keys == []


def test_cap_by_absolute_count_prevents_mass_deletion(db):
    _seed(db, range(1, 6))
    _plant_probe_strikes(db, range(1, 6))
    probe = _probe_by_article({i: "missing" for i in range(1, 6)})

    _, keys, summary = _collect_and_check(
        settings=_settings(max_deletions=3, max_fraction=1.0), probe=probe
    )

    assert keys == []
    assert summary["capped"] is True
    assert all(doc.status == "active" for doc in _docs(db).values())


def test_capped_run_records_no_new_strikes_but_keeps_old(db):
    _seed(db, range(1, 11))
    _seed(db, [99])
    _plant_probe_strikes(db, [99])
    probe = _probe_by_article({i: "missing" for i in list(range(1, 11)) + [99]})

    _, keys, summary = _collect_and_check(settings=_settings(max_deletions=0), probe=probe)

    assert keys == []
    assert summary["capped"] is True
    assert _ledger(summary) == {_doc_ids(db, [99])[99]}


def test_budget_and_delay_are_respected_and_rotation_continues(db):
    _seed(db, range(1, 8))
    calls: list[str] = []
    sleeps: list[float] = []
    settings = _settings(budget=3, delay_seconds=0.25)

    _, _, first = _collect_and_check(
        settings=settings, probe=_probe_by_article({}, calls), sleep=sleeps.append
    )
    assert len(calls) == 3
    assert sleeps == [0.25, 0.25]
    assert first["candidates"] == 7

    _, _, second = _collect_and_check(
        settings=settings, probe=_probe_by_article({}, calls), sleep=sleeps.append
    )
    first_ids = [int(url.rsplit("/", 1)[1]) for url in calls[:3]]
    second_ids = [int(url.rsplit("/", 1)[1]) for url in calls[3:]]
    assert len(second_ids) == 3
    assert not set(first_ids) & set(second_ids)
    assert second["cursor_document_id"] > first["cursor_document_id"]


def test_pending_strikes_are_checked_first_and_keep_cursor(db):
    _seed(db, range(1, 6))
    _seed(db, [50])
    _plant_probe_strikes(db, [50])
    calls: list[str] = []

    _, _, summary = _collect_and_check(settings=_settings(budget=1), probe=_probe_by_article({}, calls))

    assert calls == [f"{BASE}/50"]
    assert summary["cursor_document_id"] == 0


def test_window_manual_and_non_visible_documents_are_not_probed(db):
    _seed(db, [1], published_at=_recent(400))
    _seed(db, [2], status="hidden")
    _seed(db, [3], status="deleted")
    session = db()
    try:
        session.add(
            SourceDocument(
                dataset="notices",
                source_type="manual_notice",
                source_id="manual_notice:1",
                source_url="manual://notice/1",
                document_key="notices:manual_notice:1",
                published_at=_recent(),
                status="active",
            )
        )
        session.commit()
    finally:
        session.close()
    calls: list[str] = []

    _, _, summary = _collect_and_check(settings=_settings(), probe=_probe_by_article({}, calls))

    assert calls == []
    assert summary["candidates"] == 0


def test_dry_run_changes_no_state_and_keeps_own_ledger(db):
    _seed(db, [1, 2])
    _plant_probe_strikes(db, [1], mode="dry_run")
    probe = _probe_by_article({1: "missing", 2: "missing"})

    result, keys, summary = _collect_and_check(settings=_settings(mode="dry_run"), probe=probe)

    assert keys == []
    assert summary["would_delete"] == 1
    assert summary["missing_strike_1"] == 1
    assert _ledger(summary) == {_doc_ids(db, [2])[2]}
    assert all(doc.status == "active" and doc.miss_count == 0 for doc in _docs(db).values())
    session = db()
    try:
        run = session.get(IngestionRun, result.run_id)
        assert run.status == "success"
        assert json.loads(run.diagnostics_json)["deletion_check"]["mode"] == "dry_run"
    finally:
        session.close()


def test_dry_run_cap_does_not_mark_run_partial(db):
    _seed(db, range(1, 6))
    _plant_probe_strikes(db, range(1, 6), mode="dry_run")

    result, _, summary = _collect_and_check(
        settings=_settings(mode="dry_run", max_deletions=0),
        probe=_probe_by_article({i: "missing" for i in range(1, 6)}),
    )

    assert summary["capped"] is True
    session = db()
    try:
        run = session.get(IngestionRun, result.run_id)
        assert run.status == "success"
        assert not run.error_summary
    finally:
        session.close()


def test_disabled_mode_makes_no_requests(db):
    _seed(db, [1])
    calls: list[str] = []

    _, keys, summary = _collect_and_check(
        settings=_settings(mode="off"), probe=_probe_by_article({1: "missing"}, calls)
    )

    assert keys == [] and calls == []
    assert summary["skip_reason"] == "disabled"


def test_invalid_mode_warns_and_makes_no_requests(db, caplog):
    import logging

    _seed(db, [1])
    calls: list[str] = []

    with caplog.at_level(logging.WARNING, logger=notices_sync.__name__):
        _, keys, summary = _collect_and_check(
            settings=_settings(mode="enforced"), probe=_probe_by_article({1: "missing"}, calls)
        )

    assert keys == [] and calls == []
    assert summary["skip_reason"] == "invalid_mode"
    assert "enforced" in caplog.text


def test_incomplete_boards_skip_deletion_check(db):
    _seed(db, [1])
    _plant_probe_strikes(db, [1])
    frame = _empty_frame()
    frame.attrs["crawl_incomplete_boards"] = ["일반공지"]
    calls: list[str] = []

    _, keys, summary = _collect_and_check(
        frame, settings=_settings(), probe=_probe_by_article({1: "missing"}, calls)
    )

    assert keys == [] and calls == []
    assert summary["skip_reason"] == "incomplete_boards"
    assert _docs(db)["GENERALNOTICES:1"].status == "active"


def test_sync_notices_routes_confirmed_deletions_through_existing_apply_path(db, monkeypatch):
    _seed(db, [1, 2, 3, 4])
    _seed(db, [9])
    _plant_probe_strikes(db, [9])
    applied: list[list[str]] = []
    finalized: list[int] = []
    monkeypatch.setattr(
        notices_sync,
        "apply_notice_normalized_documents",
        lambda *, document_keys, apply_index: applied.append(list(document_keys)),
    )
    monkeypatch.setattr(notices_sync, "refresh_notice_artifacts", lambda: None)
    monkeypatch.setattr(
        notices_sync, "_finalize_notice_derivatives", lambda result: finalized.append(result.run_id)
    )

    summary = notices_sync.sync_notices(
        _empty_frame(),
        allow_missing_detection=False,
        mode="full-sync",
        deletion_check=True,
        deletion_settings=_settings(),
        deletion_probe=_probe_by_article({9: "missing"}),
        deletion_sleep=lambda _s: None,
    )

    assert applied == [["notices:GENERALNOTICES:9"]]
    assert finalized == [summary["run_id"]]
    assert summary["deleted"] == 1
    assert summary["deletion_checked"] == 5
    assert summary["deletion_confirmed"] == 1
    assert summary["deletion_strike_1"] == 0
    assert summary["deletion_capped"] == 0
    assert summary["deletion_enforce"] == 1
    assert _docs(db)["GENERALNOTICES:9"].status == "deleted"


def test_sync_notices_isolates_deletion_check_failure(db, monkeypatch):
    _seed(db, [1])
    monkeypatch.setattr(
        notices_sync,
        "apply_notice_normalized_documents",
        lambda *, document_keys, apply_index: None,
    )
    monkeypatch.setattr(notices_sync, "refresh_notice_artifacts", lambda: None)
    monkeypatch.setattr(notices_sync, "_finalize_notice_derivatives", lambda result: None)

    def broken(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(notices_sync, "_plan_deletion_probes", broken)

    summary = notices_sync.sync_notices(
        _empty_frame(),
        deletion_check=True,
        deletion_settings=_settings(),
        deletion_probe=_probe_by_article({}),
    )

    assert summary["deleted"] == 0
    assert _run_diagnostics(db, summary["run_id"])["deletion_check"]["status"] == "error"
    assert _docs(db)["GENERALNOTICES:1"].status == "active"


def test_recording_failure_rolls_back_confirmed_deletion(db, monkeypatch):
    """진단 기록이 실패하면 deleted 상태도 남지 않아 색인과 어긋나지 않는다."""
    _seed(db, [1, 2, 3, 4])
    _seed(db, [9])
    _plant_probe_strikes(db, [9])
    applied: list[list[str]] = []
    monkeypatch.setattr(
        notices_sync,
        "apply_notice_normalized_documents",
        lambda *, document_keys, apply_index: applied.append(list(document_keys)),
    )
    monkeypatch.setattr(notices_sync, "refresh_notice_artifacts", lambda: None)
    monkeypatch.setattr(notices_sync, "_finalize_notice_derivatives", lambda result: None)
    original_stage = notices_sync._stage_deletion_check_record
    failures = {"left": 1}

    def flaky_stage(session, run_id, summary, **kwargs):
        if failures["left"]:
            failures["left"] -= 1
            original_stage(session, run_id, summary, **kwargs)
            raise RuntimeError("database is locked")
        return original_stage(session, run_id, summary, **kwargs)

    monkeypatch.setattr(notices_sync, "_stage_deletion_check_record", flaky_stage)

    summary = notices_sync.sync_notices(
        _empty_frame(),
        deletion_check=True,
        deletion_settings=_settings(),
        deletion_probe=_probe_by_article({9: "missing"}),
        deletion_sleep=lambda _s: None,
    )

    assert _docs(db)["GENERALNOTICES:9"].status == "active"
    assert applied == [[]]
    assert summary["deleted"] == 0
    diagnostics = _run_diagnostics(db, summary["run_id"])["deletion_check"]
    assert diagnostics["status"] == "error"
    session = db()
    try:
        assert session.get(IngestionRun, summary["run_id"]).documents_deleted == 0
    finally:
        session.close()

    # 다음 실행에서는 원장(확인 전 상태)이 그대로이므로 정상적으로 확정·반영된다.
    summary = notices_sync.sync_notices(
        _empty_frame(),
        deletion_check=True,
        deletion_settings=_settings(),
        deletion_probe=_probe_by_article({9: "missing"}),
        deletion_sleep=lambda _s: None,
    )
    assert applied[-1] == ["notices:GENERALNOTICES:9"]
    assert _docs(db)["GENERALNOTICES:9"].status == "deleted"


def test_sync_notices_without_flag_never_probes(db, monkeypatch):
    _seed(db, [1])
    monkeypatch.setattr(
        notices_sync,
        "apply_notice_normalized_documents",
        lambda *, document_keys, apply_index: None,
    )
    monkeypatch.setattr(notices_sync, "refresh_notice_artifacts", lambda: None)
    monkeypatch.setattr(notices_sync, "_finalize_notice_derivatives", lambda result: None)
    calls: list[str] = []

    summary = notices_sync.sync_notices(
        _empty_frame(),
        deletion_settings=_settings(),
        deletion_probe=_probe_by_article({1: "missing"}, calls),
    )

    assert calls == []
    assert "deletion_checked" not in summary


def test_default_settings_are_off():
    import src.config as config

    assert notices_sync.NoticeDeletionCheckSettings().mode == "off"
    assert config.RAG_NOTICE_DELETION_CHECK_MODE in {"off", "dry_run", "enforce"}


# ---------------------------------------------------------------- scheduler wiring
@pytest.mark.parametrize(
    ("enforce", "expected_status"),
    [(1, "partial"), (0, "ok")],
)
def test_scheduler_requests_deletion_check_and_flags_only_enforced_cap(
    monkeypatch, enforce, expected_status
):
    from types import ModuleType

    from src.services import scheduler

    captured: dict = {}
    recorded: list[tuple[str, str, str | None]] = []

    crawler = ModuleType("src.crawlers.dongguk_notices")
    crawler.crawl_notices = lambda **_kwargs: pd.DataFrame([{"notice": 1}])
    pipeline = ModuleType("src.pipelines.notices_sync")
    pipeline.load_known_article_ids_by_board = lambda: {}
    pipeline.record_notice_ingestion_failure = lambda *_a, **_k: 1

    def fake_sync(df, **kwargs):
        captured.update(kwargs)
        return {
            "new": 0,
            "updated": 0,
            "failed": 0,
            "incomplete_boards": 0,
            "deletion_checked": 12,
            "deletion_confirmed": 0,
            "deletion_capped": 1,
            "deletion_enforce": enforce,
        }

    pipeline.sync_notices = fake_sync
    monkeypatch.setitem(sys.modules, crawler.__name__, crawler)
    monkeypatch.setitem(sys.modules, pipeline.__name__, pipeline)
    library_timeouts: list[float] = []

    def fake_library_fetch(*, timeout):
        library_timeouts.append(timeout)
        return dongguk_library_hours.LibraryHoursFetchResult(
            payload={"list": [], "totalCount": 0},
            source_url=dongguk_library_hours.LIBRARY_OPERATION_TIME_URL,
            fetched_at="2026-07-21T03:04:05Z",
        )

    monkeypatch.setattr(dongguk_library_hours, "fetch_library_operation_times", fake_library_fetch)
    monkeypatch.setattr(scheduler, "_refresh_runtime_dataset_state", lambda _d: None)
    monkeypatch.setattr(scheduler, "_start_faq_draft_worker", lambda: None)
    monkeypatch.setattr(
        scheduler, "_record_run", lambda job, status, message=None: recorded.append((job, status, message))
    )

    scheduler.refresh_notices_job()

    assert library_timeouts == [scheduler.RAG_SCHEDULER_REQUEST_TIMEOUT_SECONDS]
    assert captured["deletion_check"] is True
    assert captured["allow_missing_detection"] is False
    assert captured["mode"] == "full-sync"
    assert recorded[-1][0] == "refresh_notices"
    assert recorded[-1][1] == expected_status
    assert "삭제확인 12" in recorded[-1][2]
