"""동국대학교 학과별 교과과정 소스에서 RAG용 통합 CSV를 생성합니다.

입력:
- dongguk_department_curriculum_sources.csv

출력:
- dongguk_courses_all.csv

전략:
1. curriculum source CSV에서 status=found인 URL을 읽는다.
2. 페이지에서 교과 관련 표를 우선 추출해 행 단위 레코드로 정규화한다.
3. 유의미한 표가 없으면 교과과정 섹션 본문을 텍스트 레코드로 저장한다.
4. 최종적으로 전 학과 공통 ingest가 읽을 수 있는 CSV를 생성한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
import re

import pandas as pd
import requests
from bs4 import BeautifulSoup, FeatureNotFound, Tag

from src.config import DATA_SOURCES

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DonggukCurriculumContentCrawler/0.1)",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}
PARSER_CANDIDATES = ("lxml", "html5lib", "html.parser")
SOURCE_CSV_PATH = DATA_SOURCES["courses_curriculum_sources"]
OUTPUT_CSV_PATH = DATA_SOURCES["courses_all"]
CURRICULUM_LINKS_XLSX_GLOB = "dongguk_department_curriculum_links_*.xlsx"
CURATED_SHEET_NAME = "curriculum_links"
CURRICULUM_PAGE_KEYWORDS = (
    "교과과정",
    "교육과정",
    "전공교육과정",
    "전공과목",
    "개설총괄표",
    "이수체계도",
    "학부과정",
    "교과목 해설",
)
COURSE_HEADER_MAP = {
    "course_code": ("학수번호", "과목코드", "학수", "code", "course code", "subject code"),
    "title": ("교과목명", "과목명", "국문교과목명", "교과목", "교과명", "title", "course"),
    "english_title": ("영문명", "english", "영문교과목명"),
    "credit": ("학점", "credit"),
    "semester": ("개설학기", "학기", "semester"),
    "grade": ("학년", "이수대상", "대상", "수강대상"),
    "course_type": ("전공구분", "이수구분", "구분", "이수", "category", "type"),
    "description": ("해설", "설명", "비고", "교과목 해설", "내용", "description"),
}
IGNORED_TEXT_PATTERNS = (
    "개인정보처리방침",
    "이메일무단수집거부",
    "찾아오시는 길",
    "사이트맵",
)
EXCLUDED_SOURCE_TITLE_TERMS = (
    "대학원",
)
BOILERPLATE_TEXT_SNIPPETS = (
    "등록된 팝업이 없습니다",
    "portal",
    "ndrims",
    "e-class",
    "groupwawre",
    "groupware",
    "webmail",
    "중앙도서관",
    "인쇄 공유 페이스북 공유하기 트위터 공유하기",
)
CONTENT_SIGNAL_TERMS = (
    "학점",
    "학수번호",
    "교과목명",
    "과목명",
    "이수구분",
    "전공구분",
    "이수대상",
    "개설학기",
)
HEADING_RECORD_TAGS = ("h3", "h4")


@dataclass(frozen=True)
class CurriculumSource:
    college_name: str
    department_name: str
    department_key: str
    department_url: str
    curriculum_title: str
    curriculum_url: str
    source_type: str


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


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


def find_curated_curriculum_workbook() -> Optional[Path]:
    candidates = sorted(SOURCE_CSV_PATH.parent.glob(CURRICULUM_LINKS_XLSX_GLOB))
    if not candidates:
        return None
    return candidates[-1]


def load_curriculum_sources_from_workbook(path: Path) -> list[CurriculumSource]:
    df = pd.read_excel(path, sheet_name=CURATED_SHEET_NAME).fillna("").astype(str)
    rows: list[CurriculumSource] = []
    seen: set[tuple[str, str, str]] = set()

    for _, row in df.iterrows():
        status = normalize_text(row.get("상태", ""))
        if status not in {"found_department_page", "found_college_page"}:
            continue

        curriculum_url = normalize_text(row.get("교과과정_URL", ""))
        department_name = normalize_text(row.get("학과/전공", ""))
        department_url = normalize_text(row.get("학과홈페이지", ""))
        curriculum_title = normalize_text(row.get("페이지명/메뉴", ""))
        college_name = normalize_text(row.get("단과대학", ""))
        if not curriculum_url or not department_name:
            continue
        if any(term in curriculum_title for term in EXCLUDED_SOURCE_TITLE_TERMS):
            continue

        source_type = "curriculum_page"
        if "총괄표" in curriculum_title or "이수체계" in curriculum_title or "개설교과목" in curriculum_title:
            source_type = "curriculum_table"
        elif "해설" in curriculum_title:
            source_type = "course_description"

        key = (department_name, curriculum_url, source_type)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            CurriculumSource(
                college_name=college_name,
                department_name=department_name,
                department_key=department_name,
                department_url=department_url,
                curriculum_title=curriculum_title,
                curriculum_url=curriculum_url,
                source_type=source_type,
            )
        )

    return rows


def load_curriculum_sources(path: Path) -> list[CurriculumSource]:
    if not path.exists():
        raise FileNotFoundError(f"Curriculum source CSV not found: {path}")

    df = pd.read_csv(path).fillna("").astype(str)
    rows: list[CurriculumSource] = []
    seen: set[tuple[str, str, str]] = set()
    for _, row in df.iterrows():
        if row.get("status", "").strip() != "found":
            continue
        curriculum_url = row.get("curriculum_url", "").strip()
        department_name = row.get("department_name", "").strip()
        curriculum_title = row.get("curriculum_title", "").strip()
        if not curriculum_url or not department_name:
            continue
        if any(term in curriculum_title for term in EXCLUDED_SOURCE_TITLE_TERMS):
            continue
        key = (department_name, curriculum_url, row.get("source_type", "").strip())
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            CurriculumSource(
                college_name=row.get("college_name", "").strip(),
                department_name=department_name,
                department_key=row.get("department_key", "").strip(),
                department_url=row.get("department_url", "").strip(),
                curriculum_title=curriculum_title,
                curriculum_url=curriculum_url,
                source_type=row.get("source_type", "").strip() or "curriculum_page",
            )
        )
    workbook = find_curated_curriculum_workbook()
    if workbook is None:
        return rows

    curated_rows = load_curriculum_sources_from_workbook(workbook)
    covered_departments = {row.department_name for row in curated_rows}
    supplemented_rows = curated_rows + [row for row in rows if row.department_name not in covered_departments]
    return supplemented_rows


def read_table_to_df(table: Tag) -> pd.DataFrame:
    headers: Optional[list[str]] = None
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        cell_texts = [normalize_text(cell.get_text(" ", strip=True)) for cell in cells]
        has_header = any(cell.name == "th" for cell in cells)
        if headers is None and has_header:
            headers = cell_texts
            continue
        rows.append(cell_texts)

    if headers is None and rows:
        max_len = max(len(row) for row in rows)
        headers = [f"col_{idx + 1}" for idx in range(max_len)]
    elif headers is None:
        headers = []

    max_len = len(headers) if headers else max((len(row) for row in rows), default=0)
    normalized_rows: list[list[str]] = []
    for row in rows:
        if len(row) < max_len:
            row = row + ["" for _ in range(max_len - len(row))]
        elif len(row) > max_len:
            row = row[:max_len]
        normalized_rows.append(row)

    df = pd.DataFrame(normalized_rows, columns=headers)
    df.columns = [normalize_text(col) for col in df.columns]
    return df


def table_relevance_score(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    header_text = " ".join(df.columns.tolist()).lower()
    body_text = " ".join(df.astype(str).head(5).fillna("").stack().tolist()).lower()
    score = 0
    for tokens in COURSE_HEADER_MAP.values():
        for token in tokens:
            token_lower = token.lower()
            if token_lower in header_text:
                score += 3
            elif token_lower in body_text:
                score += 1
    return score


def find_section_title(table: Tag) -> str:
    heading = table.find_previous(["h1", "h2", "h3", "h4", "strong", "dt"])
    if not heading:
        return ""
    return normalize_text(heading.get_text(" ", strip=True))


def canonical_field_name(column: str) -> str:
    normalized = normalize_text(column).lower()
    for canonical, variants in COURSE_HEADER_MAP.items():
        if any(token.lower() in normalized for token in variants):
            return canonical
    return column


def normalize_semester(value: str) -> str:
    normalized = normalize_text(value)
    if normalized in {"1", "2"}:
        return f"{normalized}학기"
    return normalized


def choose_record_title(row: dict[str, str]) -> str:
    for key in ("title", "course_name", "교과목명", "국문교과목명", "과목명"):
        value = normalize_text(row.get(key, ""))
        if value:
            return value
    for value in row.values():
        value_str = normalize_text(value)
        if value_str:
            return value_str[:80]
    return "교과과정 정보"


def build_table_records(df: pd.DataFrame, source: CurriculumSource, section_title: str) -> list[dict[str, str]]:
    if df.empty:
        return []

    renamed = {col: canonical_field_name(col) for col in df.columns}
    working = df.rename(columns=renamed).copy()
    working = working.map(normalize_text)  # pandas 3.0: applymap 제거됨 → DataFrame.map
    working = working.loc[:, ~(working.eq("").all())]
    if working.empty:
        return []

    records: list[dict[str, str]] = []
    for _, row in working.iterrows():
        row_dict = {str(col): normalize_text(val) for col, val in row.items()}
        row_values = [value for value in row_dict.values() if value]
        if not row_values:
            continue

        title = choose_record_title(row_dict)
        course_code = row_dict.get("course_code", "")
        description = row_dict.get("description", "")
        raw_text = "\n".join(f"{key}: {value}" for key, value in row_dict.items() if value)
        if not raw_text:
            continue

        records.append(
            {
                "college_name": source.college_name,
                "department_name": source.department_name,
                "department_url": source.department_url,
                "curriculum_title": source.curriculum_title,
                "curriculum_url": source.curriculum_url,
                "source_type": source.source_type,
                "section_title": section_title or source.curriculum_title,
                "record_type": "table_row",
                "course_code": course_code,
                "title": title,
                "course_name": title,
                "description": description,
                "credit": row_dict.get("credit", ""),
                "semester": normalize_semester(row_dict.get("semester", "")),
                "grade": row_dict.get("grade", ""),
                "course_type": row_dict.get("course_type", ""),
                "english_title": row_dict.get("english_title", ""),
                "raw_text": raw_text,
            }
        )
    return records


def find_content_root(soup: BeautifulSoup) -> Tag:
    for selector in [
        "#jwxe_main_content",
        ".fr-view",
        ".cont",
        ".content",
        "#content",
        "main",
    ]:
        node = soup.select_one(selector)
        if isinstance(node, Tag):
            return node
    if isinstance(soup.body, Tag):
        return soup.body
    return soup


def extract_page_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.string:
        return normalize_text(soup.title.string)
    return ""


def is_useful_section_text(title: str, text: str, source: CurriculumSource) -> bool:
    normalized_title = normalize_text(title)
    normalized_text = normalize_text(text)
    if not normalized_text:
        return False

    lowered = normalized_text.lower()
    if any(snippet in lowered for snippet in BOILERPLATE_TEXT_SNIPPETS):
        return False
    if normalized_title.lower() == "popup zone":
        return False
    if normalized_title in {source.department_name, f"동국대학교 {source.department_name}"}:
        return False
    if any(pattern in normalized_text for pattern in IGNORED_TEXT_PATTERNS):
        return False

    has_signal_term = any(term in normalized_text for term in CONTENT_SIGNAL_TERMS)
    has_sentence = any(token in normalized_text for token in ("다.", "된다.", "한다.", ". "))
    if has_signal_term or has_sentence:
        return True

    # 메뉴/네비게이션 텍스트는 길어도 실제 교과 정보가 아니므로 제외한다.
    return False


def build_heading_paragraph_records(soup: BeautifulSoup, source: CurriculumSource) -> list[dict[str, str]]:
    root = find_content_root(soup)
    records: list[dict[str, str]] = []

    for heading in root.find_all(HEADING_RECORD_TAGS, recursive=True):
        title = normalize_text(heading.get_text(" ", strip=True))
        if not title or title in {"popup zone", source.department_name, f"동국대학교 {source.department_name}"}:
            continue

        parts: list[str] = []
        sibling = heading.find_next_sibling()
        while sibling:
            if isinstance(sibling, Tag) and sibling.name in HEADING_RECORD_TAGS:
                break
            if isinstance(sibling, Tag) and sibling.name in {"p", "ul", "ol", "div"}:
                text = normalize_text(sibling.get_text(" ", strip=True))
                if text:
                    parts.append(text)
            sibling = sibling.find_next_sibling()

        description = normalize_text(" ".join(parts))
        if not description:
            continue
        if not is_useful_section_text(title, description, source):
            continue

        records.append(
            {
                "college_name": source.college_name,
                "department_name": source.department_name,
                "department_url": source.department_url,
                "curriculum_title": source.curriculum_title,
                "curriculum_url": source.curriculum_url,
                "source_type": source.source_type,
                "section_title": source.curriculum_title or "교과과정",
                "record_type": "section_text",
                "course_code": "",
                "title": title,
                "course_name": title,
                "description": description,
                "credit": "",
                "semester": "",
                "grade": "",
                "course_type": "",
                "english_title": "",
                "raw_text": description,
            }
        )

    return records


def build_section_text_records(soup: BeautifulSoup, source: CurriculumSource) -> list[dict[str, str]]:
    root = find_content_root(soup)
    records: list[dict[str, str]] = []
    current_title = source.curriculum_title or "교과과정"
    current_parts: list[str] = []

    def flush() -> None:
        nonlocal current_parts
        text = normalize_text("\n".join(current_parts))
        if not text:
            current_parts = []
            return
        if not is_useful_section_text(current_title, text, source):
            current_parts = []
            return
        records.append(
            {
                "college_name": source.college_name,
                "department_name": source.department_name,
                "department_url": source.department_url,
                "curriculum_title": source.curriculum_title,
                "curriculum_url": source.curriculum_url,
                "source_type": source.source_type,
                "section_title": current_title,
                "record_type": "section_text",
                "course_code": "",
                "title": current_title or "교과과정 정보",
                "course_name": current_title or "교과과정 정보",
                "description": text,
                "credit": "",
                "semester": "",
                "grade": "",
                "course_type": "",
                "english_title": "",
                "raw_text": text,
            }
        )
        current_parts = []

    for node in root.find_all(["h1", "h2", "h3", "h4", "strong", "p", "li"], recursive=True):
        text = normalize_text(node.get_text(" ", strip=True))
        if not text:
            continue
        if node.name in {"h1", "h2", "h3", "h4", "strong"}:
            if current_parts:
                flush()
            current_title = text
            continue
        current_parts.append(text)

    if current_parts:
        flush()
    return records


def parse_curriculum_page(source: CurriculumSource) -> list[dict[str, str]]:
    html = fetch_page_html(source.curriculum_url)
    soup = make_soup(html)
    page_title = extract_page_title(soup)

    # 서울캠 학부 기준 수집이므로 대학원 전용 페이지는 제외한다.
    if "대학원과정" in page_title or "대학원 교과과정" in page_title:
        return []

    records: list[dict[str, str]] = []
    for table in soup.find_all("table"):
        df = read_table_to_df(table)
        if table_relevance_score(df) < 3:
            continue
        section_title = find_section_title(table)
        records.extend(build_table_records(df, source, section_title))

    if records:
        return records
    records = build_heading_paragraph_records(soup, source)
    if records:
        return records
    return build_section_text_records(soup, source)


def dedupe_records(records: Iterable[dict[str, str]]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if df.empty:
        return df
    dedupe_key = (
        df["department_name"].astype(str)
        + "||"
        + df["curriculum_url"].astype(str)
        + "||"
        + df["record_type"].astype(str)
        + "||"
        + df["course_code"].astype(str)
        + "||"
        + df["title"].astype(str)
        + "||"
        + df["raw_text"].astype(str)
    )
    df = df.loc[~dedupe_key.duplicated()].copy()
    df.sort_values(["college_name", "department_name", "curriculum_url", "record_type", "title"], inplace=True)
    return df


def main() -> None:
    sources = load_curriculum_sources(SOURCE_CSV_PATH)
    all_records: list[dict[str, str]] = []
    error_records: list[dict[str, str]] = []

    for source in sources:
        try:
            records = parse_curriculum_page(source)
        except Exception as exc:  # noqa: BLE001
            error_records.append(
                {
                    "college_name": source.college_name,
                    "department_name": source.department_name,
                    "department_url": source.department_url,
                    "curriculum_title": source.curriculum_title,
                    "curriculum_url": source.curriculum_url,
                    "source_type": source.source_type,
                    "section_title": "",
                    "record_type": "crawl_error",
                    "course_code": "",
                    "title": "",
                    "course_name": "",
                    "description": "",
                    "credit": "",
                    "semester": "",
                    "grade": "",
                    "course_type": "",
                    "english_title": "",
                    "raw_text": f"error: {exc}",
                }
            )
            continue
        all_records.extend(records)

    combined_df = dedupe_records(all_records + error_records)
    OUTPUT_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined_df.to_csv(OUTPUT_CSV_PATH, index=False, encoding="utf-8-sig")

    print(f"저장 완료: {OUTPUT_CSV_PATH}")
    print(f"입력 소스 수: {len(sources)}")
    print(f"생성 레코드 수: {len(combined_df)}")


if __name__ == "__main__":
    main()
