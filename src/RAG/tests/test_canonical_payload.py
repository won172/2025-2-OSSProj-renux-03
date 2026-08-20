from __future__ import annotations

from datetime import datetime, timezone

from src.pipelines.canonical import (
    canonical_hash,
    canonical_json,
    normalize_payload,
    source_document_key,
)


def test_canonical_json_is_independent_of_mapping_order():
    first = {"title": "공지", "nested": {"b": 2, "a": 1}}
    second = {"nested": {"a": 1, "b": 2}, "title": "공지"}

    assert canonical_json(first) == canonical_json(second)
    assert canonical_hash(first) == canonical_hash(second)


def test_collection_metadata_does_not_change_content_revision():
    first = {"title": "학식", "menu": "김치찌개", "collected_at": "2026-08-09T09:00:00+09:00"}
    second = {"title": "학식", "menu": "김치찌개", "collected_at": "2026-08-09T10:00:00+09:00"}

    assert canonical_hash(first, exclude_fields={"collected_at"}) == canonical_hash(
        second, exclude_fields={"collected_at"}
    )
    assert canonical_hash(first) != canonical_hash(second)


def test_normalize_payload_handles_dates_non_finite_numbers_and_ordered_lists():
    payload = {
        "when": datetime(2026, 8, 9, 12, 30, tzinfo=timezone.utc),
        "score": float("nan"),
        "items": ["b", "a"],
    }

    assert normalize_payload(payload) == {
        "when": "2026-08-09T12:30:00+00:00",
        "score": None,
        "items": ["b", "a"],
    }


def test_source_document_key_requires_both_parts():
    assert source_document_key("meals", "2026-08-09:학생식당") == "meals:2026-08-09:학생식당"

    try:
        source_document_key("meals", "")
    except ValueError:
        pass
    else:
        raise AssertionError("missing source_id must be rejected")
