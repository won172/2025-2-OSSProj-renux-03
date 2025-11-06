"""One-off crawler for Dongguk Statistics department course information."""
from __future__ import annotations

import re
import warnings
from typing import List, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup, FeatureNotFound, Tag

warnings.filterwarnings("ignore")

TARGET_URL = "https://stat.dongguk.edu/page/176"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DonggukStatisticsCrawler/0.1)",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}
SECTION_MAPPINGS = {
    "전공과목 개설총괄표": "major_course_overview_df",
    "학과내규": "department_policy_df",
    "졸업요건": "graduation_requirements_df",
    "진출분야": "career_track_df",
    "트랙별 이수체계": "career_track_df",
    "교과목 해설": "course_description_df",
}
PARSER_CANDIDATES = ("lxml", "html5lib", "html.parser")
COURSE_DESCRIPTION_TARGET_COLUMNS = ["학수번호", "국문교과목명", "영문명", "해설"]
COURSE_DESCRIPTION_KEYWORDS = {
    "학수번호": ["학수", "과목코드", "코드", "course", "subject"],
    "국문교과목명": ["국문", "교과목명", "과목명", "korean"],
    "영문명": ["영문", "english", "영문명"],
    "해설": ["해설", "설명", "비고", "description"],
}


class CoursePageError(RuntimeError):
    """Raised when the statistics course page cannot be parsed."""


def fetch_page_html(url: str, *, timeout: float = 10.0) -> str:
    response = requests.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    return response.text


def make_soup(markup: str) -> BeautifulSoup:
    last_exc: Optional[Exception] = None
    for parser in PARSER_CANDIDATES:
        try:
            return BeautifulSoup(markup, parser)
        except FeatureNotFound as exc:
            last_exc = exc
            continue
    try:
        return BeautifulSoup(markup, "html.parser")
    except Exception as exc:  # noqa: BLE001
        raise CoursePageError("사용 가능한 HTML 파서를 찾을 수 없습니다. 'lxml' 또는 'html5lib' 설치를 고려하세요.") from exc


def read_table_to_df(table: Tag) -> pd.DataFrame:
    headers: Optional[List[str]] = None
    rows: List[List[str]] = []

    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        cell_texts = [cell.get_text(" ", strip=True) for cell in cells]
        has_header = any(cell.name == "th" for cell in cells)
        if headers is None and has_header:
            headers = cell_texts
            continue
        rows.append(cell_texts)

    if headers is None and rows:
        max_len = max(len(row) for row in rows)
        headers = [f"col_{i + 1}" for i in range(max_len)]
    elif headers is None:
        headers = []

    max_len = len(headers) if headers else max((len(row) for row in rows), default=0)
    if headers and len(headers) < max_len:
        headers = headers + [f"col_{len(headers) + i + 1}" for i in range(max_len - len(headers))]

    normalized_rows: List[List[str]] = []
    for row in rows:
        if len(row) < max_len:
            row = row + ["" for _ in range(max_len - len(row))]
        elif len(row) > max_len:
            row = row[:max_len]
        normalized_rows.append(row)

    df = pd.DataFrame(normalized_rows, columns=headers)
    df.columns = [str(col).strip() for col in df.columns]
    return df


def find_section_title(table: Tag) -> Optional[str]:
    heading_tags = {"h1", "h2", "h3", "h4", "strong"}
    previous = table
    while previous := previous.find_previous():
        if getattr(previous, "name", None) in heading_tags:
            text = previous.get_text(" ", strip=True)
            if text:
                return text
    return None


def evaluate_table_relevance(table: Tag) -> int:
    header_text = " ".join(th.get_text(" ", strip=True) for th in table.find_all("th"))
    body_sample = table.get_text(" ", strip=True)
    keywords = ["과목", "구분", "학점", "학년", "이수", "code", "subject"]
    score = 0
    for keyword in keywords:
        if keyword in header_text:
            score += 2
        if keyword in body_sample:
            score += 1
    return score


def extract_course_sections(html: str) -> pd.DataFrame:
    soup = make_soup(html)
    tables = soup.find_all("table")
    if not tables:
        raise CoursePageError("페이지에서 표를 찾을 수 없습니다.")

    course_frames: List[pd.DataFrame] = []
    for table in tables:
        score = evaluate_table_relevance(table)
        if score <= 1:
            continue
        df = read_table_to_df(table)
        if df.empty:
            continue
        section_title = find_section_title(table) or "미분류"
        df.insert(0, "section", section_title)
        course_frames.append(df)

    if not course_frames:
        raise CoursePageError("전공 과목 정보를 담은 표를 찾지 못했습니다.")

    combined = pd.concat(course_frames, ignore_index=True)
    for column in combined.columns:
        if combined[column].dtype == object:
            combined[column] = combined[column].map(lambda value: re.sub(r"\s+", " ", str(value)).strip() if pd.notna(value) else value)
    return combined


def normalize_course_description(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=COURSE_DESCRIPTION_TARGET_COLUMNS)

    working = df.copy()
    working.columns = [str(col).strip() for col in working.columns]
    working = working.applymap(lambda value: re.sub(r"\s+", " ", str(value)).strip() if pd.notna(value) else "")
    working = working.loc[:, ~(working.eq("").all())]

    if not working.empty:
        first_row_tokens = [str(value).strip().lower() for value in working.iloc[0].tolist()]
        keywords_flat = [kw.lower() for values in COURSE_DESCRIPTION_KEYWORDS.values() for kw in values]
        if any(any(keyword in token for keyword in keywords_flat) for token in first_row_tokens):
            working.columns = [str(value).strip() for value in working.iloc[0].tolist()]
            working = working.iloc[1:].reset_index(drop=True)
            working = working.applymap(lambda value: re.sub(r"\s+", " ", str(value)).strip() if pd.notna(value) else "")

    length = len(working)
    columns_data = {target: ["" for _ in range(length)] for target in COURSE_DESCRIPTION_TARGET_COLUMNS}

    remaining_columns = list(working.columns)
    for target, keywords in COURSE_DESCRIPTION_KEYWORDS.items():
        match = None
        for column in remaining_columns:
            column_lower = column.lower()
            if any(keyword.lower() in column_lower for keyword in keywords):
                match = column
                break
        if match is not None:
            columns_data[target] = working[match].tolist()
            remaining_columns.remove(match)

    values_matrix = working.values.tolist()
    for row_idx, row_values in enumerate(values_matrix):
        for position, target in enumerate(COURSE_DESCRIPTION_TARGET_COLUMNS):
            if columns_data[target][row_idx]:
                continue
            value = row_values[position] if position < len(row_values) else ""
            columns_data[target][row_idx] = value

    result = pd.DataFrame(columns_data)
    result = result[result.apply(lambda r: any(str(value).strip() for value in r), axis=1)]
    result.reset_index(drop=True, inplace=True)

    code_pattern = re.compile(r"^[A-Za-z]{3}\d{4}$")
    merged_records = []
    for record in result.to_dict("records"):
        code = str(record["학수번호"]).strip()
        if code and not code_pattern.match(code):
            base_desc = str(record["해설"]).strip()
            merged_desc = f"{base_desc} ({code})".strip() if base_desc else code
            record["해설"] = merged_desc
            record["학수번호"] = ""

        if record["학수번호"]:
            merged_records.append(record)
        else:
            has_meta = any(str(record.get(col, "")).strip() for col in ["국문교과목명", "영문명"])
            if merged_records and not has_meta:
                prev = merged_records[-1]
                if record.get("해설"):
                    combined_desc = " ".join(filter(None, [prev.get("해설", "").strip(), str(record["해설"]).strip()])).strip()
                    prev["해설"] = combined_desc
            else:
                merged_records.append(record)

    result = pd.DataFrame(merged_records)
    result["학수번호"] = result["학수번호"].map(lambda v: v.strip() if isinstance(v, str) else v)
    result["해설"] = result["해설"].map(lambda v: v.strip() if isinstance(v, str) else v)
    result = result[result.apply(lambda r: any(str(value).strip() for value in r), axis=1)]
    result.reset_index(drop=True, inplace=True)
    return result


def assign_section_dataframes(section_frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    assigned: dict[str, pd.DataFrame] = {}
    for section_name, df in section_frames.items():
        key = str(section_name).strip()
        matched_key = None
        for needle, var_name in SECTION_MAPPINGS.items():
            if needle in key:
                matched_key = var_name
                break
        if matched_key:
            cleaned = df.copy()
            if matched_key == "course_description_df":
                cleaned = normalize_course_description(cleaned)
            assigned[matched_key] = cleaned
    return assigned


def main() -> None:
    try:
        page_html = fetch_page_html(TARGET_URL)
    except requests.RequestException as exc:  # noqa: BLE001
        raise CoursePageError(f"페이지를 가져오지 못했습니다: {exc}") from exc

    course_df = extract_course_sections(page_html)
    print(f"총 {len(course_df)}개의 과목 레코드 추출")

    section_frames = {}
    for section, group in course_df.groupby("section", dropna=False):
        section_df = group.drop(columns=["section"]).reset_index(drop=True)
        section_df = section_df.applymap(lambda value: re.sub(r"\s+", " ", str(value)).strip() if pd.notna(value) else "")
        section_df = section_df.loc[:, ~(section_df.eq("").all())]
        section_frames[str(section)] = section_df

    assigned_frames = assign_section_dataframes(section_frames)

    major_course_overview_df = assigned_frames.get("major_course_overview_df", pd.DataFrame())
    course_description_df = assigned_frames.get("course_description_df", pd.DataFrame())

    major_course_overview_df.to_csv("./data/dongguk_statistics_major_course.csv", index=False, encoding="utf-8-sig")
    course_description_df.to_csv("./data/dongguk_statistics_course_descriptions.csv", index=False, encoding="utf-8-sig")
    print("저장 완료: statistics major/course description CSV")


if __name__ == "__main__":
    main()
