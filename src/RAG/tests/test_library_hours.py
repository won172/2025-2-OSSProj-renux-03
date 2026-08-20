from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.crawlers.dongguk_library_hours import (  # noqa: E402
    LIBRARY_OPERATION_TIME_URL,
    LibraryHoursFetchError,
    LibraryHoursFetchResult,
    LibraryHoursFetchTimeout,
    fetch_library_operation_times,
)
from src.services.library_hours import (  # noqa: E402
    LibraryHoursSchemaError,
    normalize_library_operation_times,
    to_chunk_shaped_records,
    to_notice_shaped_records,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "library_operation_time.json"


def _payload() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _fetch(payload: dict | None = None) -> LibraryHoursFetchResult:
    return LibraryHoursFetchResult(
        payload=_payload() if payload is None else payload,
        source_url=LIBRARY_OPERATION_TIME_URL,
        fetched_at="2026-07-21T03:04:05Z",
    )


class FakeResponse:
    def __init__(self, payload=None, *, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error

    def raise_for_status(self) -> None:
        if self.error is not None:
            raise self.error

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeHttpClient:
    def __init__(self, response: FakeResponse | None = None, *, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def get(self, url, *, headers, timeout):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return self.response


def test_fetch_is_separate_injectable_and_records_source_and_time():
    payload = _payload()
    client = FakeHttpClient(FakeResponse(payload))
    result = fetch_library_operation_times(
        http_client=client,
        timeout=3.5,
        clock=lambda: datetime(2026, 7, 21, 12, 4, 5, tzinfo=timezone.utc),
    )
    assert result.payload == payload
    assert result.source_url == LIBRARY_OPERATION_TIME_URL
    assert result.fetched_at == "2026-07-21T12:04:05Z"
    assert client.calls == [
        {
            "url": LIBRARY_OPERATION_TIME_URL,
            "headers": {
                "Accept": "application/json",
                "User-Agent": "DongttokLibraryHoursConnector/1.0",
            },
            "timeout": 3.5,
        }
    ]


def test_fetch_timeout_and_response_errors_are_explicit():
    with pytest.raises(LibraryHoursFetchTimeout, match="timed out"):
        fetch_library_operation_times(
            http_client=FakeHttpClient(error=requests.exceptions.Timeout("slow")),
            timeout=1,
        )
    with pytest.raises(LibraryHoursFetchError, match="HTTPError"):
        fetch_library_operation_times(
            http_client=FakeHttpClient(
                FakeResponse(error=requests.exceptions.HTTPError("503"))
            )
        )
    with pytest.raises(LibraryHoursFetchError, match="not valid JSON"):
        fetch_library_operation_times(
            http_client=FakeHttpClient(FakeResponse(ValueError("bad json")))
        )


def test_normalize_fixture_maps_seoul_bmc_and_all_hour_states():
    result = normalize_library_operation_times(_fetch())
    assert len(result.records) == 8
    assert result.issues == ()
    assert len(result.raw_payload_hash) == 64
    assert {record["campus_scope"] for record in result.records} == {"seoul", "bmc"}
    assert {record["raw_payload_hash"] for record in result.records} == {
        result.raw_payload_hash
    }
    assert {record["source_url"] for record in result.records} == {
        LIBRARY_OPERATION_TIME_URL
    }
    assert {record["fetched_at"] for record in result.records} == {
        "2026-07-21T03:04:05Z"
    }

    by_identity = {
        (record["library_name"], record["facility_name"], record["service_date"]): record
        for record in result.records
    }
    main = by_identity[("중앙도서관", "중앙도서관", "2026-07-21")]
    assert (main["hours_status"], main["hours_label"]) == ("scheduled", "09:00~18:00")
    closed = by_identity[("중앙도서관", "중앙도서관", "2026-07-22")]
    assert closed["hours_status"] == "closed"
    assert closed["is_closed"] is True
    assert closed["hours_label"] == "휴관"
    all_day = by_identity[("중앙도서관", "보덕열람실", "2026-07-21")]
    assert all_day["hours_status"] == "open_24h"
    assert all_day["is_24h"] is True
    assert all_day["open_time"] is all_day["close_time"] is None
    extended = by_identity[("중앙도서관", "제2열람실", "2026-07-21")]
    assert extended["hours_label"] == "06:00~24:00"
    bmc = by_identity[("바이오약학도서관", "자료실", "2026-07-21")]
    assert bmc["campus_scope"] == "bmc"
    assert bmc["hours_label"] == "10:00~17:00"


def test_missing_time_values_and_empty_operation_list_are_visible_issues():
    payload = _payload()
    children = payload["list"][0]["childBranchSections"]
    children[0]["operationTimes"][0]["openTime"] = None
    children[1]["operationTimes"] = []
    result = normalize_library_operation_times(_fetch(payload))
    codes = {issue["code"] for issue in result.issues}
    assert codes == {"missing_time_value", "missing_operation_times"}
    unknown = next(
        record for record in result.records if record["facility_name"] == "자료실(3F~B2F)"
    )
    assert unknown["hours_status"] == "unknown"
    assert unknown["is_closed"] is None
    assert unknown["is_24h"] is None
    assert unknown["hours_label"] == "운영시간 확인 필요"
    assert not any(record["facility_name"] == "보덕열람실" for record in result.records)


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda payload: payload.pop("list"), "payload.list"),
        (
            lambda payload: payload["list"][0]["operationTimes"][0].update(
                {"isClose": "false"}
            ),
            "isClose must be a boolean",
        ),
        (
            lambda payload: payload["list"][0]["childBranchSections"][2][
                "operationTimes"
            ][0].update({"closedTime": "2460"}),
            "invalid time",
        ),
        (
            lambda payload: payload["list"].pop(),
            "required libraries missing",
        ),
    ],
)
def test_schema_errors_fail_closed(mutate, expected):
    payload = _payload()
    mutate(payload)
    with pytest.raises(LibraryHoursSchemaError, match=expected):
        normalize_library_operation_times(_fetch(payload))


def test_unknown_library_is_ignored_with_issue_and_total_mismatch_is_visible():
    payload = _payload()
    payload["list"].append(
        {
            "id": 99,
            "name": "외부도서관",
            "operationTimes": [],
            "childBranchSections": [],
        }
    )
    result = normalize_library_operation_times(_fetch(payload))
    codes = {issue["code"] for issue in result.issues}
    assert codes == {"total_count_mismatch", "unsupported_library"}
    assert all(record["library_name"] != "외부도서관" for record in result.records)


def test_live_api_envelope_and_closed_24h_facility_are_normalized_safely():
    payload = _payload()
    payload["list"][0]["childBranchSections"][1]["operationTimes"][0].update(
        {"isClose": True, "isAllDayOpen": True}
    )
    enveloped = {"success": True, "data": payload}

    result = normalize_library_operation_times(_fetch(enveloped))

    assert {issue["code"] for issue in result.issues} == {
        "conflicting_status_flags"
    }
    record = next(
        item for item in result.records if item["facility_name"] == "보덕열람실"
    )
    assert record["hours_status"] == "open_24h"
    assert record["is_closed"] is False
    assert record["is_24h"] is True


def test_raw_hash_is_canonical_and_input_is_not_mutated():
    payload = _payload()
    original = copy.deepcopy(payload)
    first = normalize_library_operation_times(_fetch(payload))
    reordered = {"list": payload["list"], "totalCount": payload["totalCount"]}
    second = normalize_library_operation_times(_fetch(reordered))
    assert first.raw_payload_hash == second.raw_payload_hash
    assert payload == original


def test_notice_and_chunk_shapes_are_deterministic_and_not_persisted():
    normalized = normalize_library_operation_times(_fetch())
    notices = to_notice_shaped_records(normalized)
    chunks = to_chunk_shaped_records(normalized)
    assert len(notices) == len(chunks) == 8
    assert len({row["문서키"] for row in notices}) == 8
    assert len({row["chunk_id"] for row in chunks}) == 8
    for notice, chunk in zip(notices, chunks):
        assert notice["게시판코드"] == "LIBRARY_HOURS"
        assert notice["문서키"] == chunk["document_key"]
        assert notice["원문ID"] == chunk["source_id"]
        assert notice["본문"] == chunk["chunk_text"]
        assert notice["campus_scope"] == chunk["campus_scope"]
        assert notice["effective_date"] == chunk["effective_date"]
        assert notice["raw_payload_hash"] == chunk["raw_payload_hash"]
        assert notice["상세URL"].startswith(LIBRARY_OPERATION_TIME_URL + "#")
        assert chunk["source"] == "notices"
        assert chunk["source_type"] == "facility_guide"
        assert chunk["url"] == LIBRARY_OPERATION_TIME_URL
    assert to_chunk_shaped_records(normalized) == chunks
