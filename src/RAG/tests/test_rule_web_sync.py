from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import rag_service  # noqa: E402
from src.crawlers.dongguk_rule import (  # noqa: E402
    merge_official_rule_versions,
    parse_official_rule_content,
    parse_official_rule_list,
)
from src.pipelines import ingest  # noqa: E402
from src.pipelines.ingest import build_rule_chunks  # noqa: E402
from src.services.rule_versioning import (  # noqa: E402
    annotate_rule_versions,
    canonical_rule_key,
)


def test_official_rule_list_and_content_are_parsed_without_history_buttons():
    listing = """
    <table><tbody class="tbody"><tr>
      <td class="tbody_txt"><a href="javascript:fullPopupPost(70, 3638,'3638');">
        2-1-2&nbsp; 학사과정 학칙시행세칙 <img alt="일반 규정" />
      </a></td>
      <td><script>showDate('20260604', '2');</script></td>
    </tr></tbody></table>
    """
    rows = parse_official_rule_list(listing)
    assert rows == [
        {
            "rule_code": "2-1-2",
            "title": "학사과정 학칙시행세칙",
            "seq": "70",
            "seq_history": "3638",
            "published_at": "2026-06-04",
            "source_url": "https://rule.dongguk.edu/lmxsrv/law/lawFullContent.srv?SEQ=70&SEQ_HISTORY=3638",
        }
    ]

    content = """
    <div id="contentview">
      <div class="lawname">학사과정 학칙시행세칙</div>
      <div class="JO"><div class="sbt04"><a>연</a></div>
        <div class="article">제20조(재수강)</div>
        <div class="hang">① C+ 이하 과목만 재수강할 수 있고, 재수강으로 취득할 수 있는 성적은 A0까지로 한다.</div>
        <div class="hang">② 재수강은 과목명과 교과내용이 동일한 경우에만 허가하고 폐지 과목은 대체과목을 지정할 수 있다.</div>
        <div class="none">[2026.3.13. 본조 개정]</div>
      </div>
    </div>
    """
    title, text = parse_official_rule_content(content)
    assert title == "학사과정 학칙시행세칙"
    assert "제20조(재수강)" in text
    assert "C+ 이하" in text
    assert "\n연\n" not in f"\n{text}\n"


def test_rule_versions_keep_history_but_mark_only_current_document_latest():
    old_name = "2-1-2. 학사과정 학칙시행세칙(2025.8.13.).hwp"
    new_name = "2-1-2. 학사과정 학칙시행세칙(2026.6.4.).html"
    assert canonical_rule_key(old_name) == canonical_rule_key(new_name)

    frame = pd.DataFrame(
        [
            {"doc_id": "old", "filename": old_name, "title": old_name, "published_at": ""},
            {"doc_id": "new", "filename": new_name, "title": "2-1-2. 학사과정 학칙시행세칙", "published_at": "2026-06-04"},
            {"doc_id": "new", "filename": new_name, "title": "2-1-2. 학사과정 학칙시행세칙", "published_at": "2026-06-04"},
        ]
    )
    annotated = annotate_rule_versions(frame)
    assert annotated.loc[annotated.doc_id == "old", "is_latest"].tolist() == [False]
    assert annotated.loc[annotated.doc_id == "new", "is_latest"].tolist() == [True, True]


def test_current_rule_query_excludes_retired_version_but_historical_year_keeps_it():
    frame = pd.DataFrame(
        [
            {"chunk_id": "old", "dataset": "rules", "chunk_text": "옛 재수강 규정", "hybrid_score": 0.99, "is_latest": False},
            {"chunk_id": "new", "dataset": "rules", "chunk_text": "현행 재수강 규정", "hybrid_score": 0.60, "is_latest": True},
        ]
    )
    current = rag_service._build_balanced_shortlist([frame], query="재수강 규정", per_dataset=2)
    historical = rag_service._build_balanced_shortlist([frame], query="2025년 재수강 규정", per_dataset=2)
    assert current["chunk_id"].tolist() == ["new"]
    assert set(historical["chunk_id"]) == {"old", "new"}


def test_official_merge_is_idempotent_and_rule_chunks_keep_source_lineage():
    legacy = pd.DataFrame(
        [{"relative_dir": "legacy", "filename": "학칙(2025.1.1.).hwp", "text": "옛 본문"}]
    )
    official = pd.DataFrame(
        [{
            "relative_dir": "official",
            "filename": "학칙(2026.1.1.).html",
            "title": "학칙",
            "text": "현행 본문 " * 30,
            "source_type": "official_rule_web",
            "source_url": "https://rule.dongguk.edu/current",
            "source_page_url": "https://rule.dongguk.edu/list",
            "source_version": "69:1",
            "published_at": "2026-01-01",
        }]
    )
    once = merge_official_rule_versions(legacy, official)
    twice = merge_official_rule_versions(once, official)
    assert len(once) == len(twice) == 2

    chunks = build_rule_chunks(twice.assign(db_id=[1, 2]))
    current = chunks[chunks["published_at"] == "2026-01-01"]
    assert not current.empty
    assert set(current["url"]) == {"https://rule.dongguk.edu/current"}
    assert set(current["source_version"]) == {"69:1"}


def test_rule_collection_replacement_upserts_before_deleting_stale_ids(monkeypatch):
    events: list[object] = []
    frame = pd.DataFrame([{"chunk_id": "new", "chunk_text": "현행 규정"}])

    monkeypatch.setattr(ingest, "get_all_ids", lambda _collection: ["old", "new"])

    def fake_persist(key, collection, chunks):
        events.append(("upsert", key, collection, chunks["chunk_id"].tolist()))
        return chunks, object(), object()

    monkeypatch.setattr(ingest, "_persist_chunks", fake_persist)
    monkeypatch.setattr(
        ingest,
        "delete_items",
        lambda collection, ids: events.append(("delete", collection, ids)),
    )

    ingest._persist_replacing_collection("rules", "dongguk_rules", frame)

    assert events == [
        ("upsert", "rules", "dongguk_rules", ["new"]),
        ("delete", "dongguk_rules", ["old"]),
    ]
