"""동국대 학칙 HWP 파일을 일괄 추출하는 스크립트입니다."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET

import pandas as pd

from src.config import BASE_DIR, DATA_DIR

RULE_ROOT = BASE_DIR / "dongguk_rule"
OUTPUT_PATH = DATA_DIR / "dongguk_rule_texts.csv"


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


def main() -> None:
    hwp_paths = list_hwp_files(RULE_ROOT)
    print(f"총 {len(hwp_paths)}개의 HWP 파일 발견")

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
