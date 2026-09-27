from __future__ import annotations

import pandas as pd
import zipfile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base, SourceSchemaFingerprint
from src.services.source_schema import (
    fingerprint_dataframe,
    fingerprint_html,
    fingerprint_tabular_file,
    observe_source_structures,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_dataframe_fingerprint_ignores_rows_but_detects_column_change():
    first = fingerprint_dataframe(
        pd.DataFrame([{"id": "1", "title": "가"}]),
        source_name="notices",
        source_format="html_projection",
    )
    other_rows = fingerprint_dataframe(
        pd.DataFrame([{"id": "2", "title": "나"}]),
        source_name="notices",
        source_format="html_projection",
    )
    changed = fingerprint_dataframe(
        pd.DataFrame([{"id": "2", "subject": "나"}]),
        source_name="notices",
        source_format="html_projection",
    )

    assert first.fingerprint == other_rows.fingerprint
    assert first.fingerprint != changed.fingerprint


def test_html_fingerprint_ignores_body_text_but_detects_table_header_change():
    first = fingerprint_html("<table><tr><th>제목</th><td>공지 A</td></tr></table>", source_name="schedule")
    other_text = fingerprint_html("<table><tr><th>제목</th><td>공지 B</td></tr></table>", source_name="schedule")
    changed = fingerprint_html("<table><tr><th>일정명</th><td>공지 B</td></tr></table>", source_name="schedule")

    assert first.fingerprint == other_text.fingerprint
    assert first.fingerprint != changed.fingerprint


def test_observation_versions_schema_and_marks_only_latest_current():
    session = _session()
    try:
        first = fingerprint_dataframe(
            pd.DataFrame([{"id": "1"}]), source_name="staff_api", source_format="json"
        )
        changed = fingerprint_dataframe(
            pd.DataFrame([{"id": "1", "phone": "02"}]), source_name="staff_api", source_format="json"
        )
        baseline = observe_source_structures(
            session, dataset="staff", ingestion_run_id=1, structures=[first]
        )
        second = observe_source_structures(
            session, dataset="staff", ingestion_run_id=2, structures=[changed]
        )
        session.commit()

        assert baseline[0]["baseline_created"] is True
        assert baseline[0]["changed"] is False
        assert second[0]["previous_fingerprint"] == first.fingerprint
        assert second[0]["changed"] is True
        rows = session.query(SourceSchemaFingerprint).order_by(SourceSchemaFingerprint.id).all()
        assert [row.is_current for row in rows] == [False, True]
    finally:
        session.close()


def test_xlsx_fingerprint_tracks_each_sheet_header(tmp_path):
    path = tmp_path / "sources.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="curriculum_links" sheetId="1" r:id="rId1"/><sheet name="metadata" sheetId="2" r:id="rId2"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c t="inlineStr"><is><t>학과</t></is></c><c t="inlineStr"><is><t>URL</t></is></c></row></sheetData></worksheet>',
        )
        archive.writestr(
            "xl/worksheets/sheet2.xml",
            '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c t="inlineStr"><is><t>상태</t></is></c></row></sheetData></worksheet>',
        )

    structures = fingerprint_tabular_file(path, source_name="curriculum_links")

    assert [item.source_name for item in structures] == [
        "curriculum_links:curriculum_links",
        "curriculum_links:metadata",
    ]
    assert all(item.source_format == "xlsx" for item in structures)
