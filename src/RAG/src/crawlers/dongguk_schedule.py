"""One-off crawler that exports Dongguk University academic schedule."""
from __future__ import annotations

import re
from typing import List, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup, FeatureNotFound

SCHEDULE_URL = "https://www.dongguk.edu/schedule/detail?schedule_info_seq=22"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DonggukScheduleCrawler/0.1)",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}
PARSER_CANDIDATES = ("lxml", "html5lib", "html.parser")
DATE_PATTERN = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})\.?")
DEPT_PATTERN = re.compile(r"\(\s*주관부서\s*:\s*([^\)]+)\)")
OUTPUT_PATH = "./data/dongguk_schedule.csv"


def fetch_schedule_html(url: str, *, timeout: float = 10.0) -> str:
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
    if last_exc is not None:
        raise FeatureNotFound("지원되는 HTML 파서를 찾을 수 없습니다. 'lxml' 또는 'html5lib' 설치를 고려하세요.") from last_exc
    raise RuntimeError("HTML 파서를 초기화하지 못했습니다.")


def parse_schedule(html: str) -> pd.DataFrame:
    soup = make_soup(html)
    table = soup.select_one("table")
    if table is None:
        raise ValueError("학사일정 표를 찾을 수 없습니다.")

    headers: List[str] = []
    thead = table.find("thead")
    if thead:
        first_header_row = thead.find("tr")
        if first_header_row:
            headers = [th.get_text(" ", strip=True) for th in first_header_row.find_all("th")]

    body_rows = []
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        row = [cell.get_text(" ", strip=True) for cell in cells]
        body_rows.append(row)

    if not body_rows:
        raise ValueError("학사일정 데이터를 찾을 수 없습니다.")

    max_len = max(len(row) for row in body_rows)
    normalized_rows = [row + ["" for _ in range(max_len - len(row))] for row in body_rows]

    if headers and len(headers) == max_len:
        df = pd.DataFrame(normalized_rows, columns=headers)
    else:
        df = pd.DataFrame(normalized_rows)

    df = df.replace({None: ""})
    df = df[~df.apply(lambda row: all(str(value).strip() == "" for value in row), axis=1)]
    df.reset_index(drop=True, inplace=True)
    return df


def extract_schedule_metadata(html: str) -> Optional[str]:
    soup = make_soup(html)
    title_node = soup.select_one(".schedule-wrap h3, .schedule_view h3, .board_view .tit > p")
    if title_node:
        return title_node.get_text(" ", strip=True)
    return None


def normalize_date(value: str) -> str:
    match = DATE_PATTERN.search(value)
    if not match:
        return value.strip()
    year, month, day = match.groups()
    return f"{year}-{month}-{day}"


def split_period(period: str) -> tuple[str, str]:
    if not period or not isinstance(period, str):
        return "", ""

    matches = DATE_PATTERN.findall(period)
    if matches:
        normalized = [f"{y}-{m}-{d}" for y, m, d in matches]
        return normalized[0], normalized[-1]

    parts = [p.strip() for p in re.split(r"~|–|-", period) if p.strip()]
    if not parts:
        cleaned = period.strip()
        return cleaned, cleaned
    if len(parts) == 1:
        normalized = normalize_date(parts[0])
        return normalized, normalized
    start = normalize_date(parts[0])
    end = normalize_date(parts[-1])
    return start, end


def extract_department(text: str) -> tuple[str, str]:
    if not isinstance(text, str):
        return text, ""
    match = DEPT_PATTERN.search(text)
    if not match:
        return text.strip(), ""
    department = match.group(1).strip()
    cleaned = DEPT_PATTERN.sub("", text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned, department


def main() -> None:
    schedule_html = fetch_schedule_html(SCHEDULE_URL)
    schedule_df = parse_schedule(schedule_html)
    schedule_title = extract_schedule_metadata(schedule_html)

    if schedule_title:
        print(f"📅 학사일정 제목: {schedule_title}")

    period_col = None
    for candidate in ["기간", "일정", "기간(일정)", "기간/일정", "월"]:
        if candidate in schedule_df.columns:
            period_col = candidate
            break
    if period_col is None:
        for column in schedule_df.columns:
            series = schedule_df[column].astype(str)
            if series.str.contains(DATE_PATTERN).any():
                period_col = column
                break

    gubun_col = next((col for col in schedule_df.columns if isinstance(col, str) and "구분" in col), None)
    content_col = next((col for col in schedule_df.columns if isinstance(col, str) and "내용" in col), None)

    if period_col:
        start_end_df = schedule_df[period_col].apply(lambda value: pd.Series(split_period(value), index=["start", "end"]))
        schedule_df = pd.concat([schedule_df.drop(columns=[period_col]), start_end_df], axis=1)

    if content_col:
        content_split = schedule_df[content_col].apply(lambda text: pd.Series(extract_department(text), index=["내용", "주관부서"]))
        schedule_df[content_col] = content_split["내용"]
        schedule_df["주관부서"] = content_split["주관부서"]
    else:
        schedule_df["주관부서"] = ""

    column_order: List[str] = []
    if gubun_col:
        column_order.append(gubun_col)
    if content_col:
        column_order.append(content_col)
    column_order.append("주관부서")
    for name in ["start", "end"]:
        if name in schedule_df.columns:
            column_order.append(name)
    column_order.extend(col for col in schedule_df.columns if col not in column_order)
    schedule_df = schedule_df[column_order]

    schedule_df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
