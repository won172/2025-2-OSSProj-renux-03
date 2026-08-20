"""동국대 학칙 HWP와 공식 규정관리시스템 현행판을 정규화한다."""
from __future__ import annotations

from datetime import date
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET

import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.config import BASE_DIR, DATA_DIR

RULE_ROOT = BASE_DIR / "dongguk_rule"
OUTPUT_PATH = DATA_DIR / "dongguk_rule_texts.csv"
OFFICIAL_RULE_BASE_URL = "https://rule.dongguk.edu"
ACADEMIC_RULE_LIST_URL = (
    f"{OFFICIAL_RULE_BASE_URL}/lmxsrv/law/lawListManager.srv"
    "?LAWGROUP=1&PAGE_MODE=&SEQ=6"
)
_LIST_ID_RE = re.compile(r"fullPopupPost\((\d+)\s*,\s*(\d+)")
_LIST_DATE_RE = re.compile(r"showDate\('(\d{4})(\d{2})(\d{2})'")
_RULE_CODE_RE = re.compile(r"\b(\d+-\d+-\d+)\b")


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
        if date_match is None:
            continue
        published_at = date(*map(int, date_match.groups())).isoformat()
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


def collect_official_academic_rules(
    *,
    timeout: float = 20.0,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """제2편 제1장(대학)의 공식 현행 규정 전체를 수집한다."""
    client = session or requests.Session()
    response = client.get(ACADEMIC_RULE_LIST_URL, timeout=timeout)
    response.raise_for_status()
    rows: List[Dict[str, str]] = []
    for item in parse_official_rule_list(response.text):
        detail = client.get(item["source_url"], timeout=timeout)
        detail.raise_for_status()
        parsed_title, text = parse_official_rule_content(detail.text)
        title = parsed_title or item["title"]
        version = item["published_at"].replace("-", ".")
        code_prefix = f"{item['rule_code']}. " if item["rule_code"] else ""
        rows.append(
            {
                "relative_dir": "제2편_학칙/제1장_대학/공식_현행",
                "filename": f"{code_prefix}{title}({version}.).html",
                "title": f"{code_prefix}{title}".strip(),
                "text": text,
                "source_type": "official_rule_web",
                "source_url": item["source_url"],
                "source_page_url": ACADEMIC_RULE_LIST_URL,
                "source_version": f"{item['seq']}:{item['seq_history']}",
                "source_file": "official_rule_web",
                "published_at": item["published_at"],
                "rule_code": item["rule_code"],
            }
        )
    return pd.DataFrame(rows)


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
