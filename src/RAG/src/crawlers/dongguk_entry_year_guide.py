"""학번별 학사제도/학업이수 가이드 PDF를 rules 보조 데이터로 정규화합니다."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List

import pandas as pd
from pypdf import PdfReader

from src.config import DATA_DIR

PDF_GLOB = "*_edu.pdf"
OUTPUT_PATH = DATA_DIR / "dongguk_entry_year_guide_sections.csv"
SOURCE_TYPE = "entry_year_guide_pdf"
SECTION_PATTERNS: Dict[str, re.Pattern[str]] = {
    "수강신청 및 수업관련제도": re.compile(r"^\s*[ⅡII2]+\s*[.\-]?\s*수강신청.*수업\s*관련\s*제도"),
    "학적 및 학생 관련": re.compile(r"^\s*[ⅢIII3]+\s*[.\-]?\s*학적.*학생\s*관련"),
    "교양교육과정 이수 기준": re.compile(r"^\s*[ⅣIV4]+\s*[.\-]?\s*교양\s*교육\s*과정.*이수\s*기준"),
    "단과대학별 졸업기준": re.compile(r"^\s*[ⅤV5]+\s*[.\-]?\s*단과대학별\s*졸업\s*기준"),
}
DEFAULT_COLLEGES = [
    "불교대학",
    "문과대학",
    "이과대학",
    "법과대학",
    "사회과학대학",
    "경찰사법대학",
    "경영대학",
    "바이오시스템대학",
    "공과대학",
    "사범대학",
    "예술대학",
    "약학대학",
    "미래융합대학",
    "첨단융합대학",
]
NOISE_LINE_PATTERNS = [
    re.compile(r"^\s*\d+\s*$"),
    re.compile(r"^\s*동국대학교\s*$"),
    re.compile(r"^\s*목차\s*$"),
]
# "Ⅴ. 단과대학별 졸업기준 ▶▶" 마커. 구 PDF(2022~2025)에선 이 마커가 페이지번호에 붙어
# 줄 중간(예: "117Ⅴ. 단과대학별 졸업기준")에 나와 섹션 검출에서 누락된다. 그 결과 약학대(215학점) 등
# 단과대별 졸업기준표가 직전 섹션(교양/학적)에 흡수되므로, 본문 어디서든 이 마커를 찾아 분리한다.
GRADUATION_MARKER = re.compile(r"[ⅤV]\s*[.\-]?\s*단과대학별\s*졸업\s*기준\s*▶*")
GRADUATION_SECTION = "단과대학별 졸업기준"


def _normalize_line(line: str) -> str:
    line = line.replace("\x00", " ")
    line = re.sub(r"[ \t]+", " ", line)
    return line.strip()


def _normalize_text_block(text: str) -> str:
    lines = [_normalize_line(line) for line in text.splitlines()]
    kept: list[str] = []
    previous = ""
    for line in lines:
        if not line:
            if previous:
                kept.append("")
                previous = ""
            continue
        if any(pattern.match(line) for pattern in NOISE_LINE_PATTERNS):
            continue
        if line == previous:
            continue
        kept.append(line)
        previous = line

    text = "\n".join(kept)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_entry_year(path: Path) -> int:
    match = re.search(r"(20\d{2})", path.stem)
    if not match:
        raise ValueError(f"Could not infer entry year from filename: {path.name}")
    return int(match.group(1))


def _read_pdf_pages(path: Path) -> List[dict]:
    reader = PdfReader(str(path))
    pages: list[dict] = []
    for index, page in enumerate(reader.pages, start=1):
        text = _normalize_text_block(page.extract_text() or "")
        if not text:
            continue
        pages.append({"page_number": index, "text": text})
    return pages


def _match_section(line: str) -> str | None:
    for section_name, pattern in SECTION_PATTERNS.items():
        if pattern.search(line):
            return section_name
    return None


def _looks_like_table_of_contents(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    if any(line == "목차" for line in lines[:5]):
        return True

    numbered_lines = sum(1 for line in lines[:20] if re.search(r"\d+\s*$", line))
    short_lines = sum(1 for line in lines[:20] if len(line) <= 40)
    return numbered_lines >= 6 and short_lines >= 6


def _segment_pages_by_section(pages: List[dict]) -> List[dict]:
    records: list[dict] = []
    current: dict | None = None

    for page in pages:
        lines = page["text"].splitlines()
        matched_section = None
        matched_index = -1
        for index, line in enumerate(lines[:20]):
            section_name = _match_section(line)
            if section_name:
                matched_section = section_name
                matched_index = index
                break

        if matched_section:
            if _looks_like_table_of_contents(page["text"]):
                continue
            if current and current["text_parts"]:
                records.append(current)
            current = {
                "section": matched_section,
                "page_start": page["page_number"],
                "page_end": page["page_number"],
                "text_parts": [],
            }
            remaining = "\n".join(lines[matched_index:])
            if remaining.strip():
                current["text_parts"].append(remaining.strip())
            continue

        if current is not None:
            current["page_end"] = page["page_number"]
            current["text_parts"].append(page["text"])

    if current and current["text_parts"]:
        records.append(current)
    return records


def _load_college_departments() -> Dict[str, List[str]]:
    """단과대 → 소속 학과 목록. 졸업기준 청크 제목에 학과명을 붙여
    '통계학과 졸업요건'처럼 학과명으로 물어도 해당 단과대 청크가 검색되게 한다."""
    mapping: Dict[str, List[str]] = {}
    catalog_path = DATA_DIR / "dongguk_departments_catalog.csv"
    if catalog_path.exists():
        try:
            catalog = pd.read_csv(catalog_path).fillna("").astype(str)
            for _, row in catalog.iterrows():
                college = str(row.get("college_name", "")).strip()
                dept = str(row.get("department_name", "")).strip()
                if college and dept and college != "대학":
                    mapping.setdefault(college, [])
                    if dept not in mapping[college]:
                        mapping[college].append(dept)
        except Exception:
            pass
    return mapping


def _load_known_colleges() -> List[str]:
    colleges = set(DEFAULT_COLLEGES)
    catalog_path = DATA_DIR / "dongguk_departments_catalog.csv"
    if catalog_path.exists():
        try:
            catalog = pd.read_csv(catalog_path).fillna("").astype(str)
            for value in catalog.get("college_name", pd.Series(dtype=str)).tolist():
                cleaned = str(value).strip()
                if cleaned and cleaned != "대학" and "대학" in cleaned:
                    colleges.add(cleaned)
        except Exception:
            pass
    return sorted(colleges, key=len, reverse=True)


def _split_graduation_section_by_college(text: str, colleges: Iterable[str]) -> List[dict]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    colleges = list(colleges)  # 긴 이름 우선(바이오시스템대학 > 공과대학) 순서 보존
    # 헤더 형태 1: 단과대명이 줄 전체와 일치 (예: "약학대학", "[약학대학]")
    exact_patterns = {c: re.compile(rf"^(?:\[\s*)?{re.escape(c)}(?:\s*\])?$") for c in colleges}
    # 헤더 형태 2: "단과대명 (전화번호)" — PDF 표에서 가장 흔한 단과대 구획 헤더.
    #   전화번호가 0으로 시작하지 않는 내선(예: 2260-8963)도 있어 숫자 시작으로만 일반화한다.
    #   "경영대학 DBA2006 …"처럼 본문 줄은 괄호+숫자가 아니므로 헤더로 오인하지 않는다.
    phone_patterns = {c: re.compile(rf"^{re.escape(c)}\s*[\(（]\s*\d") for c in colleges}

    def match_college(line: str) -> str | None:
        for c in colleges:
            if exact_patterns[c].match(line) or phone_patterns[c].match(line):
                return c
        return None

    records: list[dict] = []
    current_college = ""
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_lines
        cleaned = _normalize_text_block("\n".join(current_lines))
        if cleaned:
            records.append({"college_name": current_college, "text": cleaned})
        current_lines = []

    for line in lines:
        matched_college = match_college(line)
        if matched_college:
            if current_lines:
                flush()
            current_college = matched_college
            current_lines = [line]
            continue
        current_lines.append(line)

    if current_lines:
        flush()
    return records


def _emit_graduation_records(
    text: str,
    entry_year: int,
    page_start: int,
    page_end: int,
    path: Path,
    published_at: str,
    colleges: Iterable[str],
) -> List[Dict[str, str]]:
    """졸업기준 블록을 단과대별로 쪼개 레코드로 만든다(분리 실패 시 통째로 1건)."""
    college_records = _split_graduation_section_by_college(text, colleges)
    if not college_records:
        college_records = [{"college_name": "", "text": text}]

    college_departments = _load_college_departments()
    out: list[dict[str, str]] = []
    for item in college_records:
        college_name = item["college_name"]
        title = f"{entry_year}학번 {GRADUATION_SECTION}"
        if college_name:
            title += f" - {college_name}"
            # 소속 학과명을 제목에 주입(제목은 모든 서브청크 앞에 붙으므로 학과명 검색이 가능해짐)
            depts = college_departments.get(college_name)
            if depts:
                title += " (소속 학과: " + ", ".join(depts[:8]) + ")"
        out.append(
            {
                "entry_year": str(entry_year),
                "section": GRADUATION_SECTION,
                "college_name": college_name,
                "title": title,
                "text": item["text"],
                "source_type": SOURCE_TYPE,
                "source_file": path.name,
                "published_at": published_at,
                "page_start": str(page_start),
                "page_end": str(page_end),
                "relative_dir": "entry_year_guides",
                "filename": path.name,
            }
        )
    return out


def _build_records_for_pdf(path: Path) -> List[Dict[str, str]]:
    entry_year = _extract_entry_year(path)
    pages = _read_pdf_pages(path)
    sections = _segment_pages_by_section(pages)
    colleges = _load_known_colleges()
    published_at = f"{entry_year}-01-01"
    records: list[dict[str, str]] = []

    # 졸업기준 블록은 (a)정상 검출된 섹션과 (b)다른 섹션에 흡수된 마커 이후 꼬리에 나뉘어
    # 들어올 수 있고(예: 2026은 앞쪽 단과대가 교양 섹션에, 뒤쪽은 졸업기준 섹션에 분산), 둘은
    # 상보적이므로 모두 모아 한 번에 단과대별로 분리한다. 중복은 상위에서 drop_duplicates로 제거.
    grad_parts: list[str] = []
    grad_page_start: int | None = None
    grad_page_end: int = 0

    def _track_grad(part: str, page_start: int, page_end: int) -> None:
        nonlocal grad_page_start, grad_page_end
        grad_parts.append(part)
        grad_page_start = page_start if grad_page_start is None else min(grad_page_start, page_start)
        grad_page_end = max(grad_page_end, page_end)

    for section in sections:
        section_name = section["section"]
        text = _normalize_text_block("\n\n".join(section["text_parts"]))
        if not text:
            continue

        if section_name == GRADUATION_SECTION:
            _track_grad(text, section["page_start"], section["page_end"])
            continue

        # 다른 섹션에 졸업기준 블록이 섞여 들어온 경우: 마커 앞부분만 원 섹션에 남기고 뒤는 분리.
        marker = GRADUATION_MARKER.search(text)
        if marker:
            _track_grad(text[marker.start():], section["page_start"], section["page_end"])
            text = _normalize_text_block(text[: marker.start()])
            if not text:
                continue

        records.append(
            {
                "entry_year": str(entry_year),
                "section": section_name,
                "college_name": "",
                "title": f"{entry_year}학번 {section_name}",
                "text": text,
                "source_type": SOURCE_TYPE,
                "source_file": path.name,
                "published_at": published_at,
                "page_start": str(section["page_start"]),
                "page_end": str(section["page_end"]),
                "relative_dir": "entry_year_guides",
                "filename": path.name,
            }
        )

    if grad_parts:
        merged = _normalize_text_block("\n".join(grad_parts))
        records.extend(
            _emit_graduation_records(
                merged, entry_year, grad_page_start or 0, grad_page_end, path, published_at, colleges
            )
        )

    return records


def build_entry_year_guide_dataframe() -> pd.DataFrame:
    records: list[dict[str, str]] = []
    for path in sorted(DATA_DIR.glob(PDF_GLOB)):
        # 파일명 연도 추출 실패·PDF 파싱 오류가 다른 연도 가이드 수집까지 막지 않도록 격리
        try:
            records.extend(_build_records_for_pdf(path))
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ PDF 처리 실패로 건너뜀: {path.name} ({exc})")
    df = pd.DataFrame(records)
    if not df.empty:
        df.drop_duplicates(subset=["entry_year", "section", "college_name", "text"], inplace=True)
        df.reset_index(drop=True, inplace=True)
    return df


def main() -> None:
    df = build_entry_year_guide_dataframe()
    if df.empty:
        raise RuntimeError("No entry-year guide sections were extracted.")
    df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(f"saved {len(df)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
