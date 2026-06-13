"""동국대학교 생협 학생식당 학식(식단) 크롤러입니다.

데이터 소스: 동국대 생협 식단표 (서버 렌더링 — Selenium 불필요).
  https://dgucoop.dongguk.edu/store/store.php?w=4&l=1&sday=<unix_ts>&sdate=<요일index>
  - sday: 조회할 날짜의 Unix 타임스탬프(해당 날짜의 메뉴가 표시됨)
  - sdate: 요일 인덱스(0=월 ... 표시 강조용, 날짜 선택은 sday가 결정)
페이지는 식당별(상록원3층/2층/1층, 누리터) 중식/석식 메뉴와 가격을 표로 제공한다.

학식은 매일 갱신되므로, 이번 주 월요일부터 향후 일정 기간까지의 날짜를 순회하며 수집한다.
"""
from __future__ import annotations

import io
import re
import time
from datetime import datetime, date, timedelta, timezone
from typing import Iterable, List, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup, FeatureNotFound

MEALS_URL = "https://dgucoop.dongguk.edu/store/store.php"
MEALS_QUERY_BASE = {"w": 4, "l": 1}  # w=4: 식단표 화면
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DonggukMealsCrawler/1.0)",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}
PARSER_CANDIDATES: Iterable[str] = ("lxml", "html5lib", "html.parser")
KST = timezone(timedelta(hours=9))

# 페이지에 등장하는 식당 구분 앵커(문서 순서대로). 이 앵커로 메뉴 텍스트를 식당별로 분할한다.
RESTAURANT_ANCHORS = ["상록원3층식당", "상록원2층식당", "상록원1층식당", "누리터"]
# 식당 표시명(앵커 → 사람이 읽기 좋은 정식 명칭). 누리터는 일산(WISE)캠퍼스.
RESTAURANT_LABELS = {
    "상록원3층식당": "상록원3층식당(집밥·한그릇)",
    "상록원2층식당": "상록원2층식당(백반·일품·양식·뚝배기)",
    "상록원1층식당": "상록원1층식당(솥앤누들)",
    "누리터": "누리터식당(일산캠퍼스)",
}
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]
DATE_HEADER_PATTERN = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
# 식당 블록 끝에 붙는 생협 안내 문구(원산지/분식 안내 등) 시작 지점.
NOTICE_SPLIT_PATTERN = re.compile(r"※")
# 휴무 판정: 본문에 '휴무'가 있고 가격(￦/원)이 전혀 없으면 그 끼니/식당은 운영 안 함.
# (열려 있으면 항상 가격이 붙으므로, 헤더+'휴무'만 있는 블록을 휴무로 본다.)
CLOSED_KEYWORD = re.compile(r"휴무")
PRICE_PATTERN = re.compile(r"￦\s*[\d,]+|\d[\d,]*\s*원")
DEFAULT_REQUEST_DELAY = 0.4

# === D-Flex(경영관) 학식 — 주간 PDF 식단표를 텍스트/표로 추출(OCR 불필요) ===
# dongguk.edu 게시판 CMS의 FOODDFLEX 보드를 공지 크롤러로 재사용해 PDF 첨부를 받는다.
DFLEX_BOARD_CODE = "FOODDFLEX"
DFLEX_RESTAURANT = "경영관 D-Flex식당"
DFLEX_PDF_DATE_PATTERN = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_DFLEX_WEEKDAY_WORDS = {"월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"}


def make_soup(markup: str) -> BeautifulSoup:
    last_exc: Optional[Exception] = None
    for parser in PARSER_CANDIDATES:
        try:
            return BeautifulSoup(markup, parser)
        except FeatureNotFound as exc:
            last_exc = exc
    if last_exc is not None:
        raise FeatureNotFound(
            "지원되는 HTML 파서를 찾을 수 없습니다. 'lxml' 또는 'html5lib' 설치를 고려하세요."
        ) from last_exc
    raise RuntimeError("HTML 파서를 초기화하지 못했습니다.")


def fetch_day_html(target: date, *, timeout: float = 20.0) -> str:
    """특정 날짜의 식단표 페이지 HTML을 가져옵니다."""
    # 사이트가 KST 기준 자정 타임스탬프로 날짜를 식별하므로 KST 자정으로 계산한다.
    midnight_kst = datetime(target.year, target.month, target.day, tzinfo=KST)
    sday = int(midnight_kst.timestamp())
    params = {**MEALS_QUERY_BASE, "sday": sday, "sdate": target.weekday()}
    response = requests.get(MEALS_URL, params=params, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    return response.text


def _find_menu_table(soup: BeautifulSoup):
    """'식당별메뉴' 문구를 포함하는 메뉴 표를 찾습니다(없으면 None)."""
    for table in soup.find_all("table"):
        if "식당별메뉴" in table.get_text():
            return table
    return None


def _verify_page_date(menu_text: str, target: date) -> bool:
    """페이지 헤더의 'MM월 DD일'이 요청 날짜와 일치하는지 확인합니다(파라미터 무시 방어)."""
    match = DATE_HEADER_PATTERN.search(menu_text)
    if not match:
        return False
    month, day = int(match.group(1)), int(match.group(2))
    return month == target.month and day == target.day


def parse_day_menus(html: str, target: date) -> List[dict]:
    """하루치 페이지에서 식당별 메뉴 레코드 목록을 만듭니다.

    반환 각 항목: {date, weekday, restaurant, menu_text, is_closed}.
    페이지가 요청 날짜와 다르면(파라미터 무시 등) 빈 목록을 반환해 오염을 막는다.
    """
    soup = make_soup(html)
    table = _find_menu_table(soup)
    if table is None:
        return []

    full_text = table.get_text("\n", strip=True)
    if not _verify_page_date(full_text, target):
        return []

    # 안내 문구(※...) 이후는 메뉴가 아니므로 제거.
    full_text = NOTICE_SPLIT_PATTERN.split(full_text)[0]

    # 식당 앵커 위치로 텍스트를 식당별 블록으로 분할(문서 순서 유지).
    positions: List[tuple[int, str]] = []
    for anchor in RESTAURANT_ANCHORS:
        match = re.search(re.escape(anchor), full_text)
        if match:
            positions.append((match.start(), anchor))
    positions.sort()

    weekday_ko = WEEKDAY_KO[target.weekday()]
    records: List[dict] = []
    for idx, (start, anchor) in enumerate(positions):
        end = positions[idx + 1][0] if idx + 1 < len(positions) else len(full_text)
        block = full_text[start:end]
        # 줄바꿈/중복 공백 정리.
        block = re.sub(r"\s*\n\s*", " ", block)
        block = re.sub(r"\s{2,}", " ", block).strip()
        # 식당명 잔여 접두("누리터 식당(일산캠퍼스)" 등)를 제거하고 첫 메뉴 섹션부터 본문으로 삼는다.
        # 섹션 마커: 조식/중식/중·석식/구분 중 가장 먼저 등장하는 지점.
        body = block[len(anchor):].strip() if block.startswith(anchor) else block
        section_match = re.search(r"(조식|중식|중·석식|구분)", body)
        if section_match:
            body = body[section_match.start():].strip()
        else:
            body = re.sub(r"^\([^)]*\)\s*", "", body).strip()

        # 가격이 하나도 없고 '휴무'가 있거나 본문이 사실상 비면 휴무로 판정.
        has_price = bool(PRICE_PATTERN.search(body))
        is_closed = (not has_price) and (bool(CLOSED_KEYWORD.search(body)) or len(body) < 4)
        records.append(
            {
                "date": target.strftime("%Y-%m-%d"),
                "weekday": weekday_ko,
                "restaurant": RESTAURANT_LABELS.get(anchor, anchor),
                "menu_text": "휴무" if is_closed else body,
                "is_closed": is_closed,
            }
        )
    return records


def _infer_year(month: int, day: int, ref: date) -> Optional[date]:
    """월/일에 연도를 붙인다. 게시일(ref) 기준으로 연말연초 경계를 보정한다."""
    year = ref.year
    if ref.month == 12 and month == 1:
        year += 1
    elif ref.month == 1 and month == 12:
        year -= 1
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _dflex_section_label(raw: str) -> str:
    """D-Flex 구분 셀(세로쓰기로 깨진 텍스트)에서 끼니/코너 라벨을 키워드로 추출한다."""
    joined = re.sub(r"\s+", "", raw or "")
    price = ""
    price_match = re.search(r"\d{1,3},\d{3}\s*원", raw or "")
    if price_match:
        price = " " + price_match.group(0).replace(" ", "")
    if "석식" in joined:
        return f"석식{price}".strip()
    if "B코너" in joined or ("B" in joined and "코너" in joined):
        return f"중식 B코너{price}".strip()
    if "A코너" in joined or ("A" in joined and "코너" in joined):
        return f"중식 A코너{price}".strip()
    if "특별" in joined:
        return f"중식 특별코너{price}".strip()
    if "일반" in joined:
        return "일반(뚝배기)"
    if "중식" in joined:
        return f"중식{price}".strip()
    return ""


def parse_dflex_pdf(pdf_bytes: bytes, ref_date: date) -> List[dict]:
    """D-Flex 주간 식단표 PDF(텍스트 레이어)를 요일별 메뉴 레코드로 변환한다.

    PDF는 (행=구분/코너, 열=요일) 표 구조라, 날짜 열을 찾아 열 단위로 메뉴를 모은다.
    반환 각 항목: {date, weekday, restaurant, menu_text, is_closed}.
    """
    try:
        import pdfplumber
    except ImportError:
        print("⚠️ pdfplumber 미설치 — D-Flex PDF 파싱을 건너뜁니다(requirements.txt 확인).")
        return []

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            tables = pdf.pages[0].extract_tables() if pdf.pages else []
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ D-Flex PDF 파싱 실패: {exc}")
        return []
    if not tables:
        return []
    table = tables[0]

    # 날짜가 들어 있는 열(요일 컬럼) 찾기.
    date_cols: dict[int, date] = {}
    for row in table[:3]:
        for ci, cell in enumerate(row):
            if not cell:
                continue
            m = DFLEX_PDF_DATE_PATTERN.search(cell)
            if m and ci not in date_cols:
                d = _infer_year(int(m.group(1)), int(m.group(2)), ref_date)
                if d is not None:
                    date_cols[ci] = d
    if not date_cols:
        return []

    records: List[dict] = []
    for ci, day in sorted(date_cols.items()):
        segments: List[str] = []
        for row in table:
            cell = (row[ci] or "").replace("\n", " ").strip()
            cell = re.sub(r"\s{2,}", " ", cell)
            if not cell or DFLEX_PDF_DATE_PATTERN.search(cell) or cell in _DFLEX_WEEKDAY_WORDS:
                continue
            label = _dflex_section_label(" ".join((row[i] or "") for i in range(min(4, len(row)))))
            segments.append(f"[{label}] {cell}" if label else cell)
        # 같은 셀이 일반/석식 등에서 반복될 수 있어 순서 보존 중복 제거.
        seen: set[str] = set()
        deduped = [s for s in segments if not (s in seen or seen.add(s))]
        menu_text = " / ".join(deduped).strip()
        if not menu_text:
            continue
        records.append(
            {
                "date": day.strftime("%Y-%m-%d"),
                "weekday": WEEKDAY_KO[day.weekday()],
                "restaurant": DFLEX_RESTAURANT,
                "menu_text": menu_text,
                "is_closed": False,
            }
        )
    return records


def crawl_dflex_meals(
    *,
    max_posts: int = 3,
    delay: float = DEFAULT_REQUEST_DELAY,
) -> List[dict]:
    """D-Flex 게시판 최근 주간 식단표 글들의 PDF를 받아 요일별 메뉴 레코드를 만든다.

    이미지가 아닌 PDF 첨부(텍스트 레이어)를 사용하므로 OCR이 필요 없다.
    공지 크롤러(fetch_notice_list/detail)를 재사용한다.
    """
    try:
        from src.crawlers.dongguk_notices import fetch_notice_list, fetch_notice_detail
    except ImportError as exc:
        print(f"⚠️ D-Flex 크롤 불가(공지 크롤러 임포트 실패): {exc}")
        return []

    try:
        posts = fetch_notice_list(DFLEX_BOARD_CODE, page=1)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ D-Flex 목록 수집 실패: {exc}")
        return []

    records: List[dict] = []
    for meta in posts[:max_posts]:
        article_id = meta.get("article_id")
        if article_id is None:
            continue
        try:
            detail = fetch_notice_detail(DFLEX_BOARD_CODE, article_id)
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ D-Flex 상세 수집 실패 (article_id={article_id}): {exc}")
            continue

        ref_date = detail.get("posted_at") or meta.get("posted_at") or datetime.now(KST).date()
        pdf_atts = [a for a in detail.get("attachments", []) if str(a.get("name", "")).lower().endswith(".pdf")]
        if not pdf_atts:
            if delay:
                time.sleep(delay)
            continue
        try:
            resp = requests.get(pdf_atts[0]["url"], headers=HEADERS, timeout=40)
            resp.raise_for_status()
            records.extend(parse_dflex_pdf(resp.content, ref_date))
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ D-Flex PDF 다운로드/파싱 실패 (article_id={article_id}): {exc}")
        if delay:
            time.sleep(delay)

    return records


def crawl_meals(
    *,
    days_back: int | None = None,
    days_ahead: int = 13,
    delay: float = DEFAULT_REQUEST_DELAY,
    today: date | None = None,
    include_dflex: bool = True,
) -> pd.DataFrame:
    """이번 주 월요일(기본)부터 향후 days_ahead일까지 식단을 수집합니다.

    days_back을 주면 today 기준 과거 days_back일부터 수집한다(미지정 시 이번 주 월요일 시작).
    """
    today = today or datetime.now(KST).date()
    if days_back is not None:
        start = today - timedelta(days=days_back)
    else:
        start = today - timedelta(days=today.weekday())  # 이번 주 월요일
    end = today + timedelta(days=days_ahead)

    records: List[dict] = []
    failed = 0
    cur = start
    while cur <= end:
        try:
            html = fetch_day_html(cur)
            day_records = parse_day_menus(html, cur)
            records.extend(day_records)
        except Exception as exc:  # noqa: BLE001 — 하루 실패가 전체 수집을 막지 않도록
            failed += 1
            print(f"⚠️ 식단 수집 실패 ({cur}): {exc}")
        if delay:
            time.sleep(delay)
        cur += timedelta(days=1)

    if failed:
        print(f"⚠️ 식단 수집 실패 {failed}일치 (수집 레코드 {len(records)}건)")

    # D-Flex(경영관) 주간 PDF 식단표 병합 — 수집 윈도우([start, end]) 안의 날짜만.
    if include_dflex:
        try:
            dflex_records = crawl_dflex_meals(delay=delay)
            for rec in dflex_records:
                try:
                    rec_date = datetime.strptime(rec["date"], "%Y-%m-%d").date()
                except (ValueError, KeyError):
                    continue
                if start <= rec_date <= end:
                    records.append(rec)
        except Exception as exc:  # noqa: BLE001 — D-Flex 실패가 전체 수집을 막지 않도록
            print(f"⚠️ D-Flex 식단 병합 실패: {exc}")

    columns = ["date", "weekday", "restaurant", "menu_text", "is_closed"]
    if not records:
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(records)[columns]
    # (날짜, 식당) 중복 제거 후 정렬.
    df.drop_duplicates(subset=["date", "restaurant"], keep="first", inplace=True)
    df.sort_values(by=["date", "restaurant"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


__all__ = [
    "crawl_meals",
    "crawl_dflex_meals",
    "parse_dflex_pdf",
    "fetch_day_html",
    "parse_day_menus",
    "MEALS_URL",
]


def main() -> None:
    from pathlib import Path

    output_path = Path(__file__).resolve().parents[2] / "data" / "dongguk_meals.csv"
    df = crawl_meals()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"✅ {len(df)} meal rows saved to {output_path}")


if __name__ == "__main__":
    main()
