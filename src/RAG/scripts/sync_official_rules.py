"""공식 규정관리시스템 현행판을 보존적으로 병합하고 rules 인덱스를 갱신한다.

기본 범위는 공식 사이트 분류 전체다(``RAG_OFFICIAL_RULES_SCOPE=all``).
``--scope academic`` 또는 ``RAG_OFFICIAL_RULES_SCOPE=academic``이면 이전처럼
제2편 제1장(대학, SEQ=6)만 수집한다.

병합은 추가만 한다. HWP 스냅샷 행은 지우거나 숨기지 않는다. 같은 규정(정규화 제목이
같은 규정)의 공식 현행판이 들어오면 색인 단계의 ``annotate_rule_versions``가 공식판만
``is_latest``로 표시해 구판을 현행 질의에서 뺀다. 공식 사이트에 없는 스냅샷 규정
(폐지 추정)은 숨기지 않고 보고서로만 드러내 사람 검토에 넘긴다.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import DATA_SOURCES  # noqa: E402
from src.crawlers.dongguk_rule import (  # noqa: E402
    RULE_SCOPES,
    OfficialRuleCrawlError,
    collect_official_rules,
    merge_official_rule_versions,
)
from src.services.rule_versioning import (  # noqa: E402
    annotate_rule_versions,
    canonical_rule_key,
)

logger = logging.getLogger(__name__)

OFFICIAL_SOURCE_TYPE = "official_rule_web"
REFRESH_DETAILS_ENV = "RAG_OFFICIAL_RULES_REFRESH_DETAILS"
_COMPARISON_COLUMNS = [
    "source_version",
    "published_at",
    "source_url",
    "source_page_url",
    "relative_dir",
    "title",
    "text",
]
_LEADING_CODE_RE = re.compile(r"^\s*(\d+-\d+-\d+)")
REPORT_SAMPLE_LIMIT = 50


def _column(frame: pd.DataFrame, name: str) -> pd.Series:
    if name in frame.columns:
        return frame[name].fillna("").astype(str)
    return pd.Series("", index=frame.index, dtype=str)


def _is_official(frame: pd.DataFrame) -> pd.Series:
    return _column(frame, "source_type").eq(OFFICIAL_SOURCE_TYPE)


def _identity_title(frame: pd.DataFrame) -> pd.Series:
    """``build_rule_chunks``와 같은 우선순위(title → filename)로 규정 식별 제목을 만든다."""
    title = _column(frame, "title")
    filename = _column(frame, "filename")
    return title.where(title.str.strip().ne(""), filename)


def _rule_code(frame: pd.DataFrame) -> pd.Series:
    code = _column(frame, "rule_code")
    from_name = _identity_title(frame).map(
        lambda value: (_LEADING_CODE_RE.match(value) or [None, ""])[1]
    )
    return code.where(code.str.strip().ne(""), from_name)


def build_rule_coverage_report(
    existing: pd.DataFrame,
    official: pd.DataFrame,
    *,
    crawl_complete: bool,
    scope: str = "all",
) -> Dict[str, object]:
    """스냅샷·이전 공식 행이 이번 공식 현행판에 어떻게 대응하는지 사람 검토용으로 요약한다.

    판정은 색인 단계(``annotate_rule_versions``)와 같은 동일성 규칙을 쓴다(제목, 그리고
    공식판과 번호가 같고 스냅샷 안에서 번호가 겹치지 않으면 규정번호).

    - superseded: 공식 현행판이 더 최신이라 색인에서 구판(is_latest=False)이 되는 스냅샷 규정
      (그중 제목이 달라 번호로 묶인 것은 ``superseded_by_code``에 따로 적는다)
    - snapshot_newer: 같은 규정인데 스냅샷 날짜가 공식판보다 늦음(검토 필요)
    - renamed_candidates: 번호는 공식에 있으나 스냅샷 안 번호 충돌로 자동으로 묶지 못함
    - abolished_candidates: 번호·제목 모두 공식 현행에 없는 스냅샷 규정
    - vanished_official: 이전에 공식 수집했으나 이번 공식 현행에 SEQ·번호가 모두 없는 규정
    - undated_official: 개정일이 비어 버전 순서를 날짜로 정할 수 없는 공식 규정
    - new_official: 기존 정본에 없던 공식 규정(스냅샷 누락분 등)

    어떤 규정도 숨기지 않는다. 부재 판단(abolished/vanished)은 전체 범위(scope=all)를
    완전히 수집한 경우에만 하고, 아니면 목록을 비우고 ``*_suppressed``를 True로 둔다.
    """
    existing = existing.copy() if not existing.empty else pd.DataFrame()
    snapshot = existing.loc[~_is_official(existing)] if not existing.empty else existing
    snapshot = snapshot.loc[_column(snapshot, "text").str.strip().ne("")] if not snapshot.empty else snapshot
    prior_official = existing.loc[_is_official(existing)] if not existing.empty else existing

    official_titles = _identity_title(official)
    official_codes_series = _rule_code(official)
    official_keys = set(official_titles.map(canonical_rule_key))
    official_codes = {code for code in official_codes_series if code}
    official_by_code = dict(zip(official_codes_series, official_titles))
    official_seqs = {
        version.split(":", 1)[0] for version in _column(official, "source_version") if version
    }
    absence_trusted = bool(crawl_complete) and scope == "all"

    superseded: List[Dict[str, str]] = []
    superseded_by_code: List[Dict[str, str]] = []
    snapshot_newer: List[Dict[str, str]] = []
    renamed: List[Dict[str, str]] = []
    abolished: List[Dict[str, str]] = []
    if not snapshot.empty:
        snapshot_titles = _identity_title(snapshot)
        combined = pd.DataFrame(
            {
                "title": snapshot_titles.tolist() + official_titles.tolist(),
                "filename": _column(snapshot, "filename").tolist() + _column(official, "filename").tolist(),
                "published_at": _column(snapshot, "published_at").tolist()
                + _column(official, "published_at").tolist(),
                "source_type": [""] * len(snapshot) + [OFFICIAL_SOURCE_TYPE] * len(official),
                "rule_code": _rule_code(snapshot).tolist() + official_codes_series.tolist(),
                "origin": ["snapshot"] * len(snapshot) + ["official"] * len(official),
                "relative_dir": _column(snapshot, "relative_dir").tolist()
                + _column(official, "relative_dir").tolist(),
            }
        )
        combined["doc_id"] = [f"row-{index}" for index in range(len(combined))]
        annotated = annotate_rule_versions(combined)
        official_group_keys = set(annotated.loc[annotated["origin"].eq("official"), "canonical_key"])
        for row in annotated.loc[annotated["origin"].eq("snapshot")].itertuples(index=False):
            entry = {"rule_code": row.rule_code, "title": row.title, "relative_dir": row.relative_dir}
            if row.canonical_key in official_group_keys:
                if row.is_latest:
                    snapshot_newer.append(entry)
                else:
                    superseded.append(entry)
                    if canonical_rule_key(row.title) not in official_keys:
                        superseded_by_code.append(
                            {**entry, "official_title": official_by_code.get(row.rule_code, "")}
                        )
            elif row.rule_code and row.rule_code in official_codes:
                renamed.append({**entry, "official_title": official_by_code.get(row.rule_code, "")})
            else:
                abolished.append(entry)

    vanished: List[Dict[str, str]] = []
    if not prior_official.empty:
        seen_versions: set[str] = set()
        for version, code, title, url in zip(
            _column(prior_official, "source_version"),
            _rule_code(prior_official),
            _identity_title(prior_official),
            _column(prior_official, "source_url"),
        ):
            seq = version.split(":", 1)[0]
            if not seq or seq in official_seqs or (code and code in official_codes):
                continue
            if seq in seen_versions:
                continue
            seen_versions.add(seq)
            vanished.append({"rule_code": code, "title": title, "seq": seq, "source_url": url})

    known_keys = set(_identity_title(snapshot).map(canonical_rule_key)) if not snapshot.empty else set()
    known_keys |= set(_identity_title(prior_official).map(canonical_rule_key)) if not prior_official.empty else set()
    new_official = [
        {"rule_code": code, "title": title}
        for code, title in zip(official_codes_series, official_titles)
        if canonical_rule_key(title) not in known_keys
    ]
    undated = [
        {"rule_code": code, "title": title, "source_url": url}
        for code, title, url, published in zip(
            official_codes_series,
            official_titles,
            _column(official, "source_url"),
            _column(official, "published_at"),
        )
        if not published.strip()
    ]

    report: Dict[str, object] = {
        "scope": scope,
        "crawl_complete": bool(crawl_complete),
        "official_current_rules": int(len(official)),
        "snapshot_rules": int(len(snapshot)),
        "superseded_count": len(superseded),
        "superseded_by_code_count": len(superseded_by_code),
        "superseded_by_code": superseded_by_code[:REPORT_SAMPLE_LIMIT],
        "snapshot_newer_count": len(snapshot_newer),
        "snapshot_newer": snapshot_newer[:REPORT_SAMPLE_LIMIT],
        "renamed_candidate_count": len(renamed),
        "renamed_candidates": renamed[:REPORT_SAMPLE_LIMIT],
        "new_official_count": len(new_official),
        "new_official": new_official[:REPORT_SAMPLE_LIMIT],
        "undated_official_count": len(undated),
        "undated_official": undated,
    }
    if absence_trusted:
        report.update(
            abolished_candidate_count=len(abolished),
            abolished_candidates=abolished,
            abolished_candidates_suppressed=False,
            vanished_official_count=len(vanished),
            vanished_official=vanished,
            vanished_official_suppressed=False,
        )
    else:
        report.update(
            abolished_candidate_count=None,
            abolished_candidates=[],
            abolished_candidates_suppressed=True,
            vanished_official_count=None,
            vanished_official=[],
            vanished_official_suppressed=True,
        )
    return report


def _known_official_versions(existing: pd.DataFrame) -> Dict[str, Dict[str, str]]:
    if existing.empty:
        return {}
    official = existing.loc[_is_official(existing)]
    known: Dict[str, Dict[str, str]] = {}
    for record in official.to_dict(orient="records"):
        version = str(record.get("source_version", "") or "")
        if version:
            known[version] = {key: str(value) for key, value in record.items()}
    return known


def _official_rows_changed(existing: pd.DataFrame, official: pd.DataFrame) -> bool:
    """공식 수집 결과 중 기존 정본에 똑같이 없는 행이 하나라도 있으면 변경이다.

    병합은 추가만 하므로, 모든 공식 행이 이미 같은 내용으로 있으면 파일이 바뀌지 않는다.
    """
    prior = existing.loc[_is_official(existing)] if not existing.empty else existing

    def records(frame: pd.DataFrame) -> set[tuple[str, ...]]:
        if frame.empty:
            return set()
        return set(
            zip(*[_column(frame, column).tolist() for column in _COMPARISON_COLUMNS])
        )

    return not records(official) <= records(prior)


def resolve_refresh_details(refresh_details: Optional[bool] = None) -> bool:
    if refresh_details is not None:
        return bool(refresh_details)
    return os.getenv(REFRESH_DETAILS_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def sync_rule_source(
    *,
    output_path: Path | None = None,
    scope: Optional[str] = None,
    write: bool = True,
    refresh_details: Optional[bool] = None,
    collector=collect_official_rules,
) -> pd.DataFrame:
    """공식 현행 규정을 수집해 규정 정본 CSV에 보존적으로 병합한다.

    수집이 실패(``OfficialRuleCrawlError`` 등)하면 수집 보고를 로그로 남기고 예외를 그대로
    올린다. 파일은 건드리지 않는다. 반환 DataFrame의 attrs:
    ``source_changed``(정본 변경 여부), ``crawl_report``(수집 보고), ``coverage_report``
    (스냅샷 대체·폐지 후보 보고), ``coverage_summary``(사람이 읽는 요약 문자열).
    요약은 ``logger``(INFO)에도 남겨 정기 작업 로그에서 볼 수 있게 한다.
    ``refresh_details``(또는 ``RAG_OFFICIAL_RULES_REFRESH_DETAILS=1``)이면 연혁이 같아도
    상세 본문을 다시 받는다.
    """
    path = output_path or DATA_SOURCES["rules"]
    existing = (
        pd.read_csv(path).fillna("").astype(str)
        if path.exists()
        else pd.DataFrame()
    )
    known = {} if resolve_refresh_details(refresh_details) else _known_official_versions(existing)
    try:
        official = collector(scope=scope, known_versions=known)
    except OfficialRuleCrawlError as exc:
        partial = exc.report.to_dict() if exc.report is not None else {}
        logger.error(
            "[rules] 공식 규정 수집 실패 — 기존 정본 유지: %s | 분류 실패 %s · 상세 실패 %s · 요청 %s/%s",
            exc,
            [item.get("seq") for item in partial.get("failed_categories", [])],
            [item.get("rule_code") for item in partial.get("detail_failures", [])],
            partial.get("requests_made"),
            partial.get("request_budget"),
        )
        raise
    crawl_report = dict(official.attrs.get("crawl_report") or {})
    merged = merge_official_rule_versions(existing, official)
    changed = _official_rows_changed(existing, official)
    if write and (changed or not path.exists()):
        path.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(path, index=False, encoding="utf-8-sig")
    official.attrs["source_changed"] = changed
    official.attrs["crawl_report"] = crawl_report
    official.attrs["coverage_report"] = build_rule_coverage_report(
        existing,
        official,
        crawl_complete=bool(crawl_report.get("complete", False)),
        scope=str(crawl_report.get("scope") or "all"),
    )
    summary = format_sync_summary(official)
    official.attrs["coverage_summary"] = summary
    logger.info("[rules] 공식 규정 동기화 보고\n%s", summary)
    return official


def _list_lines(label: str, items: List[Dict[str, str]], *, with_official: bool = False) -> List[str]:
    lines = []
    for item in items:
        suffix = f" → {item.get('official_title')}" if with_official else ""
        lines.append(f"  [{label}] {item.get('rule_code') or '-'} {item.get('title')}{suffix}")
    return lines


def format_sync_summary(official: pd.DataFrame) -> str:
    """집계와 규정번호·제목만 담은 요약(개인정보 없음)."""
    crawl = official.attrs.get("crawl_report") or {}
    coverage = official.attrs.get("coverage_report") or {}
    lines = [
        f"공식 현행 규정 {len(official)}건 수집 (범위={crawl.get('scope', '?')}, "
        f"완전={crawl.get('complete')}, 요청 {crawl.get('requests_made', '?')}/"
        f"{crawl.get('request_budget', '?')}, 예산 초과={crawl.get('budget_exhausted', False)}, "
        f"상세 신규 {crawl.get('detail_fetched', 0)} · 재사용 {crawl.get('detail_reused', 0)})",
        f"분류: 발견 {crawl.get('categories_discovered', 0)} · 수집 "
        f"{crawl.get('categories_crawled', 0)} · 제외 {len(crawl.get('excluded_categories', []))} · "
        f"실패 {len(crawl.get('failed_categories', []))}",
    ]
    for failure in crawl.get("failed_categories", []):
        lines.append(f"  [분류 실패] SEQ={failure.get('seq')} {failure.get('name')} ({failure.get('error')})")
    for failure in crawl.get("detail_failures", []):
        lines.append(
            f"  [상세 실패] {failure.get('rule_code')} {failure.get('title')} ({failure.get('error')})"
        )
    lines.append(
        f"스냅샷 대체 {coverage.get('superseded_count', 0)}"
        f"(번호 기준 {coverage.get('superseded_by_code_count', 0)}) · 스냅샷이 더 최신 "
        f"{coverage.get('snapshot_newer_count', 0)} · 이름 변경 후보 "
        f"{coverage.get('renamed_candidate_count', 0)} · 공식 신규 {coverage.get('new_official_count', 0)} · "
        f"개정일 없는 공식 {coverage.get('undated_official_count', 0)}"
    )
    lines += _list_lines("개정일 없음", coverage.get("undated_official", []))
    lines += _list_lines("스냅샷이 더 최신", coverage.get("snapshot_newer", []))
    lines += _list_lines("번호 기준 대체", coverage.get("superseded_by_code", []), with_official=True)
    lines += _list_lines("이름 변경 후보", coverage.get("renamed_candidates", []), with_official=True)
    if coverage.get("abolished_candidates_suppressed"):
        lines.append("폐지 추정 후보·사라진 공식 규정: 수집이 불완전하거나 부분 범위라 판단 보류")
    else:
        lines.append(
            f"폐지 추정 후보 {coverage.get('abolished_candidate_count', 0)}건 · 사라진 공식 규정 "
            f"{coverage.get('vanished_official_count', 0)}건 (숨기지 않음, 사람 검토 필요)"
        )
        lines += _list_lines("폐지 추정", coverage.get("abolished_candidates", []))
        lines += _list_lines("공식에서 사라짐", coverage.get("vanished_official", []))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="CSV 정본만 갱신하고 SQLite/검색 인덱스는 건드리지 않는다.",
    )
    parser.add_argument(
        "--scope",
        choices=RULE_SCOPES,
        default=None,
        help="all=공식 분류 전체(기본), academic=제2편 제1장(SEQ=6)만. "
        "생략하면 RAG_OFFICIAL_RULES_SCOPE를 따른다.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="수집·보고만 하고 CSV·인덱스를 쓰지 않는다.",
    )
    parser.add_argument(
        "--refresh-details",
        action="store_true",
        default=None,
        help=f"연혁이 같아도 상세 본문을 다시 받는다(또는 {REFRESH_DETAILS_ENV}=1).",
    )
    args = parser.parse_args()
    official = sync_rule_source(
        scope=args.scope, write=not args.dry_run, refresh_details=args.refresh_details
    )
    print(official.attrs["coverage_summary"])
    if args.dry_run or args.source_only:
        return

    from src.database import init_db
    from src.pipelines.ingest import ingest_rules

    init_db()
    chunks, _, _ = ingest_rules(force_source_reload=True)
    print(f"rules 인덱스 {len(chunks)}개 청크를 재구축했습니다.")


if __name__ == "__main__":
    main()
