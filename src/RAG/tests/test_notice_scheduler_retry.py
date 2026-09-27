from __future__ import annotations

import pandas as pd

from src.services.scheduler import _retry_incomplete_notice_boards


def _initial() -> pd.DataFrame:
    frame = pd.DataFrame(
        [{"상세URL": "https://example.test/a/1", "제목": "첫 수집"}]
    )
    frame.attrs["crawl_incomplete_boards"] = ["일반공지", "학사공지"]
    frame.attrs["crawl_status"] = "partial"
    return frame


def test_notice_retry_only_requests_incomplete_boards_and_records_recovery():
    calls: list[dict] = []

    def crawl(**kwargs):
        calls.append(kwargs)
        retry = pd.DataFrame(
            [{"상세URL": "https://example.test/b/2", "제목": "재시도 수집"}]
        )
        retry.attrs["crawl_incomplete_boards"] = ["학사공지"]
        retry.attrs["crawl_diagnostics"] = [
            {"board_name": "일반공지", "status": "success"},
            {"board_name": "학사공지", "status": "failed"},
        ]
        return retry

    result = _retry_incomplete_notice_boards(
        _initial(),
        crawl=crawl,
        known_ids_by_board={"일반공지": {1}},
    )

    assert calls[0]["boards"] == ["일반공지", "학사공지"]
    assert len(result) == 2
    assert result.attrs["crawl_incomplete_boards"] == ["학사공지"]
    assert result.attrs["crawl_retry"]["recovered_boards"] == ["일반공지"]
    assert result.attrs["crawl_retry"]["final_incomplete_boards"] == ["학사공지"]


def test_notice_retry_failure_preserves_first_pass_rows_and_board_failures():
    result = _retry_incomplete_notice_boards(
        _initial(),
        crawl=lambda **_kwargs: (_ for _ in ()).throw(TimeoutError("retry timeout")),
        known_ids_by_board=None,
    )

    assert len(result) == 1
    assert result.attrs["crawl_incomplete_boards"] == ["일반공지", "학사공지"]
    assert result.attrs["crawl_retry"]["error_type"] == "TimeoutError"
    assert result.attrs["crawl_retry"]["recovered_boards"] == []
