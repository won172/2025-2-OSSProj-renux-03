"""동국대학교 공지 게시판을 수집하는 크롤러입니다."""
from __future__ import annotations

import re
import time
from datetime import datetime, date
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup, FeatureNotFound
from bs4.builder import ParserRejectedMarkup

from src.services.notice_image_text import (
    append_notice_image_text,
    collect_notice_image_text,
)

BASE_URL = "https://www.dongguk.edu"
BOARD_CODES = {
    "일반공지": "GENERALNOTICES",
    "학사공지": "HAKSANOTICE",
    "장학공지": "JANGHAKNOTICE",
    "입학공지": "IPSINOTICE",
    "국제교류공지": "INTEXNOTICE",
    "유학생공지": "INTSTUNOTICE",
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
    "board_code",
    "article_id",
    "title",
    "category",
    "posted_at",
    "is_pinned",
    "detail_url",
    "content_html",
    "content_text",
    "attachments",
]
COLUMN_LABELS = {
    "board_name": "게시판",
    "board_code": "게시판코드",
    "article_id": "원문글ID",
    "title": "제목",
    "category": "카테고리",
    "posted_at": "게시일",
    "is_pinned": "상단고정",
    "detail_url": "상세URL",
    "content_html": "본문HTML",
    "content_text": "본문",
    "attachments": "첨부파일",
}
TARGET_BOARDS = list(BOARD_CODES.keys())
DEFAULT_MAX_PAGES = 10 # 5 -> 30으로 증가 (더 많은 과거 공지 수집)
DEFAULT_REQUEST_DELAY = 0.5
DEFAULT_REQUEST_TIMEOUT = 40.0
DEFAULT_REQUEST_RETRIES = 3

PARSER_CANDIDATES: Iterable[str] = ("lxml", "html5lib", "html.parser")


class NoticeCrawlError(RuntimeError):
    """Raised when no configured notice board produced a trustworthy list page."""


# ===== HTML 처리 헬퍼 =====
def _strip_hwpjson_sections(markup: str) -> str:
    """`<![ ... data-hwpjson ... ]>` 구획을 제거합니다.

    정규식 DOTALL `.*?` 스캔은 대형 HWP 본문에서 catastrophic backtracking을
    유발할 수 있어, 문자열 인덱스 기반 수동 스캐너로 처리한다.
    """
    lower = markup.lower()
    out: List[str] = []
    pos = 0
    while True:
        start = lower.find("<![", pos)
        if start == -1:
            out.append(markup[pos:])
            break
        end = lower.find("]>", start)
        if end == -1:
            out.append(markup[pos:])
            break
        section_lower = lower[start : end + 2]
        if "data-hwpjson" in section_lower:
            out.append(markup[pos:start])  # 구획 제거
        else:
            out.append(markup[pos : end + 2])
        pos = end + 2
    cleaned = "".join(out)
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


# ===== 크롤링 기본 함수 =====
def _get_with_retry(
    url: str,
    *,
    params: dict | None = None,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
    retries: int = DEFAULT_REQUEST_RETRIES,
):
    """일시적 네트워크 오류(503/타임아웃 등)에 지수 백오프로 재시도한다.

    한 번의 일시 장애로 게시판 수집이 통째로 중단/누락되는 것을 막는다.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # 1s → 2s → 4s
    raise last_exc  # type: ignore[misc]


def fetch_notice_list(
    board_code: str,
    page: int = 1,
    *,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
    retries: int = DEFAULT_REQUEST_RETRIES,
) -> List[Dict[str, Any]]:
    """게시판 목록 페이지에서 공지 요약 목록을 가져옵니다."""
    url = f"{BASE_URL}/article/{board_code}/list"
    response = _get_with_retry(
        url,
        params={"pageIndex": page},
        timeout=timeout,
        retries=retries,
    )

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


def fetch_notice_detail(
    board_code: str,
    article_id: int,
    *,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
    retries: int = DEFAULT_REQUEST_RETRIES,
) -> Dict[str, Any]:
    """단일 공지의 HTML·텍스트·첨부 정보를 가져옵니다."""
    url = f"{BASE_URL}/article/{board_code}/detail/{article_id}"
    response = _get_with_retry(url, timeout=timeout, retries=retries)

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

    # 표·일정이 본문 이미지에만 있는 공지는 get_text()만으로 핵심 사실이 사라진다.
    # 이미지 하나의 실패는 본문 수집을 막지 않으며, 검증된 SHA 캐시를 우선하고
    # 캐시 미스만 설정된 vision 모델로 전사한다.
    image_texts = collect_notice_image_text(
        content_html,
        detail_url=url,
        http_get=lambda image_url, timeout: _get_with_retry(
            image_url,
            timeout=timeout,
            retries=retries,
        ),
        timeout=timeout,
    )
    content_text = append_notice_image_text(content_text, image_texts)
    attachments.extend(
        {
            "name": "본문 이미지 전사",
            "url": image.image_url,
            "sha256": image.sha256,
            "extraction_method": image.method,
        }
        for image in image_texts
    )

    return {
        "title": title_text,
        "posted_at": posted_at,
        "views": views,
        "content_html": content_html,
        "content_text": content_text,
        "attachments": attachments,
        "detail_url": url,
    }


# ===== 상위 헬퍼 =====
def collect_board(
    board_name: str,
    board_code: str,
    max_pages: Optional[int] = None,
    delay: float = DEFAULT_REQUEST_DELAY,
    earliest_year: Optional[int] = 2023,
    known_ids: set[int] | None = None,
    since: date | None = None,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    request_retries: int = DEFAULT_REQUEST_RETRIES,
) -> pd.DataFrame:
    if since is not None and known_ids is not None:
        raise ValueError("since crawl must revisit known article IDs")
    if since is not None:
        earliest_year = None
    records: List[Dict[str, Any]] = []
    seen_ids: set[int] = set()
    page = 1
    stop_collecting = False
    failed_articles = 0
    list_pages_succeeded = 0
    list_pages_failed = 0
    list_rows_seen = 0
    termination_reason = "page_cap"
    coverage_error: str | None = None
    previous_list_date: date | None = None
    oldest_list_date: date | None = None
    # 목록이 대체로 날짜 역순이지만 중간에 섞인 글이 있을 수 있으므로,
    # 오래된 글이 연속으로 이 횟수만큼 나와야 수집을 중단한다(즉시 중단 시 누락 위험).
    OLD_STREAK_TO_STOP = 5

    old_streak = 0
    while True:
        if max_pages is not None and page > max_pages:
            break

        try:
            notice_list = fetch_notice_list(
                board_code,
                page=page,
                timeout=request_timeout,
                retries=request_retries,
            )
        except Exception as exc:  # noqa: BLE001 — 목록 한 페이지 실패가 게시판 전체를 중단시키지 않도록
            list_pages_failed += 1
            termination_reason = "list_error"
            print(f"⚠️ [{board_name}] 목록 페이지 {page} 수집 실패: {exc}")
            break
        list_pages_succeeded += 1
        list_rows_seen += len(notice_list)
        if not notice_list:
            termination_reason = "empty_page"
            break

        boundary_page = False
        if since is not None:
            non_pinned_dates: list[date] = []
            for meta in notice_list:
                posted = meta.get("posted_at")
                if not isinstance(posted, date):
                    coverage_error = "undated_list_row"
                    break
                posted = posted.date() if isinstance(posted, datetime) else posted
                if not meta.get("is_pinned"):
                    if previous_list_date is not None and posted > previous_list_date:
                        coverage_error = "non_monotonic_list_dates"
                        break
                    previous_list_date = posted
                    non_pinned_dates.append(posted)
                    oldest_list_date = min(oldest_list_date, posted) if oldest_list_date else posted
            if coverage_error:
                termination_reason = coverage_error
                break
            # A single old item on a mixed page does not establish coverage.
            boundary_page = bool(non_pinned_dates) and all(day < since for day in non_pinned_dates)

        for meta in notice_list:
            article_id = meta["article_id"]
            if article_id in seen_ids:
                continue
            seen_ids.add(article_id)

            if since is not None and meta["posted_at"] < since:
                continue

            if known_ids is not None and article_id in known_ids:
                continue

            # 상세 파싱/네트워크 오류가 게시판 전체 수집을 중단시키지 않도록 격리
            try:
                detail = fetch_notice_detail(
                    board_code,
                    article_id,
                    timeout=request_timeout,
                    retries=request_retries,
                )
            except Exception as exc:  # noqa: BLE001
                failed_articles += 1
                print(f"⚠️ [{board_name}] 상세 수집 실패 (article_id={article_id}) — 목록 정보로 색인합니다: {exc}")
                detail = {
                    "posted_at": meta.get("posted_at"),
                    "views": meta.get("views"),
                    "detail_url": f"{BASE_URL}/article/{board_code}/detail/{article_id}",
                    "content_html": "",
                    "content_text": "",
                    "attachments": [],
                }

            if since is not None and detail.get("posted_at") not in (None, meta["posted_at"]):
                coverage_error = "detail_date_mismatch"
                termination_reason = coverage_error
                break

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
            if earliest_year and isinstance(posted_at, (date, datetime)):
                if posted_at.year < earliest_year:
                    if not record["is_pinned"]:
                        old_streak += 1
                        if old_streak >= OLD_STREAK_TO_STOP:
                            stop_collecting = True
                            break
                    if delay:
                        time.sleep(delay)
                    continue
                else:
                    old_streak = 0

            records.append(record)

            if delay:
                time.sleep(delay)

        if stop_collecting:
            break
        if coverage_error:
            break
        if boundary_page:
            termination_reason = "before_since_boundary"
            break
        page += 1

    if failed_articles:
        print(f"⚠️ [{board_name}] 상세 수집 실패 {failed_articles}건 (수집 성공 {len(records)}건)")

    if records:
        df = pd.DataFrame(records)
        df["posted_at"] = pd.to_datetime(df["posted_at"], errors="coerce").dt.date
        df.sort_values(by=["posted_at", "article_id"], ascending=[False, False], inplace=True)
        df.reset_index(drop=True, inplace=True)
        selected = df[SELECT_COLUMNS].copy()
        selected.rename(columns=COLUMN_LABELS, inplace=True)
    else:
        columns = [COLUMN_LABELS[col] for col in SELECT_COLUMNS]
        selected = pd.DataFrame(columns=columns)

    if list_pages_succeeded == 0 and list_pages_failed:
        crawl_status = "failed"
    elif list_pages_failed or failed_articles or coverage_error or (since is not None and termination_reason == "page_cap"):
        crawl_status = "partial"
    else:
        crawl_status = "success"
    selected.attrs["crawl_diagnostics"] = {
        "board_name": board_name,
        "board_code": board_code,
        "status": crawl_status,
        "list_pages_succeeded": list_pages_succeeded,
        "list_pages_failed": list_pages_failed,
        "list_rows_seen": list_rows_seen,
        "records_collected": len(selected),
        "detail_failures": failed_articles,
        "termination_reason": termination_reason if since is not None else None,
        "coverage_complete": (
            termination_reason in {"empty_page", "before_since_boundary"}
            and list_pages_succeeded > 0
            and list_rows_seen > 0
            and not (list_pages_failed or failed_articles or coverage_error)
        ) if since is not None else None,
        "oldest_list_date": oldest_list_date.isoformat() if oldest_list_date else None,
        "since": since.isoformat() if since is not None else None,
    }
    return selected


def crawl_notices(
    boards: Optional[Iterable[str]] = None,
    max_pages: Optional[int] = DEFAULT_MAX_PAGES,
    delay: float = DEFAULT_REQUEST_DELAY,
    earliest_year: Optional[int] = 2023,
    known_ids_by_board: dict[str, set[int]] | None = None,
    since: date | None = None,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    request_retries: int = DEFAULT_REQUEST_RETRIES,
) -> pd.DataFrame:
    boards = list(boards) if boards is not None else TARGET_BOARDS
    dataframes: List[pd.DataFrame] = []
    diagnostics: List[Dict[str, Any]] = []

    for board_name in boards:
        board_code = BOARD_CODES.get(board_name)
        if not board_code:
            print(f"⚠️ 게시판 코드를 찾을 수 없습니다: {board_name}")
            continue
        known_ids = known_ids_by_board.get(board_name) if known_ids_by_board is not None else None
        df = collect_board(
            board_name,
            board_code,
            max_pages=max_pages,
            delay=delay,
            earliest_year=earliest_year,
            known_ids=known_ids,
            since=since,
            request_timeout=request_timeout,
            request_retries=request_retries,
        )
        dataframes.append(df)
        diagnostic = df.attrs.get("crawl_diagnostics")
        if isinstance(diagnostic, dict):
            diagnostics.append(dict(diagnostic))

    if not dataframes:
        columns = [COLUMN_LABELS[col] for col in SELECT_COLUMNS]
        return pd.DataFrame(columns=columns)

    failed_boards = [
        str(item.get("board_name") or item.get("board_code") or "unknown")
        for item in diagnostics
        if item.get("status") == "failed"
    ]
    incomplete_boards = [
        str(item.get("board_name") or item.get("board_code") or "unknown")
        for item in diagnostics
        if item.get("status") in {"failed", "partial"}
    ]
    reachable_boards = [
        item for item in diagnostics if int(item.get("list_pages_succeeded") or 0) > 0
    ]
    if diagnostics and not reachable_boards:
        raise NoticeCrawlError(
            f"공지 게시판 {len(diagnostics)}개 모두 목록 수집에 실패했습니다: "
            + ", ".join(failed_boards)
        )
    if reachable_boards and not any(int(item.get("list_rows_seen") or 0) > 0 for item in reachable_boards):
        raise NoticeCrawlError(
            "접속 가능한 모든 공지 게시판의 첫 목록이 0건입니다. "
            "사이트 구조 변경 또는 차단 여부를 확인하세요."
        )

    combined = pd.concat(dataframes, ignore_index=True)
    if not combined.empty:
        combined.drop_duplicates(subset=["상세URL"], inplace=True)
        combined.sort_values(by=["게시일", "제목"], ascending=[False, True], inplace=True)
        combined.reset_index(drop=True, inplace=True)
    combined.attrs["crawl_diagnostics"] = diagnostics
    combined.attrs["crawl_failed_boards"] = failed_boards
    combined.attrs["crawl_incomplete_boards"] = incomplete_boards
    combined.attrs["crawl_status"] = "partial" if incomplete_boards else "success"
    return combined


def crawl_recent_notices(
    max_pages: int = 3,
    delay: float = DEFAULT_REQUEST_DELAY,
    *,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    request_retries: int = DEFAULT_REQUEST_RETRIES,
) -> pd.DataFrame:
    """정기 실행을 염두에 두고 앞쪽 몇 페이지만 수집하는 편의 래퍼입니다."""
    return crawl_notices(
        max_pages=max_pages,
        delay=delay,
        request_timeout=request_timeout,
        request_retries=request_retries,
    )


# ===== 삭제 감지용 상세 URL 확인 =====
NOTICE_PROBE_PRESENT = "present"
NOTICE_PROBE_MISSING = "missing"
NOTICE_PROBE_UNKNOWN = "unknown"
_OFFICIAL_DETAIL_PATTERN = re.compile(
    r"^https://www\.dongguk\.edu/article/(?P<board>[A-Z]+)/detail/(?P<article>\d+)$"
)
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MISSING_STATUSES = {404, 410}
_OFFICIAL_HOSTS = frozenset({"www.dongguk.edu"})
# 같은 게시판 목록 외에 삭제 글 리다이렉트 대상으로 확인된 오류 경로(소문자, 끝 "/"
# 제외). 라이브 확인 전이므로 비워 둔다. 확인되면 여기에만 추가한다.
NOTICE_DELETION_REDIRECT_EXTRA_PATHS: frozenset[str] = frozenset()


def is_notice_deletion_redirect(detail_url: str, location: str, *, board_code: str) -> bool:
    """리다이렉트 대상이 알려진 '삭제 글' 목적지인지 판단한다.

    missing으로 보는 것은 공식 호스트의 같은 게시판 목록(``/article/{board}/list``,
    게시판 코드 대소문자 무시, 쿼리 허용)과 명시적으로 등록한 오류 경로뿐이다.
    로그인/SSO, 점검·WAF 페이지, 다른 게시판, 외부 호스트는 모두 False(unknown).
    """
    target = urlparse(urljoin(detail_url, location))
    if target.scheme not in {"http", "https"}:
        return False
    if (target.hostname or "").lower() not in _OFFICIAL_HOSTS:
        return False
    path = target.path.rstrip("/").lower()
    if path == f"/article/{board_code.lower()}/list":
        return True
    return path in NOTICE_DELETION_REDIRECT_EXTRA_PATHS


def parse_official_notice_detail_url(url: str | None) -> tuple[str, int] | None:
    """공식 공지 상세 URL이면 ``(board_code, article_id)``를, 아니면 None을 돌려준다.

    삭제 확인은 이 크롤러가 직접 만든 URL 형태에만 적용한다. 수동 공지,
    도서관 운영시간처럼 공지 형태로 합쳐진 다른 원천은 대상이 아니다.
    """
    match = _OFFICIAL_DETAIL_PATTERN.match(str(url or "").strip())
    if match is None or match.group("board") not in BOARD_CODES.values():
        return None
    return match.group("board"), int(match.group("article"))


def probe_notice_detail(
    url: str,
    *,
    session: Any = None,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> tuple[str, int | None]:
    """리다이렉트를 따라가지 않고 상세 URL이 아직 존재하는지 한 번 확인한다.

    반환값은 ``(present|missing|unknown, HTTP status)``다.

    - 200 → present. 본문 구조는 검사하지 않는다(오판 시 삭제보다 유지를 택한다).
    - 404/410, 또는 같은 게시판 목록·등록된 오류 경로로 가는 30x → missing.
      감사 문서 10의 관찰(삭제 글 상세 URL이 302)에 근거한다.
    - 그 밖의 모든 리다이렉트(로그인/SSO, 점검·WAF, 같은 글 정규화, Location 없음),
      그 밖의 4xx/5xx, 네트워크 오류·타임아웃 → unknown. unknown은 절대 삭제 근거가
      되지 않는다.

    재시도하지 않는다. 일시 장애는 unknown으로 남고 다음 실행에서 다시 본다.
    """
    parsed = parse_official_notice_detail_url(url)
    if parsed is None:
        return NOTICE_PROBE_UNKNOWN, None
    client = session if session is not None else requests
    try:
        response = client.get(
            url,
            headers=HEADERS,
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
    except Exception:  # noqa: BLE001 - 어떤 오류도 삭제 근거가 아니다.
        return NOTICE_PROBE_UNKNOWN, None
    try:
        status = int(getattr(response, "status_code", 0) or 0)
        if status == 200:
            return NOTICE_PROBE_PRESENT, status
        if status in _MISSING_STATUSES:
            return NOTICE_PROBE_MISSING, status
        if status in _REDIRECT_STATUSES:
            headers = getattr(response, "headers", None) or {}
            location = str(headers.get("Location") or headers.get("location") or "").strip()
            if not location:
                return NOTICE_PROBE_UNKNOWN, status
            if is_notice_deletion_redirect(url, location, board_code=parsed[0]):
                return NOTICE_PROBE_MISSING, status
            # 로그인/SSO, 점검·WAF 페이지, 같은 글 정규화 등 삭제로 확정할 수 없는 곳.
            return NOTICE_PROBE_UNKNOWN, status
        return NOTICE_PROBE_UNKNOWN, status or None
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "NOTICE_PROBE_MISSING",
    "NOTICE_PROBE_PRESENT",
    "NOTICE_PROBE_UNKNOWN",
    "NoticeCrawlError",
    "crawl_notices",
    "crawl_recent_notices",
    "collect_board",
    "fetch_notice_list",
    "fetch_notice_detail",
    "is_notice_deletion_redirect",
    "parse_official_notice_detail_url",
    "probe_notice_detail",
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
