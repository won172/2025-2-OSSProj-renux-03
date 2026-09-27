"""동국대 학칙 HWP와 공식 규정관리시스템 현행판을 정규화한다."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import math
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urljoin
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET

import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.config import BASE_DIR, DATA_DIR
from src.services.rule_versioning import canonical_rule_title

RULE_ROOT = BASE_DIR / "dongguk_rule"
OUTPUT_PATH = DATA_DIR / "dongguk_rule_texts.csv"
OFFICIAL_RULE_BASE_URL = "https://rule.dongguk.edu"
ACADEMIC_RULE_CATEGORY_SEQ = "6"
ACADEMIC_RULE_LIST_URL = (
    f"{OFFICIAL_RULE_BASE_URL}/lmxsrv/law/lawListManager.srv"
    f"?LAWGROUP=1&PAGE_MODE=&SEQ={ACADEMIC_RULE_CATEGORY_SEQ}"
)
# 규정정보 분류 트리(LAWGROUP=1: 규정). 편/장 분류 전체를 여기서 찾는다.
RULE_CATEGORY_TREE_URL = (
    f"{OFFICIAL_RULE_BASE_URL}/lmxsrv/law/lawTree.srv?LAWGROUP=1&MODE=srvlist"
)
# 현행 대학 규정이 아닌 분류. 폐지규정은 폐지본, 상위법은 대학 규정이 아닌 법령이다.
DEFAULT_EXCLUDED_CATEGORY_NAMES = ("폐지규정", "상위법")
OFFICIAL_RULE_RELATIVE_SUFFIX = "공식_현행"

RULE_SCOPE_ENV = "RAG_OFFICIAL_RULES_SCOPE"  # all(기본) | academic(SEQ=6 전용, 이전 동작)
RULE_REQUEST_DELAY_ENV = "RAG_OFFICIAL_RULES_REQUEST_DELAY_SECONDS"
RULE_MAX_REQUESTS_ENV = "RAG_OFFICIAL_RULES_MAX_REQUESTS"
RULE_MAX_FAILED_RATIO_ENV = "RAG_OFFICIAL_RULES_MAX_FAILED_RATIO"
RULE_SCOPES = ("all", "academic")
DEFAULT_REQUEST_DELAY_SECONDS = 0.5
# 목록 약 80분류 + 추가 페이지 약 40 + 현행 규정 상세 약 520. 첫 전체 수집이 들어갈 여유.
DEFAULT_MAX_REQUESTS = 1200
DEFAULT_MAX_FAILED_RATIO = 0.2
DEFAULT_PAGE_SIZE = 15
MAX_PAGES_PER_CATEGORY = 40

_LIST_ID_RE = re.compile(r"fullPopupPost\((\d+)\s*,\s*(\d+)")
_LIST_DATE_RE = re.compile(r"showDate\('(\d{4})(\d{2})(\d{2})'")
_RULE_CODE_RE = re.compile(r"\b(\d+-\d+-\d+)\b")
_TREE_ITEM_RE = re.compile(r"var\s+tree(\d+)\s*=\s*new\s+WebFXTree(?:Item)?\(\s*'([^']*)'")
_TREE_ACTION_RE = re.compile(
    r"tree(\d+)\.action\s*=\s*\"javascript:gotoLawList\(\s*'(\d+)'\s*,\s*'(\d*)'\s*,"
    r"\s*'([^']*)'\s*,\s*'(\d*)'"
)
_TREE_ADD_RE = re.compile(r"tree(\d+)\.add\(\s*tree(\d+)\s*\)")
_LIST_TOTAL_RE = re.compile(
    r"getPageListSet\(\s*\"(\d+)\"\s*,\s*\"(\d+)\"\s*,\s*\"[^\"]*\"\s*,\s*\"(\d+)\"\s*\)"
)


def list_hwp_files(root: Path) -> List[Path]:
    return sorted(path for path in root.rglob("*.hwp") if path.is_file())


def extract_text_from_zip_hwp(path: Path) -> Optional[str]:
    try:
        with ZipFile(path) as zf:
            section_names = sorted(name for name in zf.namelist() if name.startswith("BodyText/Section"))
            if not section_names:
                return None
            paragraphs: List[str] = []
            for section_name in section_names:
                with zf.open(section_name) as section_file:
                    xml_data = section_file.read()
                try:
                    root = ET.fromstring(xml_data)
                except ET.ParseError:
                    continue
                texts: List[str] = []
                for tag in root.iter():
                    if tag.tag.endswith("txt") and tag.text:
                        texts.append(tag.text)
                if texts:
                    paragraphs.append("".join(texts))
            if not paragraphs:
                return None
            return "".join(paragraphs)
    except BadZipFile:
        return None


def extract_text_using_hwp5txt(path: Path) -> Optional[str]:
    """
    Uses the hwp5txt command line tool to extract text from HWP files.
    Requires 'pyhwp' package to be installed.
    """
    try:
        # Run hwp5txt with output to stdout
        result = subprocess.run(
            ["hwp5txt", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore"
        )
        
        if result.returncode == 0:
            return result.stdout
        else:
            # If hwp5txt fails, it might output empty string or error
            return None
    except FileNotFoundError:
        # hwp5txt command not found
        return None
    except Exception:
        return None


def extract_text_from_hwp(path: Path) -> Tuple[str, Optional[str], List[str]]:
    failures: List[str] = []
    
    # 1. Try hwp5txt (Best method for HWP 5.0)
    text = extract_text_using_hwp5txt(path)
    if text and len(text.strip()) > 0:
        return "hwp5txt", text, failures
    else:
        failures.append("hwp5txt_failed")

    # 2. Try zip method (For HWPX or if hwp5txt fails on zip-based format)
    text = extract_text_from_zip_hwp(path)
    if text:
        return "zip", text, failures
    else:
        failures.append("zip_failed")

    return "unknown", None, failures



def summarise_relative_path(path: Path, root: Path) -> Tuple[str, str]:
    rel_path = path.relative_to(root)
    parent = str(rel_path.parent)
    return parent, rel_path.name


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_official_rule_list(html: str) -> List[Dict[str, str]]:
    """규정 목록 HTML에서 현재 SEQ/연혁번호/개정일을 읽는다."""
    soup = BeautifulSoup(html, "lxml")
    records: List[Dict[str, str]] = []
    for row in soup.select("tbody.tbody tr"):
        title_cell = row.select_one("td.tbody_txt")
        if title_cell is None:
            continue
        link = title_cell.find("a", href=True)
        match = _LIST_ID_RE.search(str(link.get("href") if link else ""))
        if match is None:
            continue
        seq, history = match.groups()
        title = clean_text(title_cell.get_text(" ", strip=True))
        code_match = _RULE_CODE_RE.search(title)
        code = code_match.group(1) if code_match else ""
        if code and title.startswith(code):
            title = title[len(code) :].strip()
        date_match = _LIST_DATE_RE.search(str(row))
        # 개정일이 비어 있는 현행 규정도 누락시키지 않는다(버전 정렬은 목록 순서로).
        published_at = ""
        if date_match is not None:
            try:
                published_at = date(*map(int, date_match.groups())).isoformat()
            except ValueError:
                published_at = ""
        records.append(
            {
                "rule_code": code,
                "title": title,
                "seq": seq,
                "seq_history": history,
                "published_at": published_at,
                "source_url": urljoin(
                    OFFICIAL_RULE_BASE_URL,
                    f"/lmxsrv/law/lawFullContent.srv?SEQ={seq}&SEQ_HISTORY={history}",
                ),
            }
        )
    return records


def parse_official_rule_content(html: str) -> Tuple[str, str]:
    """전체보기 HTML을 검색 가능한 제목/본문으로 바꾼다."""
    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one("#contentview")
    if root is None:
        raise ValueError("official rule content container is missing")
    title_node = root.select_one(".lawname")
    title = clean_text(title_node.get_text(" ", strip=True) if title_node else "")
    lines: List[str] = []
    for node in root.select(
        ".lawname, .chapter, .addenda, .article, .hang, .ho, .mok, .none"
    ):
        line = clean_text(node.get_text(" ", strip=True))
        if line and line != "연" and (not lines or line != lines[-1]):
            lines.append(line)
    text = "\n".join(lines)
    if not title or len(text) < 100:
        raise ValueError("official rule content is unexpectedly empty")
    return title, text


@dataclass(frozen=True)
class RuleCategory:
    """공식 규정관리시스템 분류 트리의 한 노드(편/장)."""

    seq: str
    name: str
    path: Tuple[str, ...]
    is_root: bool = False

    @property
    def relative_dir(self) -> str:
        # HWP 스냅샷 경로 규칙("제2편_학칙/제1장_대학")과 맞춘다. 캠퍼스(WISE) 표지도 보존된다.
        parts = [part.strip().replace(" ", "_", 1) for part in self.path if part.strip()]
        return "/".join([*parts, OFFICIAL_RULE_RELATIVE_SUFFIX])

    @property
    def list_url(self) -> str:
        return official_rule_list_url(self.seq)


ACADEMIC_RULE_CATEGORY = RuleCategory(
    seq=ACADEMIC_RULE_CATEGORY_SEQ,
    name="제1장 대학",
    path=("제2편 학칙", "제1장 대학"),
)


class OfficialRuleCrawlError(RuntimeError):
    """수집 결과를 믿을 수 없어 기존 정본을 유지해야 하는 실패."""

    def __init__(self, message: str, report: "OfficialRuleCrawlReport | None" = None):
        super().__init__(message)
        self.report = report


class _RequestBudgetExhausted(RuntimeError):
    pass


@dataclass
class OfficialRuleCrawlReport:
    scope: str
    categories_discovered: int = 0
    categories_crawled: int = 0
    excluded_categories: List[str] = field(default_factory=list)
    failed_categories: List[Dict[str, str]] = field(default_factory=list)
    list_pages_fetched: int = 0
    detail_fetched: int = 0
    detail_reused: int = 0
    detail_failures: List[Dict[str, str]] = field(default_factory=list)
    duplicate_rules_dropped: int = 0
    duplicate_codes: List[str] = field(default_factory=list)
    shared_codes: List[str] = field(default_factory=list)
    requests_made: int = 0
    request_budget: int = 0
    budget_exhausted: bool = False
    rules_collected: int = 0

    @property
    def complete(self) -> bool:
        """모든 분류·상세를 실패 없이 읽었을 때만 True. 부재 판단(폐지 후보)의 전제다."""
        return (
            not self.failed_categories
            and not self.detail_failures
            and not self.budget_exhausted
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "scope": self.scope,
            "complete": self.complete,
            "categories_discovered": self.categories_discovered,
            "categories_crawled": self.categories_crawled,
            "excluded_categories": list(self.excluded_categories),
            "failed_categories": [dict(item) for item in self.failed_categories],
            "list_pages_fetched": self.list_pages_fetched,
            "detail_fetched": self.detail_fetched,
            "detail_reused": self.detail_reused,
            "detail_failures": [dict(item) for item in self.detail_failures],
            "duplicate_rules_dropped": self.duplicate_rules_dropped,
            "duplicate_codes": list(self.duplicate_codes),
            "shared_codes": list(self.shared_codes),
            "requests_made": self.requests_made,
            "request_budget": self.request_budget,
            "budget_exhausted": self.budget_exhausted,
            "rules_collected": self.rules_collected,
        }


def official_rule_list_url(category_seq: str, page: int = 1) -> str:
    url = (
        f"{OFFICIAL_RULE_BASE_URL}/lmxsrv/law/lawListManager.srv"
        f"?LAWGROUP=1&PAGE_MODE=&SEQ={category_seq}"
    )
    return url if page <= 1 else f"{url}&PAGE={page}"


def parse_rule_category_tree(html: str) -> List[RuleCategory]:
    """lawTree.srv의 WebFXTree 스크립트에서 분류 노드와 전체 경로를 읽는다."""
    names: Dict[str, str] = {}
    for var_id, name in _TREE_ITEM_RE.findall(html):
        names[var_id] = clean_text(name)
    actions: Dict[str, Tuple[str, str, str]] = {}
    for var_id, seq, _folder, name, root in _TREE_ACTION_RE.findall(html):
        actions[var_id] = (seq, clean_text(name), root)
    parents: Dict[str, str] = {}
    for parent_id, child_id in _TREE_ADD_RE.findall(html):
        parents.setdefault(child_id, parent_id)

    # 트리 전위 순회 순서(편 → 장)로 정렬한다. 같은 규정이 여러 분류에 걸리면 먼저 나온
    # 분류가 경로를 가진다.
    children: Dict[str, List[str]] = {}
    for child_id, parent_id in parents.items():
        children.setdefault(parent_id, []).append(child_id)
    ordered: List[str] = []
    stack = [var_id for var_id in reversed(list(names)) if var_id not in parents]
    while stack:
        current = stack.pop()
        if current in ordered:
            continue
        ordered.append(current)
        stack.extend(reversed(children.get(current, [])))
    ordered.extend(var_id for var_id in names if var_id not in ordered)

    categories: List[RuleCategory] = []
    seen: set[str] = set()
    for var_id in ordered:
        name = names.get(var_id, "")
        seq, action_name, root = actions.get(var_id, ("", name, ""))
        if not seq or seq in seen:
            continue
        seen.add(seq)
        path: List[str] = []
        cursor: Optional[str] = var_id
        visited: set[str] = set()
        while cursor is not None and cursor not in visited:
            visited.add(cursor)
            _seq, cursor_name, cursor_root = actions.get(cursor, ("", names.get(cursor, ""), ""))
            if cursor_root == "1":
                break  # 최상위 "규정" 루트는 경로에 넣지 않는다.
            path.append(cursor_name or names.get(cursor, ""))
            cursor = parents.get(cursor)
        categories.append(
            RuleCategory(
                seq=seq,
                name=action_name or name,
                path=tuple(reversed([part for part in path if part])),
                is_root=root == "1",
            )
        )
    return categories


def parse_rule_list_paging(html: str) -> Tuple[Optional[int], int]:
    """목록 하단 getPageListSet(총건수, 현재쪽, 함수, 쪽크기)을 읽는다."""
    match = _LIST_TOTAL_RE.search(html)
    if match is None:
        return None, DEFAULT_PAGE_SIZE
    total, _page, page_size = (int(value) for value in match.groups())
    return total, page_size if page_size > 0 else DEFAULT_PAGE_SIZE


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def resolve_rule_scope(scope: Optional[str] = None) -> str:
    value = (scope or os.getenv(RULE_SCOPE_ENV, "") or "all").strip().lower()
    if value not in RULE_SCOPES:
        raise ValueError(f"unknown official rule scope: {value!r} (expected {RULE_SCOPES})")
    return value


class _PoliteFetcher:
    def __init__(
        self,
        client: requests.Session,
        *,
        timeout: float,
        delay: float,
        budget: int,
        sleep: Callable[[float], None],
        report: OfficialRuleCrawlReport,
    ) -> None:
        self._client = client
        self._timeout = timeout
        self._delay = max(0.0, delay)
        self._budget = max(1, budget)
        self._sleep = sleep
        self._report = report
        self._report.request_budget = self._budget

    def get(self, url: str) -> str:
        if self._report.requests_made >= self._budget:
            self._report.budget_exhausted = True
            raise _RequestBudgetExhausted(f"request budget {self._budget} exhausted")
        if self._report.requests_made and self._delay:
            self._sleep(self._delay)
        self._report.requests_made += 1
        response = self._client.get(url, timeout=self._timeout)
        response.raise_for_status()
        return response.text


class IncompleteRuleListingError(RuntimeError):
    """목록 페이지를 끝까지 읽었다고 확인할 수 없을 때(조용한 절단 방지)."""


def list_row_keys(html: str) -> List[str]:
    """목록 한 쪽의 행(규정 + 하위 분류 폴더) 식별자. 총건수 대조에 쓴다."""
    soup = BeautifulSoup(html, "lxml")
    keys: List[str] = []
    for row in soup.select("tbody.tbody tr"):
        cell = row.select_one("td.tbody_txt")
        if cell is None:
            continue
        link = cell.find("a", href=True)
        href = str(link.get("href") if link else "")
        match = _LIST_ID_RE.search(href)
        if match:
            keys.append(f"rule:{match.group(1)}")
        elif href:
            keys.append(f"link:{href}")
        else:
            keys.append(f"text:{clean_text(cell.get_text(' ', strip=True))}")
    return keys


def _collect_category_items(
    fetcher: _PoliteFetcher,
    category: RuleCategory,
    report: OfficialRuleCrawlReport,
) -> List[Dict[str, str]]:
    """분류 목록을 끝까지 읽는다. 끝까지 읽었음을 확인하지 못하면 예외를 던진다.

    - 1쪽에 총건수(getPageListSet)가 없으면 실패
    - 마지막 쪽 이전에 새 행이 없는 쪽이 나오면 실패
    - 쪽 수 상한(MAX_PAGES_PER_CATEGORY)에 걸리면 실패
    - 읽은 행(규정 + 폴더) 수가 총건수보다 적으면 실패
    """
    items: List[Dict[str, str]] = []
    seen: set[str] = set()
    seen_rows: set[str] = set()
    html = fetcher.get(official_rule_list_url(category.seq, 1))
    report.list_pages_fetched += 1
    total, page_size = parse_rule_list_paging(html)
    if total is None:
        raise IncompleteRuleListingError("paging info missing")
    total_pages = max(1, math.ceil(total / page_size))
    if total_pages > MAX_PAGES_PER_CATEGORY:
        raise IncompleteRuleListingError(
            f"{total_pages} pages exceed cap {MAX_PAGES_PER_CATEGORY}"
        )
    page = 1
    while True:
        new_rows = [key for key in list_row_keys(html) if key not in seen_rows]
        seen_rows.update(new_rows)
        for item in parse_official_rule_list(html):
            if item["seq"] not in seen:
                seen.add(item["seq"])
                items.append(item)
        if page >= total_pages:
            break
        if not new_rows:
            raise IncompleteRuleListingError(f"page {page}/{total_pages} had no new rows")
        page += 1
        html = fetcher.get(official_rule_list_url(category.seq, page))
        report.list_pages_fetched += 1
    if len(seen_rows) < total:
        raise IncompleteRuleListingError(f"read {len(seen_rows)} of {total} rows")
    return items


def _official_rule_row(
    item: Dict[str, str],
    category: RuleCategory,
    title: str,
    text: str,
) -> Dict[str, str]:
    version = item["published_at"].replace("-", ".")
    code_prefix = f"{item['rule_code']}. " if item["rule_code"] else ""
    version_suffix = f"({version}.)" if version else ""
    return {
        "relative_dir": category.relative_dir,
        "filename": f"{code_prefix}{title}{version_suffix}.html",
        "title": f"{code_prefix}{title}".strip(),
        "text": text,
        "source_type": "official_rule_web",
        "source_url": item["source_url"],
        "source_page_url": category.list_url,
        "source_version": f"{item['seq']}:{item['seq_history']}",
        "source_file": "official_rule_web",
        "published_at": item["published_at"],
        "rule_code": item["rule_code"],
    }


def collect_official_rules(
    *,
    scope: Optional[str] = None,
    timeout: float = 20.0,
    session: requests.Session | None = None,
    request_delay: Optional[float] = None,
    max_requests: Optional[int] = None,
    max_failed_ratio: Optional[float] = None,
    known_versions: Optional[Mapping[str, Mapping[str, str]]] = None,
    excluded_category_names: Tuple[str, ...] = DEFAULT_EXCLUDED_CATEGORY_NAMES,
    sleep: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """공식 규정관리시스템 현행 규정을 분류 전체(기본) 또는 SEQ=6(academic)에서 수집한다.

    - ``scope="all"``: 분류 트리를 읽어 모든 편/장 목록을 페이지 끝까지 순회한다.
    - ``scope="academic"``: 이전 동작(제2편 제1장 대학, SEQ=6)만 수집한다.
    - ``known_versions``: ``source_version``(SEQ:연혁)별 기존 행. 같은 연혁이면 상세를
      다시 받지 않고 재사용해 요청 수를 줄인다.

    트리 발견 실패, 실패 분류 비율 초과, 상세 실패 비율 초과, 수집 0건이면
    ``OfficialRuleCrawlError``를 던진다. 호출부는 이때 기존 정본을 그대로 둔다.
    결과 DataFrame의 ``attrs["crawl_report"]``에 수집 보고서(dict)를 붙인다.
    """
    resolved_scope = resolve_rule_scope(scope)
    delay = (
        _env_float(RULE_REQUEST_DELAY_ENV, DEFAULT_REQUEST_DELAY_SECONDS)
        if request_delay is None
        else request_delay
    )
    budget = _env_int(RULE_MAX_REQUESTS_ENV, DEFAULT_MAX_REQUESTS) if max_requests is None else max_requests
    failed_ratio_limit = (
        _env_float(RULE_MAX_FAILED_RATIO_ENV, DEFAULT_MAX_FAILED_RATIO)
        if max_failed_ratio is None
        else max_failed_ratio
    )
    report = OfficialRuleCrawlReport(scope=resolved_scope)
    fetcher = _PoliteFetcher(
        session or requests.Session(),
        timeout=timeout,
        delay=delay,
        budget=budget,
        sleep=sleep,
        report=report,
    )

    # 1) 분류 발견
    if resolved_scope == "academic":
        categories = [ACADEMIC_RULE_CATEGORY]
        report.categories_discovered = 1
    else:
        try:
            tree_html = fetcher.get(RULE_CATEGORY_TREE_URL)
            discovered = parse_rule_category_tree(tree_html)
        except Exception as exc:  # noqa: BLE001 - 어떤 실패든 기존 정본 유지
            raise OfficialRuleCrawlError(
                f"official rule category discovery failed: {exc}", report
            ) from exc
        report.categories_discovered = len(discovered)
        categories = []
        for category in discovered:
            if category.is_root:
                continue
            if category.name in excluded_category_names or any(
                part in excluded_category_names for part in category.path
            ):
                report.excluded_categories.append(category.name)
                continue
            categories.append(category)
        if not categories:
            raise OfficialRuleCrawlError(
                "official rule category discovery returned no categories", report
            )

    # 2) 분류별 목록(페이지 끝까지). SEQ(규정 id) 기준 중복 제거.
    listed: List[Tuple[Dict[str, str], RuleCategory]] = []
    seen_rule_seqs: Dict[str, int] = {}
    for category in categories:
        if report.budget_exhausted:
            # 예산 초과로 방문하지 못한 분류도 실패로 집계한다.
            report.failed_categories.append(
                {"seq": category.seq, "name": "/".join(category.path) or category.name,
                 "error": "RequestBudgetExhausted"}
            )
            continue
        try:
            items = _collect_category_items(fetcher, category, report)
        except Exception as exc:  # noqa: BLE001 - 분류 단위 실패는 따로 집계
            report.failed_categories.append(
                {"seq": category.seq, "name": "/".join(category.path) or category.name,
                 "error": type(exc).__name__}
            )
            continue
        report.categories_crawled += 1
        for item in items:
            index = seen_rule_seqs.get(item["seq"])
            if index is not None:
                report.duplicate_rules_dropped += 1
                # 편(상위)과 장(하위) 목록에 모두 걸리면 더 구체적인(깊은) 경로를 쓴다.
                if len(category.path) > len(listed[index][1].path):
                    listed[index] = (item, category)
                continue
            seen_rule_seqs[item["seq"]] = len(listed)
            listed.append((item, category))
    failed_ratio = len(report.failed_categories) / max(1, len(categories))
    if failed_ratio > failed_ratio_limit or report.categories_crawled == 0:
        raise OfficialRuleCrawlError(
            f"official rule listing failed for {len(report.failed_categories)}/"
            f"{len(categories)} categories", report
        )

    # 같은 규정번호·같은 (정규화) 제목이 서로 다른 SEQ로 나오면 최신 개정일 하나만 남긴다.
    # 번호만 같고 제목이 다르면(예: 서울·WISE 캠퍼스 규정이 같은 번호) 다른 규정이므로 둘 다 둔다.
    by_identity: Dict[Tuple[str, str], int] = {}
    deduped: List[Tuple[Dict[str, str], RuleCategory]] = []
    codes_seen: Dict[str, set[str]] = {}
    for item, category in listed:
        code = item["rule_code"]
        identity = (code, canonical_rule_title(item["title"]))
        if code:
            codes_seen.setdefault(code, set()).add(identity[1])
        if code and identity in by_identity:
            index = by_identity[identity]
            report.duplicate_rules_dropped += 1
            if code not in report.duplicate_codes:
                report.duplicate_codes.append(code)
            if item["published_at"] > deduped[index][0]["published_at"]:
                deduped[index] = (item, category)
            continue
        if code:
            by_identity[identity] = len(deduped)
        deduped.append((item, category))
    report.shared_codes = sorted(code for code, titles in codes_seen.items() if len(titles) > 1)

    # 3) 상세(현행 전문). 같은 연혁은 기존 본문 재사용.
    rows: List[Dict[str, str]] = []
    known = known_versions or {}
    for item, category in deduped:
        version_key = f"{item['seq']}:{item['seq_history']}"
        previous = known.get(version_key)
        if previous is not None and len(str(previous.get("text", "") or "")) >= 100:
            previous_title = str(previous.get("title", "") or "")
            code_prefix = f"{item['rule_code']}. " if item["rule_code"] else ""
            bare_title = (
                previous_title[len(code_prefix):]
                if code_prefix and previous_title.startswith(code_prefix)
                else previous_title
            ) or item["title"]
            rows.append(_official_rule_row(item, category, bare_title, str(previous["text"])))
            report.detail_reused += 1
            continue
        try:
            html = fetcher.get(item["source_url"])
            parsed_title, text = parse_official_rule_content(html)
        except Exception as exc:  # noqa: BLE001 - 규정 단위 실패는 따로 집계
            report.detail_failures.append(
                {"rule_code": item["rule_code"], "title": item["title"],
                 "seq": item["seq"], "error": type(exc).__name__}
            )
            continue
        report.detail_fetched += 1
        rows.append(_official_rule_row(item, category, parsed_title or item["title"], text))

    if deduped and len(report.detail_failures) / len(deduped) > failed_ratio_limit:
        raise OfficialRuleCrawlError(
            f"official rule detail failed for {len(report.detail_failures)}/{len(deduped)} rules",
            report,
        )
    if not rows:
        raise OfficialRuleCrawlError("official rule crawl produced no rules", report)
    report.rules_collected = len(rows)
    frame = pd.DataFrame(rows)
    frame.attrs["crawl_report"] = report.to_dict()
    return frame


def collect_official_academic_rules(
    *,
    timeout: float = 20.0,
    session: requests.Session | None = None,
    **kwargs,
) -> pd.DataFrame:
    """제2편 제1장(대학, SEQ=6)의 공식 현행 규정만 수집한다(이전 정기 수집 범위)."""
    return collect_official_rules(scope="academic", timeout=timeout, session=session, **kwargs)


def merge_official_rule_versions(
    existing: pd.DataFrame,
    official: pd.DataFrame,
) -> pd.DataFrame:
    """같은 공식 연혁은 갱신하고 과거판/HWP 정본은 보존한다."""
    if official.empty:
        raise ValueError("refusing to replace rules with an empty official crawl")
    merged = pd.concat([existing, official], ignore_index=True, sort=False).fillna("")
    official_mask = merged.get("source_type", pd.Series("", index=merged.index)).eq(
        "official_rule_web"
    )
    official_rows = merged.loc[official_mask].drop_duplicates(
        subset=["source_type", "source_version"], keep="last"
    )
    legacy_rows = merged.loc[~official_mask]
    return pd.concat([legacy_rows, official_rows], ignore_index=True, sort=False).fillna("")


def main() -> None:
    hwp_paths = list_hwp_files(RULE_ROOT)
    print(f"총 {len(hwp_paths)}개의 HWP 파일 발견")

    records: List[Dict[str, object]] = []
    failure_details: List[Dict[str, object]] = []

    for idx, path in enumerate(hwp_paths, start=1):
        method, text, failures = extract_text_from_hwp(path)
        rel_dir, filename = summarise_relative_path(path, RULE_ROOT)

        cleaned = clean_text(text) if text else ""

        # 추출 실패(빈 텍스트) 레코드는 CSV에 포함하지 않는다 —
        # 빈 청크가 Chroma에 upsert되어 검색 품질을 해치는 것을 방지.
        if not cleaned:
            failures.append("empty_text_skipped")
            print(f"⚠️ 텍스트 추출 실패로 건너뜀: {rel_dir}/{filename}")
        else:
            records.append(
                {
                    "relative_dir": rel_dir,
                    "filename": filename,
                    "absolute_path": str(path.resolve()),
                    "method": method,
                    "text": cleaned,
                }
            )

        if failures:
            failure_details.append(
                {
                    "path": str(path),
                    "method": method,
                    "issues": ";".join(failures),
                }
            )

        if idx % 25 == 0:
            print(f"처리 진행률: {idx}/{len(hwp_paths)}")

    rule_df = pd.DataFrame(records)
    if not rule_df.empty:
        rule_df.drop(columns=["absolute_path", "method"], inplace=True, errors="ignore")
    rule_df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {OUTPUT_PATH.resolve()}")

    if failure_details:
        print("⚠️ 추출 실패 항목 요약 (최대 5건)")
        for item in failure_details[:5]:
            print(item)


if __name__ == "__main__":
    main()
