"""동국대 학사 일정을 추출하는 크롤러입니다."""
from __future__ import annotations

import re
from typing import List, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup, FeatureNotFound

SCHEDULE_URL = "https://www.dongguk.edu/schedule/detail"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DonggukScheduleCrawler/0.1)",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}
PARSER_CANDIDATES = ("lxml", "html5lib", "html.parser")
DATE_PATTERN = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})\.?")
EVENT_LINE_PATTERN = re.compile(
    r"^(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+)?"
    r"(?P<start>\d{4}\.\d{2}\.\d{2}\.)\s*"
    r"(?:~\s*(?P<end>\d{4}\.\d{2}\.\d{2}\.)\s*)?"
    r"(?P<content>.*)$"
)
DEPT_PATTERN = re.compile(r"\(\s*주관부서\s*:\s*([^\)]+)\)")
YEAR_TITLE_PATTERN = re.compile(r"(\d{4})\s*학년도\s*교내일정")
LINK_LABEL_PATTERN = re.compile(r"【\d+†[^】]+】")
MONTH_LABEL_PATTERN = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$")
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
    lines = [
        line.strip()
        for line in soup.get_text("\n", strip=True).splitlines()
        if line.strip()
    ]
    if not lines:
        raise ValueError("학사일정 데이터를 찾을 수 없습니다.")

    academic_year = ""
    records: List[dict[str, str]] = []
    current: dict[str, str] | None = None
    started = False

    for line in lines:
        year_match = YEAR_TITLE_PATTERN.search(line)
        if year_match:
            academic_year = year_match.group(1)
            started = True
            continue

        if not started:
            continue

        dept_match = DEPT_PATTERN.search(line)
        if dept_match and current is not None:
            current["주관부서"] = dept_match.group(1).strip()
            continue

        event_match = EVENT_LINE_PATTERN.match(line)
        if not event_match:
            if current is not None and line not in {"2026", "2025", "2024", "2023"} and not re.fullmatch(r"\d{2}", line):
                if not DEPT_PATTERN.search(line):
                    extra_content = clean_event_content(line)
                    if extra_content:
                        if current["내용"]:
                            current["내용"] = f"{current['내용']} {extra_content}".strip()
                        else:
                            current["내용"] = extra_content
            continue

        if current is not None:
            records.append(current)

        start_raw = event_match.group("start")
        end_raw = event_match.group("end") or start_raw
        content = clean_event_content(event_match.group("content"))

        current = {
            "학년도": academic_year,
            "구분": "학사일정",
            "내용": content,
            "주관부서": "",
            "start": normalize_date(start_raw),
            "end": normalize_date(end_raw),
        }

    if current is not None:
        records.append(current)

    if not records:
        raise ValueError("학사일정 텍스트 블록을 파싱하지 못했습니다.")

    return pd.DataFrame(records)


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


def clean_event_content(value: str) -> str:
    cleaned = LINK_LABEL_PATTERN.sub("", value or "")
    cleaned = re.sub(r"\b바로가기\b", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if MONTH_LABEL_PATTERN.fullmatch(cleaned):
        return ""
    return cleaned


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

    output_df = schedule_df[["학년도", "구분", "내용", "주관부서", "start", "end"]].copy()
    output_df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
