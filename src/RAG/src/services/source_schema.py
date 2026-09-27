"""Stable upstream-structure fingerprints and durable change observations."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any, Iterable
import zipfile
from xml.etree import ElementTree

import pandas as pd
from sqlalchemy.orm import Session

from src.database import SourceSchemaFingerprint, kst_now


STRUCTURE_SCHEMA_VERSION = 1
_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(structure: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(structure).encode("utf-8")).hexdigest()


def _clean(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _value_kinds(series: pd.Series) -> list[str]:
    kinds: set[str] = set()
    for value in series.dropna().head(200):
        if isinstance(value, dict):
            kinds.add("object")
        elif isinstance(value, (list, tuple, set)):
            kinds.add("array")
        elif isinstance(value, bool):
            kinds.add("boolean")
        elif isinstance(value, (int, float)):
            kinds.add("number")
        elif _clean(value):
            kinds.add("string")
    return sorted(kinds) or ["empty"]


@dataclass(frozen=True)
class SourceStructure:
    source_name: str
    source_format: str
    fingerprint: str
    structure: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def fingerprint_dataframe(
    frame: pd.DataFrame,
    *,
    source_name: str,
    source_format: str,
) -> SourceStructure:
    columns = [
        {
            "name": str(column),
            "dtype": str(frame[column].dtype),
            "value_kinds": _value_kinds(frame[column]),
        }
        for column in frame.columns
    ]
    structure = {
        "schema_version": STRUCTURE_SCHEMA_VERSION,
        "kind": "tabular",
        "columns": columns,
    }
    return SourceStructure(source_name, source_format, _fingerprint(structure), structure)


class _StructureHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.paths: set[str] = set()
        self.attributes: dict[str, set[str]] = {}
        self.semantic_attributes: dict[str, set[str]] = {}
        self._in_th = False
        self._th_parts: list[str] = []
        self.headers: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        path = "/".join((self.stack + [normalized])[-6:])
        self.paths.add(path)
        names = {str(name).lower() for name, _ in attrs}
        self.attributes.setdefault(normalized, set()).update(names)
        for name, value in attrs:
            if name.lower() in {"name", "role", "type", "itemprop"} and value:
                token = re.sub(r"\s+", " ", str(value).strip().lower())
                if token:
                    self.semantic_attributes.setdefault(f"{normalized}:{name.lower()}", set()).add(token)
        if normalized == "th":
            self._in_th = True
            self._th_parts = []
        if normalized not in _VOID_TAGS:
            self.stack.append(normalized)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized == "th" and self._in_th:
            header = re.sub(r"\s+", " ", " ".join(self._th_parts)).strip()
            if header:
                self.headers.add(header)
            self._in_th = False
            self._th_parts = []
        if normalized in self.stack:
            index = len(self.stack) - 1 - self.stack[::-1].index(normalized)
            del self.stack[index:]

    def handle_data(self, data: str) -> None:
        if self._in_th and data.strip():
            self._th_parts.append(data.strip())


def fingerprint_html(markup: str, *, source_name: str) -> SourceStructure:
    parser = _StructureHTMLParser()
    parser.feed(str(markup or ""))
    structure = {
        "schema_version": STRUCTURE_SCHEMA_VERSION,
        "kind": "html",
        "tag_paths": sorted(parser.paths),
        "attribute_names": {
            tag: sorted(names) for tag, names in sorted(parser.attributes.items())
        },
        "semantic_attributes": {
            key: sorted(values) for key, values in sorted(parser.semantic_attributes.items())
        },
        "table_headers": sorted(parser.headers),
    }
    return SourceStructure(source_name, "html", _fingerprint(structure), structure)


def fingerprint_tabular_file(path: Path, *, source_name: str) -> list[SourceStructure]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        frame = pd.read_csv(path, nrows=200)
        return [fingerprint_dataframe(frame, source_name=source_name, source_format="csv")]
    if suffix == ".xlsx":
        return _fingerprint_xlsx(path, source_name=source_name)
    if suffix == ".xls":
        workbook = pd.ExcelFile(path)
        return [
            fingerprint_dataframe(
                pd.read_excel(workbook, sheet_name=sheet, nrows=200),
                source_name=f"{source_name}:{sheet}",
                source_format="xls",
            )
            for sheet in workbook.sheet_names
        ]
    raise ValueError(f"unsupported tabular source format: {path.suffix}")


def _fingerprint_xlsx(path: Path, *, source_name: str) -> list[SourceStructure]:
    """Read sheet names and header cells directly from the XLSX ZIP container."""
    main_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{{{main_ns}}}si"):
                shared.append("".join(node.text or "" for node in item.iter(f"{{{main_ns}}}t")))

        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            item.attrib["Id"]: item.attrib["Target"]
            for item in relationships.findall(f"{{{package_rel_ns}}}Relationship")
        }
        results: list[SourceStructure] = []
        for sheet in workbook.findall(f".//{{{main_ns}}}sheet"):
            sheet_name = str(sheet.attrib.get("name") or "sheet")
            relation_id = sheet.attrib.get(f"{{{rel_ns}}}id")
            target = targets.get(str(relation_id), "")
            member = target.lstrip("/")
            if not member.startswith("xl/"):
                member = f"xl/{member}"
            xml = ElementTree.fromstring(archive.read(member))
            first_row = xml.find(f".//{{{main_ns}}}sheetData/{{{main_ns}}}row")
            headers: list[str] = []
            if first_row is not None:
                for cell in first_row.findall(f"{{{main_ns}}}c"):
                    kind = cell.attrib.get("t")
                    value = cell.find(f"{{{main_ns}}}v")
                    if kind == "inlineStr":
                        header = "".join(
                            node.text or "" for node in cell.iter(f"{{{main_ns}}}t")
                        )
                    elif value is None or value.text is None:
                        header = ""
                    elif kind == "s":
                        try:
                            header = shared[int(value.text)]
                        except (IndexError, ValueError):
                            header = value.text
                    else:
                        header = value.text
                    headers.append(_clean(header))
            structure = {
                "schema_version": STRUCTURE_SCHEMA_VERSION,
                "kind": "tabular",
                "sheet_name": sheet_name,
                "columns": [
                    {"name": header, "dtype": "xlsx_cell", "value_kinds": ["unknown"]}
                    for header in headers
                ],
            }
            results.append(
                SourceStructure(
                    f"{source_name}:{sheet_name}",
                    "xlsx",
                    _fingerprint(structure),
                    structure,
                )
            )
        return results


def observe_source_structures(
    session: Session,
    *,
    dataset: str,
    ingestion_run_id: int | None,
    structures: Iterable[SourceStructure],
) -> list[dict[str, Any]]:
    """Upsert observations and return compact change diagnostics."""
    now = kst_now()
    reports: list[dict[str, Any]] = []
    for item in structures:
        current = (
            session.query(SourceSchemaFingerprint)
            .filter(
                SourceSchemaFingerprint.dataset == dataset,
                SourceSchemaFingerprint.source_name == item.source_name,
                SourceSchemaFingerprint.is_current.is_(True),
            )
            .order_by(SourceSchemaFingerprint.last_seen_at.desc())
            .first()
        )
        previous = current.fingerprint if current is not None else None
        changed = previous is not None and previous != item.fingerprint
        if changed:
            session.query(SourceSchemaFingerprint).filter(
                SourceSchemaFingerprint.dataset == dataset,
                SourceSchemaFingerprint.source_name == item.source_name,
                SourceSchemaFingerprint.is_current.is_(True),
            ).update({"is_current": False}, synchronize_session=False)

        row = (
            session.query(SourceSchemaFingerprint)
            .filter(
                SourceSchemaFingerprint.dataset == dataset,
                SourceSchemaFingerprint.source_name == item.source_name,
                SourceSchemaFingerprint.fingerprint == item.fingerprint,
            )
            .one_or_none()
        )
        if row is None:
            row = SourceSchemaFingerprint(
                dataset=dataset,
                source_name=item.source_name,
                source_format=item.source_format,
                fingerprint=item.fingerprint,
                structure_json=_canonical_json(item.structure),
                first_seen_at=now,
                observation_count=0,
            )
            session.add(row)
        row.source_format = item.source_format
        row.structure_json = _canonical_json(item.structure)
        row.last_seen_at = now
        row.observation_count = int(row.observation_count or 0) + 1
        row.is_current = True
        row.last_ingestion_run_id = ingestion_run_id
        reports.append(
            {
                "source_name": item.source_name,
                "source_format": item.source_format,
                "fingerprint": item.fingerprint,
                "previous_fingerprint": previous,
                "changed": changed,
                "baseline_created": previous is None,
            }
        )
    session.flush()
    return reports


__all__ = [
    "SourceStructure",
    "fingerprint_dataframe",
    "fingerprint_html",
    "fingerprint_tabular_file",
    "observe_source_structures",
]
