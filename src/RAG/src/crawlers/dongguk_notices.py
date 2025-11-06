"""Crawler for Dongguk University notice boards.

This module is a scriptified version of ``dongguk_notices.ipynb`` so it can be
reused by other parts of the project (e.g. scheduled ingestion)."""
from __future__ import annotations

import re
import time
from datetime import datetime, date
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote, urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup, FeatureNotFound
from bs4.builder import ParserRejectedMarkup

# ===== Constants mirrored from the notebook =====
BASE_URL = "https://www.dongguk.edu"
BOARD_CODES = {
    "일반공지": "GENERALNOTICES",
    "학사공지": "HAKSANOTICE",
    "장학공지": "JANGHAKNOTICE",
    "입학공지": "IPSINOTICE",
    "국제공지": "GLOBALNOLTICE",
    "학술공지": "HAKSULNOTICE",
    "안전공지": "SAFENOTICE",
    "행사공지": "BUDDHISTEVENT",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DonggukNoticeCrawler/1.0)",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}
SELECT_COLUMNS = [
    "board_name",
    "title",
    "category",
    "posted_at",
    "is_pinned",
    "detail_url",
    "content_text",
    "attachments",
]
COLUMN_LABELS = {
    "board_name": "게시판",
    "title": "제목",
    "category": "카테고리",
    "posted_at": "게시일",
    "is_pinned": "상단고정",
    "detail_url": "상세URL",
    "content_text": "본문",
    "attachments": "첨부파일",
}
TARGET_BOARDS = list(BOARD_CODES.keys())
DEFAULT_MAX_PAGES = 5
DEFAULT_REQUEST_DELAY = 0.2

PARSER_CANDIDATES: Iterable[str] = ("lxml", "html5lib", "html.parser")
HWPJSON_SECTION_PATTERN = re.compile(r"<!\[[^<]*?data-hwpjson.*?\]>", re.IGNORECASE | re.DOTALL)


# ===== HTML helpers =====
def _strip_hwpjson_sections(markup: str) -> str:
    cleaned = markup
    while True:
        updated = HWPJSON_SECTION_PATTERN.sub("", cleaned)
        if updated == cleaned:
            break
        cleaned = updated
    if "data-hwpjson" in cleaned.lower():
        cleaned = re.sub(r"<!\[\s*data-hwpjson", "<![CDATA", cleaned, flags=re.IGNORECASE)
    return cleaned


def _neutralize_marked_sections(markup: str) -> str:
    def replacer(match: re.Match) -> str:
        segment = match.group(0)
        return f"<!--{segment[2:-1]}-->"

    return re.sub(r"<!\[[^>]*?\]>", replacer, markup, flags=re.DOTALL)


def make_soup(markup: str) -> BeautifulSoup:
    cleaned_markup = _strip_hwpjson_sections(markup)
    last_exc: Optional[Exception] = None

    for parser in PARSER_CANDIDATES:
        try:
            return BeautifulSoup(cleaned_markup, parser)
        except (FeatureNotFound, ParserRejectedMarkup) as exc:
            last_exc = exc
        except Exception as exc:  # noqa: BLE001
            last_exc = exc

    fallback_markup = _neutralize_marked_sections(cleaned_markup)
    for parser in PARSER_CANDIDATES:
        try:
            return BeautifulSoup(fallback_markup, parser)
        except (FeatureNotFound, ParserRejectedMarkup):
            continue
        except Exception:
            continue

    if last_exc is not None:
        raise ParserRejectedMarkup(f"HTML 파싱 실패: {last_exc}") from last_exc
    raise RuntimeError("No HTML parser could parse the provided markup.")


# ===== Crawling primitives =====
def fetch_notice_list(board_code: str, page: int = 1) -> List[Dict[str, Any]]:
    """Return a notice summary list from the board list page."""
    url = f"{BASE_URL}/article/{board_code}/list"
    response = requests.get(url, params={"pageIndex": page}, headers=HEADERS, timeout=40)
    response.raise_for_status()

    soup = make_soup(response.text)
    notices: List[Dict[str, Any]] = []

    for item in soup.select("div.board_list > ul > li"):
        anchor = item.find("a")
        if anchor is None:
            continue

        onclick = anchor.get("onclick", "")
        match = re.search(r"goDetail\((\d+)\)", onclick)
        if match is None:
            continue
        article_id = int(match.group(1))

        title_tag = anchor.select_one("p.tit")
        title = title_tag.get_text(" ", strip=True) if title_tag else ""

        category_tag = anchor.select_one("div.top > em")
        category = category_tag.get_text(strip=True) if category_tag else None

        info_spans = anchor.select("div.info span")
        posted_at: Optional[date] = None
        views: Optional[int] = None
        if info_spans:
            raw_date = info_spans[0].get_text(strip=True).rstrip(".")
            try:
                posted_at = datetime.strptime(raw_date, "%Y.%m.%d").date()
            except ValueError:
                posted_at = None
        if len(info_spans) > 1:
            match_views = re.search(r"(\d+)", info_spans[1].get_text(strip=True))
            if match_views:
                views = int(match_views.group(1))

        is_pinned = anchor.select_one("div.mark span.fix") is not None

        notices.append(
            {
                "article_id": article_id,
                "title": title,
                "category": category,
                "posted_at": posted_at,
                "views": views,
                "is_pinned": is_pinned,
            }
        )

    return notices


def fetch_notice_detail(board_code: str, article_id: int) -> Dict[str, Any]:
    """Return detail data (HTML+text+attachments) for a single notice."""
    url = f"{BASE_URL}/article/{board_code}/detail/{article_id}"
    response = requests.get(url, headers=HEADERS, timeout=40)
    response.raise_for_status()

    soup = make_soup(response.text)
    container = soup.select_one("div.board_view")
    if container is None:
        raise RuntimeError("상세 정보를 찾을 수 없습니다.")

    title_tag = container.select_one("div.tit > p")
    title_text = title_tag.get_text(strip=True) if title_tag else ""

    info_block = container.select_one("div.tit > div.info")
    posted_at = None
    views = None
    if info_block:
        for span in info_block.select("span"):
            text = span.get_text(strip=True)
            if text.startswith("등록일"):
                raw_date = text.replace("등록일", "").strip().rstrip(".")
                try:
                    posted_at = datetime.strptime(raw_date, "%Y.%m.%d").date()
                except ValueError:
                    posted_at = None
            elif text.startswith("조회"):
                match_views = re.search(r"(\d+)", text)
                if match_views:
                    views = int(match_views.group(1))

    content_block = container.select_one("div.view_cont")
    if content_block:
        for script in content_block.find_all("script"):
            script.decompose()
        content_html = content_block.decode_contents().strip()
        content_text = content_block.get_text("\n", strip=True)
    else:
        content_html = ""
        content_text = ""

    attachments: List[Dict[str, Any]] = []
    for link in container.select("div.view_files ul li a"):
        href = link.get("href", "")
        match = re.search(r"downGO\('(.+?)','(.+?)','(.+?)'\)", href)
        if not match:
            continue
        name, path, stored = match.groups()
        download_url = urljoin(
            BASE_URL,
            f"/cmmn/fileDown.do?filename={quote(name)}&filepath={quote(path, safe='/')}&filerealname={quote(stored)}",
        )
        attachments.append({"name": name, "url": download_url})

    return {
        "title": title_text,
        "posted_at": posted_at,
        "views": views,
        "content_html": content_html,
        "content_text": content_text,
        "attachments": attachments,
        "detail_url": url,
    }


# ===== High level helpers =====
def collect_board(
    board_name: str,
    board_code: str,
    max_pages: Optional[int] = None,
    delay: float = DEFAULT_REQUEST_DELAY,
    earliest_year: int = 2024,
) -> pd.DataFrame:
    records: List[Dict[str, Any]] = []
    seen_ids: set[int] = set()
    page = 1

    while True:
        if max_pages is not None and page > max_pages:
            break

        notice_list = fetch_notice_list(board_code, page=page)
        if not notice_list:
            break

        for meta in notice_list:
            article_id = meta["article_id"]
            if article_id in seen_ids:
                continue
            seen_ids.add(article_id)

            detail = fetch_notice_detail(board_code, article_id)

            record = {
                "board_name": board_name,
                "board_code": board_code,
                "article_id": article_id,
                "title": meta.get("title"),
                "category": meta.get("category"),
                "posted_at": detail.get("posted_at") or meta.get("posted_at"),
                "views": detail.get("views") or meta.get("views"),
                "is_pinned": meta.get("is_pinned"),
                "detail_url": detail.get("detail_url"),
                "content_html": detail.get("content_html"),
                "content_text": detail.get("content_text"),
                "attachments": detail.get("attachments"),
            }

            posted_at = record["posted_at"]
            if earliest_year and isinstance(posted_at, date):
                if posted_at.year < earliest_year:
                    continue

            records.append(record)

            if delay:
                time.sleep(delay)

        page += 1

    if not records:
        columns = [COLUMN_LABELS[col] for col in SELECT_COLUMNS]
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(records)
    df["posted_at"] = pd.to_datetime(df["posted_at"], errors="coerce").dt.date
    df.sort_values(by=["posted_at", "article_id"], ascending=[False, False], inplace=True)
    df.reset_index(drop=True, inplace=True)

    selected = df[SELECT_COLUMNS].copy()
    selected.rename(columns=COLUMN_LABELS, inplace=True)
    return selected


def crawl_notices(
    boards: Optional[Iterable[str]] = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    delay: float = DEFAULT_REQUEST_DELAY,
) -> pd.DataFrame:
    boards = list(boards) if boards is not None else TARGET_BOARDS
    dataframes: List[pd.DataFrame] = []

    for board_name in boards:
        board_code = BOARD_CODES.get(board_name)
        if not board_code:
            print(f"⚠️ 게시판 코드를 찾을 수 없습니다: {board_name}")
            continue
        df = collect_board(board_name, board_code, max_pages=max_pages, delay=delay)
        dataframes.append(df)

    if not dataframes:
        columns = [COLUMN_LABELS[col] for col in SELECT_COLUMNS]
        return pd.DataFrame(columns=columns)

    combined = pd.concat(dataframes, ignore_index=True)
    combined.drop_duplicates(subset=["상세URL"], inplace=True)
    combined.sort_values(by=["게시일", "제목"], ascending=[False, True], inplace=True)
    combined.reset_index(drop=True, inplace=True)
    return combined


def crawl_recent_notices(max_pages: int = 3, delay: float = DEFAULT_REQUEST_DELAY) -> pd.DataFrame:
    """Convenience wrapper tuned for scheduled runs (crawl first few pages)."""
    return crawl_notices(max_pages=max_pages, delay=delay)


__all__ = [
    "crawl_notices",
    "crawl_recent_notices",
    "collect_board",
    "fetch_notice_list",
    "fetch_notice_detail",
]


def main() -> None:
    from pathlib import Path

    output_path = Path(__file__).resolve().parents[2] / "data" / "dongguk_notices.csv"
    df = crawl_notices()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"✅ {len(df)} notices saved to {output_path}")


if __name__ == "__main__":
    main()
