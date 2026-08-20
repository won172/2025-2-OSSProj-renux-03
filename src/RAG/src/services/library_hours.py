"""Normalize official Dongguk library hours without persistence side effects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from src.crawlers.dongguk_library_hours import LibraryHoursFetchResult


LIBRARY_CAMPUSES = {
    "중앙도서관": "seoul",
    "바이오약학도서관": "bmc",
}
# Golden/source lineage treats campus facility opening hours as a facility
# guide; the endpoint name is an ingestion detail, not a reader-facing type.
SOURCE_TYPE = "facility_guide"
NOTICE_BOARD_CODE = "LIBRARY_HOURS"


class LibraryHoursSchemaError(ValueError):
    """The official payload changed in a way that prevents safe normalization."""


@dataclass(frozen=True)
class LibraryHoursNormalizationResult:
    records: tuple[dict[str, Any], ...]
    issues: tuple[dict[str, str], ...]
    raw_payload_hash: str
    fetched_at: str
    source_url: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "records": [dict(record) for record in self.records],
            "issues": [dict(issue) for issue in self.issues],
            "raw_payload_hash": self.raw_payload_hash,
            "fetched_at": self.fetched_at,
            "source_url": self.source_url,
        }


@dataclass(frozen=True)
class LibraryHoursDateView:
    service_date: str
    status: str
    records: tuple[dict[str, Any], ...]


def _payload_hash(payload: Any) -> str:
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LibraryHoursSchemaError("payload is not canonical JSON") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _required_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LibraryHoursSchemaError(f"{path} must be a non-empty string")
    return re.sub(r"\s+", " ", value).strip()


def _required_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise LibraryHoursSchemaError(f"{path} must be a boolean")
    return value


def _service_date(value: Any, path: str) -> str:
    text = _required_text(value, path)
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise LibraryHoursSchemaError(f"{path} has an invalid date: {text}") from exc
    return parsed.date().isoformat()


def _clock_time(value: Any, path: str, *, allow_2400: bool) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise LibraryHoursSchemaError(f"{path} must be a string or null")
    compact = value.strip().replace(":", "")
    if not re.fullmatch(r"\d{4}", compact):
        raise LibraryHoursSchemaError(f"{path} has an invalid time: {value}")
    hour, minute = int(compact[:2]), int(compact[2:])
    if allow_2400 and hour == 24 and minute == 0:
        return "24:00"
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise LibraryHoursSchemaError(f"{path} has an invalid time: {value}")
    return f"{hour:02d}:{minute:02d}"


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _record_id(library_name: str, facility_name: str, service_date: str) -> str:
    identity = f"{library_name}\0{facility_name}\0{service_date}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _hours_label(record: Mapping[str, Any]) -> str:
    status = record["hours_status"]
    if status == "closed":
        return "휴관"
    if status == "open_24h":
        return "24시간"
    if status == "scheduled":
        return f"{record['open_time']}~{record['close_time']}"
    return "운영시간 확인 필요"


def _normalize_time_entry(
    raw: Any,
    *,
    path: str,
    library_name: str,
    facility_name: str,
    campus_scope: str,
    facility_kind: str,
    fetch: LibraryHoursFetchResult,
    raw_payload_hash: str,
    issues: list[dict[str, str]],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise LibraryHoursSchemaError(f"{path} must be an object")
    service_date = _service_date(raw.get("libraryDate"), f"{path}.libraryDate")
    is_closed = _required_bool(raw.get("isClose"), f"{path}.isClose")
    is_all_day = _required_bool(raw.get("isAllDayOpen"), f"{path}.isAllDayOpen")
    if is_closed and is_all_day:
        # 운영 API는 휴관일에도 24시간 열람실에 두 플래그를 모두 내려준다.
        # 시설 단위의 ``isAllDayOpen``을 우선하되 모순은 품질 이슈로 남긴다.
        issues.append(
            _issue(
                "conflicting_status_flags",
                path,
                "isClose and isAllDayOpen are both true; treating facility as open 24 hours",
            )
        )

    open_time: str | None = None
    close_time: str | None = None
    if is_all_day:
        hours_status = "open_24h"
        normalized_closed = False
        normalized_24h = True
    elif is_closed:
        hours_status = "closed"
        normalized_closed: bool | None = True
        normalized_24h: bool | None = False
    else:
        open_time = _clock_time(raw.get("openTime"), f"{path}.openTime", allow_2400=False)
        close_time = _clock_time(raw.get("closedTime"), f"{path}.closedTime", allow_2400=True)
        if open_time is None or close_time is None:
            hours_status = "unknown"
            normalized_closed = None
            normalized_24h = None
            issues.append(
                _issue(
                    "missing_time_value",
                    path,
                    "non-closed operation time is missing openTime or closedTime",
                )
            )
        else:
            hours_status = "scheduled"
            normalized_closed = False
            normalized_24h = False

    record = {
        "record_id": _record_id(library_name, facility_name, service_date),
        "library_name": library_name,
        "facility_name": facility_name,
        "facility_kind": facility_kind,
        "campus_scope": campus_scope,
        "service_date": service_date,
        "effective_date": service_date,
        "hours_status": hours_status,
        "is_closed": normalized_closed,
        "is_24h": normalized_24h,
        "open_time": open_time,
        "close_time": close_time,
        "source_type": SOURCE_TYPE,
        "source_url": fetch.source_url,
        "fetched_at": fetch.fetched_at,
        "raw_payload_hash": raw_payload_hash,
    }
    record["hours_label"] = _hours_label(record)
    return record


def _normalize_operation_times(
    raw_times: Any,
    *,
    path: str,
    library_name: str,
    facility_name: str,
    campus_scope: str,
    facility_kind: str,
    fetch: LibraryHoursFetchResult,
    raw_payload_hash: str,
    issues: list[dict[str, str]],
) -> list[dict[str, Any]]:
    if not isinstance(raw_times, list):
        raise LibraryHoursSchemaError(f"{path} must be an array")
    if not raw_times:
        issues.append(
            _issue(
                "missing_operation_times",
                path,
                "facility has no date-specific operationTimes",
            )
        )
        return []
    records = [
        _normalize_time_entry(
            raw,
            path=f"{path}[{index}]",
            library_name=library_name,
            facility_name=facility_name,
            campus_scope=campus_scope,
            facility_kind=facility_kind,
            fetch=fetch,
            raw_payload_hash=raw_payload_hash,
            issues=issues,
        )
        for index, raw in enumerate(raw_times)
    ]
    dates = [record["service_date"] for record in records]
    if len(dates) != len(set(dates)):
        raise LibraryHoursSchemaError(f"{path} contains duplicate libraryDate values")
    return records


def normalize_library_operation_times(
    fetch: LibraryHoursFetchResult,
) -> LibraryHoursNormalizationResult:
    """Normalize only dates explicitly present in the official API payload.

    Missing dates are never inferred from an adjacent weekday, semester, or
    prior fetch.  Use :func:`library_hours_for_date`; an absent service date is
    returned as ``status="unknown"``.
    """
    raw_payload = fetch.payload
    if not isinstance(raw_payload, dict):
        raise LibraryHoursSchemaError("payload must be an object")
    payload = raw_payload
    if "data" in raw_payload:
        if raw_payload.get("success") is not True or not isinstance(raw_payload.get("data"), dict):
            raise LibraryHoursSchemaError("payload.data must be a successful object envelope")
        payload = raw_payload["data"]
    branches = payload.get("list")
    if not isinstance(branches, list):
        raise LibraryHoursSchemaError("payload.list must be an array")
    total_count = payload.get("totalCount")
    if not isinstance(total_count, int) or isinstance(total_count, bool):
        raise LibraryHoursSchemaError("payload.totalCount must be an integer")

    raw_payload_hash = _payload_hash(raw_payload)
    issues: list[dict[str, str]] = []
    if total_count != len(branches):
        issues.append(
            _issue(
                "total_count_mismatch",
                "payload.totalCount",
                f"totalCount={total_count} but list has {len(branches)} entries",
            )
        )

    records: list[dict[str, Any]] = []
    found_libraries: set[str] = set()
    for branch_index, branch in enumerate(branches):
        branch_path = f"payload.list[{branch_index}]"
        if not isinstance(branch, dict):
            raise LibraryHoursSchemaError(f"{branch_path} must be an object")
        library_name = _required_text(branch.get("name"), f"{branch_path}.name")
        campus_scope = LIBRARY_CAMPUSES.get(library_name)
        if campus_scope is None:
            issues.append(
                _issue(
                    "unsupported_library",
                    branch_path,
                    f"library is outside the Seoul/BMC connector allowlist: {library_name}",
                )
            )
            continue
        if library_name in found_libraries:
            raise LibraryHoursSchemaError(f"duplicate library entry: {library_name}")
        found_libraries.add(library_name)
        records.extend(
            _normalize_operation_times(
                branch.get("operationTimes"),
                path=f"{branch_path}.operationTimes",
                library_name=library_name,
                facility_name=library_name,
                campus_scope=campus_scope,
                facility_kind="library",
                fetch=fetch,
                raw_payload_hash=raw_payload_hash,
                issues=issues,
            )
        )
        children = branch.get("childBranchSections")
        if not isinstance(children, list):
            raise LibraryHoursSchemaError(f"{branch_path}.childBranchSections must be an array")
        seen_facilities: set[str] = set()
        for child_index, child in enumerate(children):
            child_path = f"{branch_path}.childBranchSections[{child_index}]"
            if not isinstance(child, dict):
                raise LibraryHoursSchemaError(f"{child_path} must be an object")
            facility_name = _required_text(child.get("name"), f"{child_path}.name")
            if facility_name in seen_facilities:
                raise LibraryHoursSchemaError(
                    f"duplicate facility in {library_name}: {facility_name}"
                )
            seen_facilities.add(facility_name)
            records.extend(
                _normalize_operation_times(
                    child.get("operationTimes"),
                    path=f"{child_path}.operationTimes",
                    library_name=library_name,
                    facility_name=facility_name,
                    campus_scope=campus_scope,
                    facility_kind="facility",
                    fetch=fetch,
                    raw_payload_hash=raw_payload_hash,
                    issues=issues,
                )
            )

    missing_libraries = sorted(set(LIBRARY_CAMPUSES) - found_libraries)
    if missing_libraries:
        raise LibraryHoursSchemaError(
            f"required libraries missing from payload: {missing_libraries}"
        )
    records.sort(
        key=lambda record: (
            record["service_date"],
            0 if record["campus_scope"] == "seoul" else 1,
            record["library_name"],
            record["facility_name"],
        )
    )
    return LibraryHoursNormalizationResult(
        records=tuple(records),
        issues=tuple(issues),
        raw_payload_hash=raw_payload_hash,
        fetched_at=fetch.fetched_at,
        source_url=fetch.source_url,
    )


def library_hours_for_date(
    normalized: LibraryHoursNormalizationResult,
    service_date: str,
) -> LibraryHoursDateView:
    try:
        parsed = datetime.strptime(service_date, "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError("service_date must use YYYY-MM-DD") from exc
    matches = tuple(
        dict(record)
        for record in normalized.records
        if record["service_date"] == parsed
    )
    return LibraryHoursDateView(
        service_date=parsed,
        status="known" if matches else "unknown",
        records=matches,
    )


def _record_content(record: Mapping[str, Any]) -> str:
    return (
        f"{record['service_date']} {record['library_name']} "
        f"{record['facility_name']} 운영시간: {record['hours_label']}"
    )


def to_notice_shaped_records(
    normalized: LibraryHoursNormalizationResult,
) -> list[dict[str, Any]]:
    """Return a compatibility contract for a future notices merge.

    This function does not call the current notice ingest path and does not
    write CSV, SQLite, parquet, TF-IDF, or Chroma.
    """
    rows: list[dict[str, Any]] = []
    for record in normalized.records:
        source_id = f"library_hours:{record['record_id']}"
        rows.append(
            {
                "게시판": "도서관 운영시간",
                "게시판코드": NOTICE_BOARD_CODE,
                "원문글ID": record["record_id"],
                "원문ID": source_id,
                "문서키": f"notices:{source_id}",
                "제목": (
                    f"{record['library_name']} {record['facility_name']} "
                    f"운영시간 ({record['service_date']})"
                ),
                "카테고리": "시설운영",
                "게시일": record["service_date"],
                "상단고정": False,
                # notices.detail_url is unique.  The official endpoint is shared by
                # every facility/date row, so a stable fragment preserves both the
                # canonical source and one-to-one relational identity.
                "상세URL": f"{record['source_url']}#{record['record_id']}",
                "본문": _record_content(record),
                "본문HTML": "",
                "첨부파일": [],
                "campus_scope": record["campus_scope"],
                "effective_date": record["effective_date"],
                "source_type": record["source_type"],
                "fetched_at": record["fetched_at"],
                "raw_payload_hash": record["raw_payload_hash"],
                "hours_status": record["hours_status"],
                "is_closed": record["is_closed"],
                "is_24h": record["is_24h"],
                "open_time": record["open_time"],
                "close_time": record["close_time"],
            }
        )
    return rows


def to_chunk_shaped_records(
    normalized: LibraryHoursNormalizationResult,
) -> list[dict[str, Any]]:
    """Return deterministic one-record/one-chunk notices-compatible rows."""
    chunks: list[dict[str, Any]] = []
    for record in normalized.records:
        source_id = f"library_hours:{record['record_id']}"
        chunks.append(
            {
                "chunk_id": f"notices:library_hours:{record['record_id']}:0",
                "chunk_text": _record_content(record),
                "source": "notices",
                "source_type": record["source_type"],
                "source_id": source_id,
                "document_key": f"notices:{source_id}",
                "title": (
                    f"{record['library_name']} {record['facility_name']} "
                    f"운영시간 ({record['service_date']})"
                ),
                "topics": "도서관 운영시간",
                "category": "시설운영",
                "campus_scope": record["campus_scope"],
                "published_at": record["service_date"],
                "effective_date": record["effective_date"],
                "url": record["source_url"],
                "fetched_at": record["fetched_at"],
                "raw_payload_hash": record["raw_payload_hash"],
                "hours_status": record["hours_status"],
                "is_closed": record["is_closed"],
                "is_24h": record["is_24h"],
                "open_time": record["open_time"],
                "close_time": record["close_time"],
            }
        )
    return chunks


__all__ = [
    "LIBRARY_CAMPUSES",
    "LibraryHoursDateView",
    "LibraryHoursNormalizationResult",
    "LibraryHoursSchemaError",
    "normalize_library_operation_times",
    "to_chunk_shaped_records",
    "to_notice_shaped_records",
    "library_hours_for_date",
]
