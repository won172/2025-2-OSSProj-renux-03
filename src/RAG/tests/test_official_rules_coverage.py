"""공식 규정관리시스템 전체 분류 수집·보존 병합·보고 계약 (네트워크 없이 HTML fixture 사용)."""
from __future__ import annotations

import functools
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import sync_official_rules  # noqa: E402
from src.crawlers import dongguk_rule  # noqa: E402
from src.crawlers.dongguk_rule import (  # noqa: E402
    ACADEMIC_RULE_LIST_URL,
    RULE_CATEGORY_TREE_URL,
    OfficialRuleCrawlError,
    collect_official_academic_rules,
    collect_official_rules,
    official_rule_list_url,
    parse_rule_category_tree,
    parse_rule_list_paging,
)
from src.pipelines.ingest import build_rule_chunks  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "official_rules"
_CONTENT_RE = re.compile(r"lawFullContent\.srv\?SEQ=(\d+)&SEQ_HISTORY=(\d+)$")


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class FakeRuleSite:
    """rule.dongguk.edu를 흉내 내는 세션. 요청 URL을 기록하고 실패를 주입할 수 있다."""

    def __init__(self, *, failing: set[str] | None = None) -> None:
        self.requests: list[str] = []
        self.failing = failing or set()
        self.pages = {
            RULE_CATEGORY_TREE_URL: _fixture("tree.html"),
            official_rule_list_url("5"): _fixture("list_seq5.html"),
            official_rule_list_url("6"): _fixture("list_seq6.html"),
            official_rule_list_url("7"): _fixture("list_seq7_p1.html"),
            official_rule_list_url("7", 2): _fixture("list_seq7_p2.html"),
            official_rule_list_url("18"): _fixture("list_seq18.html"),
            official_rule_list_url("44"): _fixture("list_seq44.html"),
            official_rule_list_url("59"): _fixture("list_seq59.html"),
        }
        self.content_template = _fixture("content_template.html")

    def get(self, url: str, timeout: float = 0) -> _Response:
        self.requests.append(url)
        if url in self.failing:
            raise ConnectionError(f"injected failure: {url}")
        if url in self.pages:
            return _Response(self.pages[url])
        match = _CONTENT_RE.search(url)
        if match:
            return _Response(self.content_template.replace("{TITLE}", f"규정 {match.group(1)}"))
        raise AssertionError(f"unexpected request: {url}")

    def content_requests(self) -> list[str]:
        return [url for url in self.requests if "lawFullContent" in url]


def _collector(site: FakeRuleSite, **kwargs):
    kwargs.setdefault("request_delay", 0)
    kwargs.setdefault("sleep", lambda _seconds: None)
    return functools.partial(collect_official_rules, session=site, **kwargs)


@pytest.fixture(autouse=True)
def _clear_rule_env(monkeypatch):
    for name in (
        dongguk_rule.RULE_SCOPE_ENV,
        dongguk_rule.RULE_REQUEST_DELAY_ENV,
        dongguk_rule.RULE_MAX_REQUESTS_ENV,
        dongguk_rule.RULE_MAX_FAILED_RATIO_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


def test_category_tree_discovery_builds_full_paths_and_marks_root():
    categories = {item.seq: item for item in parse_rule_category_tree(_fixture("tree.html"))}

    assert set(categories) == {"1", "5", "6", "7", "18", "44", "59", "324", "314"}
    assert categories["1"].is_root
    assert not any(item.is_root for seq, item in categories.items() if seq != "1")
    assert categories["6"].path == ("제2편 학칙", "제1장 대학")
    # 기존 SEQ=6 공식 행과 HWP 스냅샷 경로 규칙이 그대로 유지된다.
    assert categories["6"].relative_dir == "제2편_학칙/제1장_대학/공식_현행"
    assert categories["6"].list_url == ACADEMIC_RULE_LIST_URL
    # 캠퍼스 판정에 쓰이는 WISE 표지가 경로에 남는다.
    assert categories["59"].relative_dir == "제7편_일반연구기관/제15장_WISE캠퍼스 일반연구기관/공식_현행"
    assert categories["18"].relative_dir == "제4편_위원회/공식_현행"


def test_list_paging_is_read_from_page_navigation_script():
    assert parse_rule_list_paging(_fixture("list_seq7_p1.html")) == (17, 15)
    assert parse_rule_list_paging(_fixture("list_empty.html")) == (0, 15)
    assert parse_rule_list_paging("<html></html>") == (None, 15)


def test_all_scope_crawls_every_category_with_pagination_dedupe_and_exclusions():
    site = FakeRuleSite()
    sleeps: list[float] = []
    official = collect_official_rules(
        scope="all", session=site, request_delay=0.25, sleep=sleeps.append
    )
    report = official.attrs["crawl_report"]

    # 폐지규정·상위법은 요청하지 않는다.
    assert not any("SEQ=314" in url or "SEQ=324" in url for url in site.requests)
    assert official_rule_list_url("7", 2) in site.requests
    # 6(5건) + 7(17건, 2쪽) + 18(2건 중 1건은 7과 같은 SEQ) + 59(2건)
    # 5(제2편)·44(제7편) 상위 목록에 직접 걸린 69·500은 하위 장 목록과 중복이다.
    assert len(official) == 5 + 17 + 1 + 2
    assert official["source_version"].is_unique
    assert official["rule_code"].is_unique
    assert report["duplicate_rules_dropped"] == 3
    assert report["complete"] is True
    assert report["categories_crawled"] == 6
    assert sorted(report["excluded_categories"]) == ["상위법", "폐지규정"]
    assert report["requests_made"] == len(site.requests)
    # 첫 요청 뒤 매 요청 사이에 예의 지연을 둔다.
    assert sleeps == [0.25] * (len(site.requests) - 1)

    scholarship = official.loc[official["rule_code"].eq("2-2-2")].iloc[0]
    assert scholarship["relative_dir"] == "제2편_학칙/제2장_일반대학원/공식_현행"
    assert scholarship["source_page_url"] == official_rule_list_url("7")
    assert scholarship["source_url"].endswith("lawFullContent.srv?SEQ=101&SEQ_HISTORY=4001")
    assert scholarship["published_at"] == "2026-06-15"
    assert scholarship["filename"] == "2-2-2. 규정 101(2026.06.15.).html"
    assert set(official["source_type"]) == {"official_rule_web"}
    # 상위(편) 목록에서 먼저 보였어도 더 구체적인 장 경로를 쓴다(WISE 표지 보존).
    wise = official.loc[official["rule_code"].eq("7-15-1")].iloc[0]
    assert wise["relative_dir"] == "제7편_일반연구기관/제15장_WISE캠퍼스 일반연구기관/공식_현행"
    assert wise["source_page_url"] == official_rule_list_url("59")
    charter = official.loc[official["rule_code"].eq("2-1-1")].iloc[0]
    assert charter["relative_dir"] == "제2편_학칙/제1장_대학/공식_현행"
    assert charter["source_page_url"] == ACADEMIC_RULE_LIST_URL
    undated = official.loc[official["rule_code"].eq("7-15-2")].iloc[0]
    assert undated["published_at"] == ""
    assert undated["filename"] == "7-15-2. 규정 501.html"


def test_academic_scope_keeps_previous_seq6_only_behavior(monkeypatch):
    site = FakeRuleSite()
    official = collect_official_academic_rules(
        session=site, request_delay=0, sleep=lambda _s: None
    )
    assert site.requests[0] == ACADEMIC_RULE_LIST_URL
    assert RULE_CATEGORY_TREE_URL not in site.requests
    assert len(official) == 5
    assert set(official["relative_dir"]) == {"제2편_학칙/제1장_대학/공식_현행"}
    assert set(official["source_page_url"]) == {ACADEMIC_RULE_LIST_URL}
    first = official.iloc[0]
    assert first["filename"] == "2-1-1. 규정 69(2026.08.12.).html"
    assert first["source_version"] == "69:3704"

    # 환경 변수 knob으로도 이전 범위를 강제할 수 있다.
    monkeypatch.setenv(dongguk_rule.RULE_SCOPE_ENV, "academic")
    env_site = FakeRuleSite()
    via_env = collect_official_rules(session=env_site, request_delay=0, sleep=lambda _s: None)
    assert RULE_CATEGORY_TREE_URL not in env_site.requests
    assert via_env["source_version"].tolist() == official["source_version"].tolist()
    assert via_env.attrs["crawl_report"]["scope"] == "academic"


def test_unknown_scope_is_rejected():
    with pytest.raises(ValueError):
        collect_official_rules(scope="everything", session=FakeRuleSite())


def test_known_versions_are_reused_without_detail_requests():
    site = FakeRuleSite()
    known = {
        "69:3704": {"title": "2-1-1. 학 칙", "text": "기존 현행 본문 " * 20},
    }
    official = collect_official_rules(
        scope="academic", session=site, request_delay=0, sleep=lambda _s: None,
        known_versions=known,
    )
    assert not any("SEQ=69&" in url for url in site.content_requests())
    reused = official.loc[official["source_version"].eq("69:3704")].iloc[0]
    assert reused["title"] == "2-1-1. 학 칙"
    assert reused["text"] == known["69:3704"]["text"]
    assert official.attrs["crawl_report"]["detail_reused"] == 1
    assert official.attrs["crawl_report"]["detail_fetched"] == 4


def _write_existing(path: Path) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {  # 공식 현행판이 대체해야 하는 구판
                "relative_dir": "제2편_학칙/제1장_대학",
                "filename": "2-1-2. 학사과정 학칙시행세칙(2025.8.13.).hwp",
                "text": "옛 학사과정 학칙시행세칙 본문 " * 10,
            },
            {  # 스냅샷에 있던 대학원 규정 구판
                "relative_dir": "제2편_학칙/제2장_일반대학원",
                "filename": "2-2-2. 대학원 장학금 지급내규(2024.3.1.).hwp",
                "text": "옛 장학금 지급내규 본문 " * 10,
            },
            {  # 번호는 같지만 제목이 달라 자동 대체되지 않는 규정
                "relative_dir": "제2편_학칙/제1장_대학",
                "filename": "2-1-3. 교원양성 운영지침(2024.1.1.).hwp",
                "text": "옛 교원양성 지침 본문 " * 10,
            },
            {  # 스냅샷 안에서 같은 번호를 쓰는 서로 다른 규정 두 개(번호 충돌)
                "relative_dir": "제7편_일반연구기관/제15장_WISE캠퍼스 일반연구기관",
                "filename": "7-15-1. WISE캠퍼스 옛 연구소 규정(2018.1.1.).hwp",
                "text": "옛 WISE 연구소 규정 본문 " * 10,
            },
            {
                "relative_dir": "제7편_일반연구기관/제14장_기타",
                "filename": "7-15-1. 다른 연구소 규정(2017.1.1.).hwp",
                "text": "다른 연구소 규정 본문 " * 10,
            },
            {  # 이전에 공식 수집했지만 이번 공식 현행에 없는 규정
                "relative_dir": "제2편_학칙/제9장_기타/공식_현행",
                "filename": "2-9-9. 사라진 규정(2025.01.01.).html",
                "title": "2-9-9. 사라진 규정",
                "text": "사라진 공식 규정 본문 " * 10,
                "source_type": "official_rule_web",
                "source_url": "https://rule.dongguk.edu/lmxsrv/law/lawFullContent.srv?SEQ=999&SEQ_HISTORY=1",
                "source_version": "999:1",
                "published_at": "2025-01-01",
                "rule_code": "2-9-9",
            },
            {  # 공식 사이트에 없는(폐지 추정) 규정
                "relative_dir": "제4편_위원회",
                "filename": "4-0-99. 폐지된 자문위원회 규정(2019.1.1.).hwp",
                "text": "폐지 추정 규정 본문 " * 10,
            },
        ]
    )
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return frame


class _TitledRuleSite(FakeRuleSite):
    """상세 제목을 목록 제목과 같게 돌려주는 사이트(스냅샷 파일명과 식별이 맞도록)."""

    TITLES = {
        "70": "학사과정 학칙시행세칙",
        "71": "교원양성과정 운영규정",
        "101": "대학원 장학금 지급내규",
    }

    def get(self, url: str, timeout: float = 0) -> _Response:
        match = _CONTENT_RE.search(url)
        if match and match.group(1) in self.TITLES:
            self.requests.append(url)
            return _Response(
                self.content_template.replace("{TITLE}", self.TITLES[match.group(1)])
            )
        return super().get(url, timeout)


def test_official_current_version_supersedes_snapshot_and_absent_rules_are_reported(tmp_path):
    path = tmp_path / "rules.csv"
    existing = _write_existing(path)
    site = _TitledRuleSite()

    official = sync_official_rules.sync_rule_source(
        output_path=path, collector=_collector(site)
    )
    merged = pd.read_csv(path).fillna("")
    coverage = official.attrs["coverage_report"]

    # 스냅샷 행은 하나도 지우지 않는다(폐지 추정 규정 포함).
    for filename in existing["filename"]:
        assert filename in set(merged["filename"])
    assert official.attrs["source_changed"] is True
    assert len(merged) == len(existing) + len(official)

    chunks = build_rule_chunks(merged.assign(db_id=range(1, len(merged) + 1)))
    latest = chunks.groupby("filename")["is_latest"].all()
    assert not latest["2-1-2. 학사과정 학칙시행세칙(2025.8.13.).hwp"]
    assert latest["2-1-2. 학사과정 학칙시행세칙(2026.08.10.).html"]
    assert not latest["2-2-2. 대학원 장학금 지급내규(2024.3.1.).hwp"]
    assert latest["2-2-2. 대학원 장학금 지급내규(2026.06.15.).html"]
    # 폐지 추정 규정은 숨기지 않는다(현행 질의에서도 남는다) — 보고서로만 드러낸다.
    assert latest["4-0-99. 폐지된 자문위원회 규정(2019.1.1.).hwp"]
    official_chunks = chunks[chunks["source_type"].eq("official_rule_web")]
    # 이전 공식 행(SEQ=999)도 지우지 않고 원문 URL을 그대로 유지한다.
    assert set(official_chunks["url"]) == set(official["source_url"]) | {
        "https://rule.dongguk.edu/lmxsrv/law/lawFullContent.srv?SEQ=999&SEQ_HISTORY=1"
    }
    dated = official_chunks[~official_chunks["filename"].str.startswith("7-15-2.")]
    assert dated["published_at"].ne("").all()

    assert coverage["crawl_complete"] is True
    # 2-1-3은 제목이 달라도 공식판과 번호가 같고 스냅샷 안에서 번호가 겹치지 않아 번호로 대체된다.
    assert coverage["superseded_count"] == 3
    assert [item["rule_code"] for item in coverage["superseded_by_code"]] == ["2-1-3"]
    assert coverage["superseded_by_code"][0]["official_title"] == "2-1-3. 교원양성과정 운영규정"
    assert not latest["2-1-3. 교원양성 운영지침(2024.1.1.).hwp"]
    assert coverage["snapshot_newer_count"] == 0
    # 스냅샷 안에서 7-15-1 번호를 두 규정이 공유 → 자동으로 묶지 않고 이름 변경 후보로 보고
    assert sorted(item["title"] for item in coverage["renamed_candidates"]) == [
        "7-15-1. WISE캠퍼스 옛 연구소 규정(2018.1.1.).hwp",
        "7-15-1. 다른 연구소 규정(2017.1.1.).hwp",
    ]
    assert latest["7-15-1. 다른 연구소 규정(2017.1.1.).hwp"]
    assert [item["rule_code"] for item in coverage["undated_official"]] == ["7-15-2"]
    assert coverage["vanished_official_suppressed"] is False
    assert [item["rule_code"] for item in coverage["vanished_official"]] == ["2-9-9"]
    assert coverage["abolished_candidates_suppressed"] is False
    assert coverage["abolished_candidate_count"] == 1
    assert coverage["abolished_candidates"][0]["rule_code"] == "4-0-99"
    assert coverage["new_official_count"] == len(official) - 2  # 2-1-2, 2-2-2 제목 일치

    summary = sync_official_rules.format_sync_summary(official)
    assert "폐지 추정 후보 1건" in summary
    assert "4-0-99" in summary


def test_second_sync_reuses_versions_and_reports_no_change(tmp_path):
    path = tmp_path / "rules.csv"
    _write_existing(path)
    sync_official_rules.sync_rule_source(output_path=path, collector=_collector(_TitledRuleSite()))
    before = path.read_bytes()

    site = _TitledRuleSite()
    again = sync_official_rules.sync_rule_source(output_path=path, collector=_collector(site))

    assert again.attrs["source_changed"] is False
    assert site.content_requests() == []
    assert path.read_bytes() == before


def test_discovery_failure_keeps_previous_source(tmp_path):
    path = tmp_path / "rules.csv"
    _write_existing(path)
    before = path.read_bytes()
    site = FakeRuleSite(failing={RULE_CATEGORY_TREE_URL})

    with pytest.raises(OfficialRuleCrawlError, match="discovery"):
        sync_official_rules.sync_rule_source(output_path=path, collector=_collector(site))
    assert path.read_bytes() == before


def test_large_category_failure_fraction_aborts_without_writing(tmp_path):
    path = tmp_path / "rules.csv"
    _write_existing(path)
    before = path.read_bytes()
    failing = {official_rule_list_url(seq) for seq in ("6", "7", "18")}
    site = FakeRuleSite(failing=failing)

    with pytest.raises(OfficialRuleCrawlError) as excinfo:
        sync_official_rules.sync_rule_source(output_path=path, collector=_collector(site))
    assert path.read_bytes() == before
    assert len(excinfo.value.report.failed_categories) == 3


def test_partial_failure_is_reported_distinctly_and_suppresses_abolished_report(tmp_path):
    path = tmp_path / "rules.csv"
    existing = _write_existing(path)
    # 두 번째 페이지만 실패 → 분류 1/6 실패(허용 비율 20% 이하)
    site = _TitledRuleSite(failing={official_rule_list_url("7", 2)})

    official = sync_official_rules.sync_rule_source(output_path=path, collector=_collector(site))
    crawl = official.attrs["crawl_report"]
    coverage = official.attrs["coverage_report"]
    merged = pd.read_csv(path).fillna("")

    assert crawl["complete"] is False
    assert [item["seq"] for item in crawl["failed_categories"]] == ["7"]
    assert crawl["failed_categories"][0]["error"] == "ConnectionError"
    # 실패한 분류의 규정은 없지만 이전 정본은 그대로 남는다.
    assert "2-2-1" not in set(official["rule_code"])
    for filename in existing["filename"]:
        assert filename in set(merged["filename"])
    # 부재를 믿을 수 없으므로 폐지 후보 판단을 보류한다.
    assert coverage["abolished_candidates_suppressed"] is True
    assert coverage["abolished_candidates"] == []
    summary = sync_official_rules.format_sync_summary(official)
    assert "[분류 실패] SEQ=7" in summary
    assert "판단 보류" in summary


def test_detail_failures_beyond_threshold_abort():
    failing = {
        f"https://rule.dongguk.edu/lmxsrv/law/lawFullContent.srv?SEQ={seq}&SEQ_HISTORY={hist}"
        for seq, hist in (("69", "3704"), ("70", "3690"))
    }
    with pytest.raises(OfficialRuleCrawlError, match="detail"):
        collect_official_rules(
            scope="academic", session=FakeRuleSite(failing=failing),
            request_delay=0, sleep=lambda _s: None,
        )


def test_request_budget_exhaustion_fails_safe():
    site = FakeRuleSite()
    with pytest.raises(OfficialRuleCrawlError) as excinfo:
        collect_official_rules(
            scope="all", session=site, request_delay=0, sleep=lambda _s: None,
            max_requests=3,
        )
    assert len(site.requests) == 3
    assert excinfo.value.report.budget_exhausted is True


# ── 목록 절단 방지 ─────────────────────────────────────────────────────────────


def _crawl_with_pages(pages: dict[str, str]):
    site = FakeRuleSite()
    site.pages.update(pages)
    return collect_official_rules(
        scope="all", session=site, request_delay=0, sleep=lambda _s: None,
        max_failed_ratio=0.5,
    )


def test_repeated_page_before_last_page_marks_category_failed():
    # 사이트가 마지막 쪽 전에 같은 쪽을 반복해 돌려주면 조용히 끊지 않는다.
    official = _crawl_with_pages({official_rule_list_url("7", 2): _fixture("list_seq7_p1.html")})
    report = official.attrs["crawl_report"]
    assert report["complete"] is False
    assert [item["seq"] for item in report["failed_categories"]] == ["7"]
    assert report["failed_categories"][0]["error"] == "IncompleteRuleListingError"


def test_fewer_rows_than_reported_total_marks_category_failed():
    html = _fixture("list_seq6.html").replace('getPageListSet("5"', 'getPageListSet("6"')
    official = _crawl_with_pages({official_rule_list_url("6"): html})
    assert [item["seq"] for item in official.attrs["crawl_report"]["failed_categories"]] == ["6"]


def test_page_cap_and_missing_paging_mark_category_failed():
    too_many = _fixture("list_seq18.html").replace('getPageListSet("2"', 'getPageListSet("9999"')
    no_paging = re.sub(r"getPageListSet\([^)]*\);", "", _fixture("list_seq59.html"))
    official = _crawl_with_pages(
        {official_rule_list_url("18"): too_many, official_rule_list_url("59"): no_paging}
    )
    failed = {item["seq"] for item in official.attrs["crawl_report"]["failed_categories"]}
    assert failed == {"18", "59"}
    coverage = sync_official_rules.build_rule_coverage_report(
        pd.DataFrame(), official, crawl_complete=official.attrs["crawl_report"]["complete"]
    )
    assert coverage["abolished_candidates_suppressed"] is True


# ── 규정번호 동일성 ────────────────────────────────────────────────────────────


def test_rule_code_is_identity_when_official_and_snapshot_share_a_code():
    from src.services.rule_versioning import annotate_rule_versions

    frame = pd.DataFrame(
        [
            {"doc_id": "hwp", "title": "2-1-1. 학칙(2025.8.5.).hwp", "published_at": "",
             "source_type": "rules_text"},
            {"doc_id": "web", "title": "2-1-1. 동국대학교 학칙", "published_at": "2026-08-12",
             "source_type": "official_rule_web"},
            # 번호 없는 규정은 제목 기준 그대로
            {"doc_id": "a-old", "title": "기숙사 운영지침(2020.1.1.).hwp", "published_at": "",
             "source_type": "rules_text"},
            {"doc_id": "a-new", "title": "기숙사 운영지침(2024.1.1.).hwp", "published_at": "",
             "source_type": "rules_text"},
        ]
    )
    annotated = annotate_rule_versions(frame).set_index("doc_id")
    assert annotated.loc["hwp", "canonical_key"] == annotated.loc["web", "canonical_key"]
    assert annotated["is_latest"].to_dict() == {
        "hwp": False, "web": True, "a-old": False, "a-new": True,
    }


def test_colliding_snapshot_codes_and_frames_without_source_type_keep_title_identity():
    from src.services.rule_versioning import annotate_rule_versions

    colliding = pd.DataFrame(
        [
            {"doc_id": "x", "title": "6-2-1. WISE캠퍼스 보건진료센터 규정(2022.4.26.).hwp",
             "published_at": "", "source_type": "rules_text"},
            {"doc_id": "y", "title": "6-2-1. 건강증진센터규정(2023.3.30.).hwp",
             "published_at": "", "source_type": "rules_text"},
            {"doc_id": "z", "title": "6-2-1. 건강증진센터규정", "published_at": "2026-01-01",
             "source_type": "official_rule_web"},
        ]
    )
    annotated = annotate_rule_versions(colliding).set_index("doc_id")
    assert annotated["is_latest"].to_dict() == {"x": True, "y": False, "z": True}

    legacy = pd.DataFrame(
        [
            {"doc_id": "p", "title": "2-1-1. 학칙(2025.8.5.).hwp", "published_at": ""},
            {"doc_id": "q", "title": "2-1-1. 동국대학교 학칙", "published_at": "2026-08-12"},
        ]
    )
    assert annotate_rule_versions(legacy)["is_latest"].tolist() == [True, True]


# ── 보고·로그·재수집 knob ───────────────────────────────────────────────────────


def test_sync_logs_summary_and_attaches_it_without_changing_scheduler_attrs(tmp_path, caplog):
    path = tmp_path / "rules.csv"
    _write_existing(path)
    with caplog.at_level("INFO", logger=sync_official_rules.logger.name):
        official = sync_official_rules.sync_rule_source(
            output_path=path, collector=_collector(_TitledRuleSite())
        )
    assert official.attrs["source_changed"] is True
    assert set(official.columns) >= {"source_version", "source_url", "published_at"}
    summary = official.attrs["coverage_summary"]
    assert summary == sync_official_rules.format_sync_summary(official)
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert summary in logged
    for token in ("4-0-99", "2-9-9", "7-15-2", "[번호 기준 대체] 2-1-3"):
        assert token in summary


def test_crawl_failure_is_logged_with_partial_report(tmp_path, caplog):
    path = tmp_path / "rules.csv"
    _write_existing(path)
    site = FakeRuleSite(failing={official_rule_list_url(seq) for seq in ("6", "7", "18")})
    with caplog.at_level("ERROR", logger=sync_official_rules.logger.name):
        with pytest.raises(OfficialRuleCrawlError):
            sync_official_rules.sync_rule_source(output_path=path, collector=_collector(site))
    assert "기존 정본 유지" in caplog.text


def test_academic_scope_never_reports_absence(tmp_path):
    path = tmp_path / "rules.csv"
    _write_existing(path)
    official = sync_official_rules.sync_rule_source(
        output_path=path, scope="academic", collector=_collector(_TitledRuleSite())
    )
    coverage = official.attrs["coverage_report"]
    assert official.attrs["crawl_report"]["complete"] is True
    assert coverage["abolished_candidates_suppressed"] is True
    assert coverage["vanished_official_suppressed"] is True
    assert coverage["vanished_official"] == []


def test_refresh_details_bypasses_version_reuse(tmp_path, monkeypatch):
    path = tmp_path / "rules.csv"
    _write_existing(path)
    sync_official_rules.sync_rule_source(output_path=path, collector=_collector(_TitledRuleSite()))

    forced = _TitledRuleSite()
    sync_official_rules.sync_rule_source(
        output_path=path, refresh_details=True, collector=_collector(forced)
    )
    assert len(forced.content_requests()) == 25

    monkeypatch.setenv(sync_official_rules.REFRESH_DETAILS_ENV, "1")
    via_env = _TitledRuleSite()
    sync_official_rules.sync_rule_source(output_path=path, collector=_collector(via_env))
    assert len(via_env.content_requests()) == 25


def test_budget_exhaustion_during_details_marks_crawl_incomplete(tmp_path):
    path = tmp_path / "rules.csv"
    _write_existing(path)
    # 트리 1 + 목록 7쪽 = 8, 상세 25건 중 22건만 예산 안(실패 3/25 = 12% < 20%)
    official = sync_official_rules.sync_rule_source(
        output_path=path, collector=_collector(_TitledRuleSite(), max_requests=30)
    )
    crawl = official.attrs["crawl_report"]
    assert crawl["budget_exhausted"] is True
    assert crawl["complete"] is False
    assert len(crawl["detail_failures"]) == 3
    assert official.attrs["coverage_report"]["abolished_candidates_suppressed"] is True
    assert "예산 초과=True" in official.attrs["coverage_summary"]


# ── 번호를 공유하는 서로 다른 공식 규정(서울·WISE) ─────────────────────────────


def _shared_code_site(seoul_heading: str) -> "_TitledRuleSite":
    """SEQ=69(서울 학칙)와 SEQ=600(WISE 학칙)이 같은 번호 2-1-1을 쓰는 사이트."""
    site = _TitledRuleSite()
    site.TITLES = {**_TitledRuleSite.TITLES, "69": seoul_heading, "600": "WISE캠퍼스 학칙"}
    extra_row = """      <tr>
        <td></td><td class="tbody_c">3</td>
        <td class="tbody_txt">
          <a href="javascript:fullPopupPost(600, 4600,'4600');">2-1-1&nbsp; WISE캠퍼스 학칙</a>
        </td>
        <td class="tbody_c"><script type="text/javascript">showDate('20260901', '2');</script></td>
        <td></td><td></td>
      </tr>
    </tbody>"""
    html = _fixture("list_seq18.html").replace("    </tbody>", extra_row, 1)
    html = html.replace('getPageListSet("2"', 'getPageListSet("3"')
    site.pages[official_rule_list_url("18")] = html
    return site


def test_official_rules_sharing_a_code_with_different_titles_are_both_kept():
    site = _shared_code_site("학칙")
    official = collect_official_rules(
        scope="all", session=site, request_delay=0, sleep=lambda _s: None
    )
    shared = official.loc[official["rule_code"].eq("2-1-1")]
    assert sorted(shared["source_version"]) == ["600:4600", "69:3704"]
    assert official.attrs["crawl_report"]["shared_codes"] == ["2-1-1"]
    assert official.attrs["crawl_report"]["duplicate_codes"] == []


def _shared_code_snapshot(path: Path) -> None:
    pd.DataFrame(
        [
            {
                "relative_dir": "제2편_학칙/제1장_대학",
                "filename": "2-1-1. 학칙(2025.8.5.).hwp",
                "text": "서울 학칙 옛 본문 " * 10,
            }
        ]
    ).to_csv(path, index=False, encoding="utf-8-sig")


def _latest_by_filename(path: Path) -> pd.Series:
    merged = pd.read_csv(path).fillna("")
    chunks = build_rule_chunks(merged.assign(db_id=range(1, len(merged) + 1)))
    return chunks.groupby("filename")["is_latest"].all()


def test_later_wise_rule_with_shared_code_does_not_hide_seoul_rule(tmp_path):
    # 서울 공식판 제목이 스냅샷과 같을 때: 서울 스냅샷은 서울 공식판(제목 동일성)으로만 대체되고,
    # 더 늦은 WISE 규정은 서울 규정을 대체하지 않는다.
    path = tmp_path / "rules.csv"
    _shared_code_snapshot(path)
    sync_official_rules.sync_rule_source(output_path=path, collector=_collector(_shared_code_site("학칙")))
    latest = _latest_by_filename(path)
    assert latest["2-1-1. 학칙(2026.08.12.).html"]
    assert latest["2-1-1. WISE캠퍼스 학칙(2026.09.01.).html"]
    assert not latest["2-1-1. 학칙(2025.8.5.).hwp"]


def test_shared_code_disables_code_identity_so_snapshot_is_never_hidden(tmp_path):
    # 서울 공식판 제목이 스냅샷과 달라(상세 제목 표기 차이) 제목으로 묶이지 않고,
    # 번호는 WISE와 공유돼 번호로도 묶지 않는다 → 서울 스냅샷은 현행에 남는다.
    path = tmp_path / "rules.csv"
    _shared_code_snapshot(path)
    official = sync_official_rules.sync_rule_source(
        output_path=path, collector=_collector(_shared_code_site("동국대학교 학칙"))
    )
    latest = _latest_by_filename(path)
    assert latest["2-1-1. 학칙(2025.8.5.).hwp"]
    assert latest["2-1-1. 동국대학교 학칙(2026.08.12.).html"]
    assert latest["2-1-1. WISE캠퍼스 학칙(2026.09.01.).html"]
    renamed = official.attrs["coverage_report"]["renamed_candidates"]
    assert [item["title"] for item in renamed] == ["2-1-1. 학칙(2025.8.5.).hwp"]


def test_code_identity_excludes_codes_with_several_official_titles():
    from src.services.rule_versioning import annotate_rule_versions

    frame = pd.DataFrame(
        [
            {"doc_id": "hwp", "title": "6-2-1. 건강증진센터규정(2023.3.30.).hwp",
             "published_at": "", "source_type": "rules_text"},
            {"doc_id": "seoul", "title": "6-2-1. 건강증진센터 운영규정", "published_at": "2026-01-01",
             "source_type": "official_rule_web"},
            {"doc_id": "wise", "title": "6-2-1. WISE캠퍼스 보건진료센터 규정",
             "published_at": "2026-05-01", "source_type": "official_rule_web"},
        ]
    )
    annotated = annotate_rule_versions(frame).set_index("doc_id")
    assert annotated["is_latest"].to_dict() == {"hwp": True, "seoul": True, "wise": True}
