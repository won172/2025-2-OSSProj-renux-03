"""동국대학교 서울캠퍼스 학부 교과과정 소스 인덱스를 수집합니다.

전략:
1. 대표 대학소개/교내홈페이지 검색 페이지에서 학과 홈페이지 URL 수집
2. 각 학과 홈페이지에서 교과과정 관련 링크 탐색
3. 못 찾은 학과는 수동 보정 CSV 또는 학교 공식 교육과정 허브로 fallback
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
import csv
import re

import pandas as pd
import requests
from bs4 import BeautifulSoup, FeatureNotFound, Tag
from urllib.parse import urljoin, urlparse

from src.config import DATA_DIR

CATALOG_SOURCE_URLS = (
    "https://www.dongguk.edu/page/853",
    "https://www.dongguk.edu/campus/list",
)
OFFICIAL_CURRICULUM_HUB_URL = "https://www.dongguk.edu/page/137"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DonggukDepartmentCatalogCrawler/0.2)",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}
PARSER_CANDIDATES = ("lxml", "html5lib", "html.parser")
CATALOG_OUTPUT_PATH = DATA_DIR / "dongguk_departments_catalog.csv"
CURRICULUM_OUTPUT_PATH = DATA_DIR / "dongguk_department_curriculum_sources.csv"
HOMEPAGE_SEED_PATH = DATA_DIR / "dongguk_department_homepages.csv"
HOMEPAGE_SEED_EXAMPLE_PATH = DATA_DIR / "dongguk_department_homepages.example.csv"

CURRICULUM_KEYWORDS = (
    "교과과정",
    "교육과정",
    "전공교육과정",
    "전공과목",
    "개설총괄표",
    "전공과목 개설 총괄표",
    "이수체계도",
    "학부과정",
    "교과목 해설",
    "curriculum",
    "course",
    "courses",
)
DEPARTMENT_KEYWORDS = (
    "학과",
    "전공",
    "학부",
    "department",
    "major",
    "school",
)
COLLEGE_KEYWORDS = (
    "대학",
    "college",
    "school",
    "faculty",
)
EXCLUDED_DEPARTMENT_TERMS = (
    "대학원",
    "교육원",
    "연구소",
    "센터",
    "사업단",
    "행정부서",
    "입학처",
    "도서관",
    "병원",
)
EXCLUDED_DEPARTMENT_EXACT = (
    "# 학과소개",
    "학과소개",
    "deis(학부모포탈)",
)
DEPARTMENT_NAME_CLEANUPS = (
    "홈페이지 바로가기",
    "홈페이지",
    "바로가기",
)
EXCLUDED_HOMEPAGE_HOSTS = {
    "www.dongguk.edu",
    "dongguk.edu",
    "support.dongguk.edu",
    "search.dongguk.edu",
}
EXCLUDED_CURRICULUM_URL_SUBSTRINGS = (
    "/article/",
    "/notice",
    "/reference/detail/",
    "search.do",
)
EXCLUDED_CURRICULUM_TEXT_SUBSTRINGS = (
    "신청서",
    "학점포기",
    "변경 신청",
    "포기 안내",
    "수강신청",
    "정정",
    "휴학",
    "복학",
    "졸업",
    "공지",
)


@dataclass(frozen=True)
class DepartmentCatalogRow:
    college_name: str
    department_name: str
    department_key: str
    department_url: str
    source_url: str
    source_type: str
    collected_at: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fetch_page_html(url: str, *, timeout: float = 15.0) -> str:
    response = requests.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    return response.text


def make_soup(markup: str) -> BeautifulSoup:
    last_exc: Exception | None = None
    for parser in PARSER_CANDIDATES:
        try:
            return BeautifulSoup(markup, parser)
        except FeatureNotFound as exc:
            last_exc = exc
            continue
    raise RuntimeError("사용 가능한 HTML 파서를 찾을 수 없습니다.") from last_exc


def slugify_department_name(name: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "-", name.strip().lower()).strip("-")
    return slug or "unknown-department"


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def looks_like_department_name(text: str) -> bool:
    normalized = normalize_text(text)
    if len(normalized) < 2:
        return False
    if normalized.lower() in EXCLUDED_DEPARTMENT_EXACT:
        return False
    if normalized.startswith("#"):
        return False
    if any(term in normalized for term in EXCLUDED_DEPARTMENT_TERMS):
        return False
    lowered = normalized.lower()
    return any(keyword in normalized for keyword in ("학과", "전공", "학부")) or any(
        keyword in lowered for keyword in ("department", "major")
    )


def looks_like_college_name(text: str) -> bool:
    normalized = normalize_text(text)
    if len(normalized) < 2:
        return False
    lowered = normalized.lower()
    return any(keyword in normalized for keyword in ("대학", "학부")) or any(keyword in lowered for keyword in COLLEGE_KEYWORDS)


def clean_department_name(text: str) -> str:
    normalized = normalize_text(text)
    for token in DEPARTMENT_NAME_CLEANUPS:
        normalized = normalized.replace(token, "")
    return normalize_text(normalized)


def clean_college_name(text: str) -> str:
    normalized = normalize_text(text)
    for token in DEPARTMENT_NAME_CLEANUPS:
        normalized = normalized.replace(token, "")
    return normalize_text(normalized)


def is_department_homepage_url(candidate: str) -> bool:
    try:
        parsed = urlparse(candidate)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    return host.endswith(".dongguk.edu") and host not in EXCLUDED_HOMEPAGE_HOSTS


def extract_nearby_label(anchor: Tag) -> str:
    text = clean_department_name(anchor.get_text(" ", strip=True))
    if looks_like_department_name(text):
        return text

    parent = anchor.parent
    if parent:
        parent_text = clean_department_name(parent.get_text(" ", strip=True))
        if looks_like_department_name(parent_text):
            return parent_text

    for sibling in list(anchor.previous_siblings)[:6]:
        if isinstance(sibling, Tag):
            sibling_text = clean_department_name(sibling.get_text(" ", strip=True))
        else:
            sibling_text = clean_department_name(str(sibling))
        if looks_like_department_name(sibling_text):
            return sibling_text

    heading = anchor.find_previous(["h1", "h2", "h3", "h4", "h5", "strong", "dt"])
    if heading:
        heading_text = clean_department_name(heading.get_text(" ", strip=True))
        if looks_like_department_name(heading_text):
            return heading_text

    return text


def extract_nearby_college(anchor: Tag) -> str:
    for node in anchor.parents:
        if not isinstance(node, Tag):
            continue
        for heading in node.find_all(["h1", "h2", "h3", "h4", "h5", "strong"], recursive=False):
            text = normalize_text(heading.get_text(" ", strip=True))
            if looks_like_college_name(text):
                return clean_college_name(text)

    for previous in anchor.find_all_previous(["h1", "h2", "h3", "h4", "h5", "strong"]):
        text = normalize_text(previous.get_text(" ", strip=True))
        if looks_like_college_name(text):
            return clean_college_name(text)
    return ""


def is_valid_department_row(department_name: str, department_url: str, college_name: str) -> bool:
    normalized_department = normalize_text(department_name)
    normalized_college = normalize_text(college_name)
    if not looks_like_department_name(normalized_department):
        return False
    if any(term in normalized_college for term in EXCLUDED_DEPARTMENT_TERMS):
        return False
    parsed = urlparse(department_url)
    host = (parsed.hostname or "").lower()
    if host in EXCLUDED_HOMEPAGE_HOSTS:
        return False
    if "search.do" in department_url:
        return False
    return True


def extract_department_links_from_page(url: str, html: str) -> list[DepartmentCatalogRow]:
    soup = make_soup(html)
    collected_at = utc_now_iso()
    rows: list[DepartmentCatalogRow] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href:
            continue
        full_url = urljoin(url, href)
        if not is_department_homepage_url(full_url) or full_url in seen:
            continue

        label = extract_nearby_label(anchor)
        if not looks_like_department_name(label):
            continue

        college_name = extract_nearby_college(anchor)
        if not is_valid_department_row(label, full_url, college_name):
            continue
        rows.append(
            DepartmentCatalogRow(
                college_name=college_name,
                department_name=label,
                department_key=slugify_department_name(label),
                department_url=full_url,
                source_url=url,
                source_type="department_homepage",
                collected_at=collected_at,
            )
        )
        seen.add(full_url)

    return rows


def dedupe_catalog_rows(rows: Iterable[DepartmentCatalogRow]) -> list[DepartmentCatalogRow]:
    deduped: dict[str, DepartmentCatalogRow] = {}
    for row in rows:
        key = row.department_url or row.department_name
        if key not in deduped:
            deduped[key] = row
            continue

        existing = deduped[key]
        if existing.college_name:
            continue
        deduped[key] = row
    return list(deduped.values())


def load_manual_homepage_map(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}

    df = pd.read_csv(path).fillna("").astype(str)
    result: dict[str, dict[str, str]] = {}
    for _, row in df.iterrows():
        department_name = normalize_text(row.get("department_name", ""))
        department_url = row.get("department_url", "").strip() or row.get("homepage_url", "").strip()
        college_name = normalize_text(row.get("college_name", ""))
        if not department_name or not department_url:
            continue
        result[department_name] = {
            "department_url": department_url,
            "college_name": college_name,
        }
    return result


def merge_homepages(rows: list[DepartmentCatalogRow], homepage_map: dict[str, dict[str, str]]) -> list[DepartmentCatalogRow]:
    merged = {row.department_name: row for row in rows}

    for department_name, info in homepage_map.items():
        existing = merged.get(department_name)
        if existing:
            merged[department_name] = DepartmentCatalogRow(
                college_name=info["college_name"] or existing.college_name,
                department_name=existing.department_name,
                department_key=existing.department_key,
                department_url=info["department_url"] or existing.department_url,
                source_url=existing.source_url,
                source_type="manual_homepage" if info["department_url"] else existing.source_type,
                collected_at=existing.collected_at,
            )
            continue

        merged[department_name] = DepartmentCatalogRow(
            college_name=info["college_name"],
            department_name=department_name,
            department_key=slugify_department_name(department_name),
            department_url=info["department_url"],
            source_url=HOMEPAGE_SEED_PATH.as_posix(),
            source_type="manual_homepage",
            collected_at=utc_now_iso(),
        )

    filtered = [
        row
        for row in merged.values()
        if is_valid_department_row(row.department_name, row.department_url, row.college_name)
    ]
    return sorted(filtered, key=lambda row: (row.college_name, row.department_name))


def _is_internal_url(base_url: str, candidate: str) -> bool:
    try:
        base_host = urlparse(base_url).hostname or ""
        cand_host = urlparse(candidate).hostname or ""
        return bool(cand_host) and (cand_host == base_host or cand_host.endswith(".dongguk.edu"))
    except Exception:
        return False


def _is_same_page_or_root(homepage_url: str, candidate: str) -> bool:
    base = urlparse(homepage_url)
    target = urlparse(candidate)
    return (
        (base.scheme, base.netloc, base.path.rstrip("/"))
        == (target.scheme, target.netloc, target.path.rstrip("/"))
        and (not target.query)
    )


def infer_source_type(anchor_text: str, url: str) -> str:
    text = f"{anchor_text} {url}".lower()
    if "pdf" in text:
        return "official_pdf" if "dongguk.edu/page/137" in url else "pdf_curriculum"
    if "/article/" in url or "/reference/detail/" in url:
        return "reference_article"
    if "해설" in anchor_text or "description" in text:
        return "course_description"
    if "총괄표" in anchor_text or "이수체계도" in anchor_text or "개설" in anchor_text:
        return "curriculum_table"
    return "curriculum_page"


def is_usable_curriculum_candidate(homepage_url: str, anchor_text: str, full_url: str) -> bool:
    haystack = f"{anchor_text} {full_url}".lower()
    if any(token in haystack for token in (s.lower() for s in EXCLUDED_CURRICULUM_TEXT_SUBSTRINGS)):
        return False
    if any(token in full_url.lower() for token in EXCLUDED_CURRICULUM_URL_SUBSTRINGS):
        return False
    if _is_same_page_or_root(homepage_url, full_url):
        strong_tokens = ("총괄표", "이수체계도", "해설", "전공과목", "학부과정")
        if not any(token in anchor_text for token in strong_tokens):
            return False
    return True


def curriculum_candidate_score(homepage_url: str, anchor_text: str, full_url: str) -> tuple[int, int, int]:
    score = 0
    if not _is_same_page_or_root(homepage_url, full_url):
        score += 3
    if "총괄표" in anchor_text or "이수체계도" in anchor_text:
        score += 3
    if "해설" in anchor_text:
        score += 2
    if "학부과정" in anchor_text or "전공과목" in anchor_text:
        score += 1
    path_len = len(urlparse(full_url).path or "")
    return (score, path_len, -len(anchor_text))


def discover_curriculum_sources(homepage_url: str) -> list[dict[str, str]]:
    html = fetch_page_html(homepage_url)
    soup = make_soup(html)
    matches: list[dict[str, str]] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        text = normalize_text(anchor.get_text(" ", strip=True))
        href = (anchor.get("href") or "").strip()
        if not text or not href:
            continue

        full_url = urljoin(homepage_url, href)
        if full_url in seen or not _is_internal_url(homepage_url, full_url):
            continue

        haystack = f"{text} {full_url}".lower()
        if not any(keyword.lower() in haystack for keyword in CURRICULUM_KEYWORDS):
            continue
        if not is_usable_curriculum_candidate(homepage_url, text, full_url):
            continue

        matches.append(
            {
                "curriculum_title": text,
                "curriculum_url": full_url,
                "source_type": infer_source_type(text, full_url),
                "status": "found",
            }
        )
        seen.add(full_url)

    matches.sort(
        key=lambda item: curriculum_candidate_score(
            homepage_url,
            item["curriculum_title"],
            item["curriculum_url"],
        ),
        reverse=True,
    )
    return matches


def write_example_homepage_seed(path: Path) -> None:
    if path.exists():
        return
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=["college_name", "department_name", "department_url"])
        writer.writeheader()


def write_catalog(rows: Iterable[DepartmentCatalogRow], path: Path) -> pd.DataFrame:
    df = pd.DataFrame([asdict(row) for row in rows])
    if not df.empty:
        df.sort_values(["college_name", "department_name"], inplace=True, na_position="last")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return df


def write_curriculum_sources(catalog_rows: Iterable[DepartmentCatalogRow], path: Path) -> pd.DataFrame:
    records: list[dict[str, str]] = []
    collected_at = utc_now_iso()

    for row in catalog_rows:
        try:
            discovered = discover_curriculum_sources(row.department_url)
        except Exception as exc:  # noqa: BLE001
            records.append(
                {
                    "college_name": row.college_name,
                    "department_name": row.department_name,
                    "department_key": row.department_key,
                    "department_url": row.department_url,
                    "curriculum_title": "",
                    "curriculum_url": "",
                    "source_type": row.source_type,
                    "status": "discovery_failed",
                    "collected_at": collected_at,
                    "error": str(exc),
                    "fallback_url": OFFICIAL_CURRICULUM_HUB_URL,
                }
            )
            continue

        if not discovered:
            records.append(
                {
                    "college_name": row.college_name,
                    "department_name": row.department_name,
                    "department_key": row.department_key,
                    "department_url": row.department_url,
                    "curriculum_title": "",
                    "curriculum_url": "",
                    "source_type": row.source_type,
                    "status": "not_found",
                    "collected_at": collected_at,
                    "error": "",
                    "fallback_url": OFFICIAL_CURRICULUM_HUB_URL,
                }
            )
            continue

        for source in discovered:
            records.append(
                {
                    "college_name": row.college_name,
                    "department_name": row.department_name,
                    "department_key": row.department_key,
                    "department_url": row.department_url,
                    "curriculum_title": source["curriculum_title"],
                    "curriculum_url": source["curriculum_url"],
                    "source_type": source["source_type"],
                    "status": source["status"],
                    "collected_at": collected_at,
                    "error": "",
                    "fallback_url": "",
                }
            )

    df = pd.DataFrame(records)
    if not df.empty:
        df.sort_values(["college_name", "department_name", "status", "curriculum_title"], inplace=True, na_position="last")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return df


def crawl_department_catalog() -> list[DepartmentCatalogRow]:
    rows: list[DepartmentCatalogRow] = []
    for url in CATALOG_SOURCE_URLS:
        html = fetch_page_html(url)
        rows.extend(extract_department_links_from_page(url, html))
    return dedupe_catalog_rows(rows)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    write_example_homepage_seed(HOMEPAGE_SEED_EXAMPLE_PATH)

    crawled_rows = crawl_department_catalog()
    homepage_map = load_manual_homepage_map(HOMEPAGE_SEED_PATH)
    merged_rows = merge_homepages(crawled_rows, homepage_map)

    catalog_df = write_catalog(merged_rows, CATALOG_OUTPUT_PATH)
    sources_df = write_curriculum_sources(merged_rows, CURRICULUM_OUTPUT_PATH)

    print(f"저장 완료: {CATALOG_OUTPUT_PATH}")
    print(f"학과 수: {len(catalog_df)}")
    print(f"저장 완료: {CURRICULUM_OUTPUT_PATH}")
    print(f"교과과정 소스 수: {len(sources_df)}")


if __name__ == "__main__":
    main()
