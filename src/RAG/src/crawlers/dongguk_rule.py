"""One-off exporter for Dongguk University rulebook HWP files."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET

import pandas as pd

try:
    import olefile  # type: ignore
except ImportError:  # pragma: no cover
    olefile = None

RULE_ROOT = Path("dongguk_rule")
OUTPUT_PATH = Path("./data/dongguk_rule_texts.csv")


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


def extract_text_from_ole_hwp(path: Path) -> Optional[str]:
    if olefile is None:
        return None
    try:
        with olefile.OleFileIO(path) as ole:
            if ole.exists("PrvText"):
                stream = ole.openstream("PrvText")
                data = stream.read()
                if data:
                    return data.decode("utf-16-le", errors="ignore")
    except OSError:
        return None
    return None


def extract_text_from_hwp(path: Path) -> Tuple[str, Optional[str], List[str]]:
    failures: List[str] = []
    text = extract_text_from_zip_hwp(path)
    if text:
        return "zip", text, failures

    text = extract_text_from_ole_hwp(path)
    if text:
        return "ole", text, failures

    if olefile is None:
        failures.append("olefile_not_installed")
    failures.append("unsupported_format")
    return "unknown", None, failures


def summarise_relative_path(path: Path, root: Path) -> Tuple[str, str]:
    rel_path = path.relative_to(root)
    parent = str(rel_path.parent)
    return parent, rel_path.name


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def main() -> None:
    hwp_paths = list_hwp_files(RULE_ROOT)
    print(f"총 {len(hwp_paths)}개의 HWP 파일 발견")
    if olefile is None:
        print("⚠️ olefile 라이브러리가 설치되어 있지 않습니다. 구형 HWP 파일은 텍스트를 추출하지 못할 수 있습니다.")

    records: List[Dict[str, object]] = []
    failure_details: List[Dict[str, object]] = []

    for idx, path in enumerate(hwp_paths, start=1):
        method, text, failures = extract_text_from_hwp(path)
        rel_dir, filename = summarise_relative_path(path, RULE_ROOT)

        cleaned = clean_text(text) if text else ""
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
