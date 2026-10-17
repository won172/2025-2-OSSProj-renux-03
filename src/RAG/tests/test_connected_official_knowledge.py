"""Network-free source-to-sink and adversarial connected evidence contracts."""
from datetime import date
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import zipfile
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from src.database import SourceDocument
from src.collectors.official_extract import extract_html, extract_attachment
from src.collectors.official_public import Collector, Fetcher, Policy, Budgets, Response, CollectionError, DIRECTORY, SITEMAP
from src.pipelines.official_public import StagingWorkspace, load_policy, import_collection
from src.pipelines.canonical import canonical_hash, canonical_json
from src.services.connected_knowledge import rebuild_connected_projection, connected_search, read_only, ConnectedReader, Scope

@pytest.fixture
def workspace(tmp_path):
    w = StagingWorkspace(tmp_path / "staging")
    w.initialize()
    return w

def add_documents(w, documents):
    engine = create_engine("sqlite:///" + str(w.database))
    try:
        with Session(engine) as session:
            for dataset, source_id, data in documents:
                data = dict(data)
                status = data.pop("_status", "active")
                source_type = data.pop("_type", dataset)
                url = data.pop("_url", "https://www.dongguk.edu/page/" + source_id)
                session.add(SourceDocument(dataset=dataset, source_type=source_type, source_id=source_id,
                    document_key=dataset+":"+source_id, source_url=url, title=data.get("title", source_id),
                    status=status, content_hash=canonical_hash(data), normalized_payload_json=canonical_json(data)))
            session.commit()
    finally:
        engine.dispose()

def build(w):
    with sqlite3.connect(w.database) as db:
        return rebuild_connected_projection(db)

def competency_documents():
    return json.loads((Path(__file__).parent / "fixtures/connected_official/corpus.json").read_text())["documents"]

def test_all_types_semantics_provenance_and_path(workspace):
    add_documents(workspace, competency_documents())
    report = build(workspace)
    assert report["documents"] == len(competency_documents())
    assert set(report["datasets"]) >= {"meals", "rules", "notices", "guidance", "attachments", "other_future_dataset"}
    assert "other_future_dataset:orphan" in report["unlinked_documents"]
    with read_only(workspace.database) as db:
        assert db.execute("SELECT count(*) FROM connected_nodes WHERE kind='SourceDocument'").fetchone()[0] == len(competency_documents())
        predicates = {r[0] for r in db.execute("SELECT DISTINCT predicate FROM connected_edges")}
        assert {"OFFERED_BY", "WORKS_AT", "GOVERNS", "ATTACHMENT_OF", "LINKS_TO", "DESCRIBES"} <= predicates
        for e in db.execute("SELECT * FROM connected_edges"):
            assert e["document_key"] and e["locator"] and e["document_revision"]
            assert e["graph_revision"] == report["revision"] and e["review_status"] == "deterministic"
        reader = ConnectedReader(db, scope=Scope(campus="seoul", as_of=date(2026,10,17)))
        result = reader.inspect(["courses:course"], max_hops=4, max_edges=200, max_nodes=100)
        reached = {p["document_key"] for p in result["paths"]}
        assert {"courses:course", "rules:guide", "notices:notice", "staff:office", "guidance:page"} <= reached
        assert all(e["document_key"] != "guidance:private" for p in result["paths"] for e in p["path"])
    hits = connected_search(workspace.database, "졸업설계", scope=Scope(campus="seoul", as_of=date(2026,10,17)))["hits"]
    keys = {h["document_key"] for h in hits}
    assert {"guidance:page", "attachments:file"} <= keys
    assert not {"guidance:wise", "guidance:private", "guidance:old", "guidance:department"} & keys
    attachment = next(h for h in hits if h["document_key"] == "attachments:file")
    assert attachment["source_locator"].startswith("page/2") and attachment["url"].endswith("/file")

@pytest.mark.parametrize("field,value,scope,expected", [
    ("visibility", "private", Scope(), False),
    ("visibility", "department", Scope(department="컴퓨터공학과"), True),
    ("visibility", "department", Scope(department="다른학과"), False),
    ("audience", "graduate", Scope(audience="undergraduate"), False),
    ("entry_year", "2025", Scope(entry_year=2026), False),
    ("academic_year", "2025", Scope(academic_year=2026), False),
    ("semester", "1", Scope(semester="2"), False),
    ("campus_scope", "unknown", Scope(campus="seoul"), False),
    ("campus_scope", "wise", Scope(), False),
    ("effective_from", "2027-01-01", Scope(as_of=date(2026,10,17)), False),
    ("effective_to", "invalid", Scope(as_of=date(2026,10,17)), False),
])
def test_filter_boundaries(workspace, field, value, scope, expected):
    add_documents(workspace, [("guidance", "scope", {"title": "조건 안내", "content": "조건 검증 본문", "department": "컴퓨터공학과", field: value})])
    build(workspace)
    assert bool(connected_search(workspace.database, "조건", scope=scope)["hits"]) is expected

def test_revision_reproducible_meals_exact_date_and_obsolete_removal(workspace):
    add_documents(workspace, competency_documents())
    assert build(workspace) == build(workspace)
    assert connected_search(workspace.database, "비빔밥", scope=Scope(as_of=date(2026,10,17)))["hits"][0]["document_key"] == "meals:meal"
    assert not connected_search(workspace.database, "비빔밥", scope=Scope(as_of=date(2026,10,18)))["hits"]
    with sqlite3.connect(workspace.database) as db:
        db.execute("UPDATE source_documents SET status='hidden' WHERE document_key='meals:meal'")
    assert connected_search(workspace.database, "비빔밥")["status"] == "fallback"
    build(workspace)
    with read_only(workspace.database) as db:
        assert not db.execute("SELECT 1 FROM connected_nodes WHERE document_key='meals:meal'").fetchone()
        assert not db.execute("SELECT 1 FROM connected_edges WHERE document_key='meals:meal'").fetchone()

def test_campus_and_masked_person_identity(workspace):
    rows = []
    for campus in ("seoul", "wise"):
        rows.extend([("courses", campus, {"course_code": "CSE101", "department_name": "컴퓨터공학과", "campus_scope": campus}),
                     ("staff", campus, {"성명": "김**", "부서경로": "컴퓨터공학과", "campus_scope": campus})])
    add_documents(workspace, rows)
    build(workspace)
    with read_only(workspace.database) as db:
        assert db.execute("SELECT count(DISTINCT object) FROM connected_edges WHERE predicate='OFFERED_BY'").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM connected_nodes WHERE kind='Person'").fetchone()[0] == 2

def test_hidden_organization_does_not_resolve_public_notice(workspace):
    add_documents(workspace, [("staff", "hidden", {"성명": "김**", "부서경로": "비밀센터", "visibility": "private"}),
                             ("notices", "public", {"title": "[비밀센터] 안내", "department": "비밀센터", "content": "공개 원문"})])
    assert build(workspace)["unresolved_counts"]["unresolved_organization"] == 1
    with read_only(workspace.database) as db:
        result = ConnectedReader(db).inspect(["notices:public"])
        assert all(e["document_key"] == "notices:public" for p in result["paths"] for e in p["path"])
        assert not db.execute("SELECT 1 FROM connected_nodes WHERE kind='Person'").fetchone()

def policy():
    return Policy({"schema_version": 1, "approved_sources": [
        {"url": "https://www.dongguk.edu/", "owner": "동국대학교", "allow_descendants": True, "verification_url": SITEMAP, "seed": False},
        {"url": DIRECTORY, "owner": "디렉터리", "kind": "directory", "verification_url": DIRECTORY},
        {"url": SITEMAP, "owner": "사이트맵", "kind": "sitemap", "verification_url": SITEMAP}]})

def html(body, title="동국대학교 - 안내"):
    return f"<html><head><title>{title}</title></head><body><nav>global menu</nav><main>{body}</main></body></html>".encode()

def response(body):
    return Response(200, {"content-type": "text/html"}, body)

def fixture_collector(w, routes, budgets=None):
    p, calls = policy(), []
    budgets = budgets or Budgets(pages=20, per_host_seconds=.1)
    def transport(url, address, remaining):
        calls.append(url)
        value = routes.get(url, Response(404, {"content-type": "text/html"}, b"missing"))
        if isinstance(value, Exception):
            raise value
        return value
    fetcher = Fetcher(p, budgets, transport=transport, resolver=lambda host: ["93.184.216.34"], sleep=lambda _: None)
    return Collector(w.checkpoint, p, budgets, fetcher=fetcher), calls

def test_directory_external_frontier_resume_import_search(workspace):
    routes = {DIRECTORY: response(html('<a href="https://institution.example/">컴퓨터공학과 홈페이지</a><a href="/page/1">학사 안내</a>')),
              SITEMAP: response(html('<a href="/page/1">학사 안내</a><a href="/member/login">로그인</a>')),
              "https://www.dongguk.edu/page/1": response(html("<h2>신청 절차</h2><p>" + "졸업설계 공식 신청 안내 원문입니다. "*10 + "</p>"))}
    c, calls = fixture_collector(workspace, routes)
    report = c.run()
    assert report["states"]["requires_review"] == 2
    assert "https://institution.example/" not in calls and "https://www.dongguk.edu/member/login" not in calls
    candidate = next(s for s in report["sources"] if s["url"] == "https://institution.example/")
    assert candidate["owner"] == "컴퓨터공학과 홈페이지" and candidate["campus_scope"] == "unknown"
    before = len(calls)
    c.run()
    assert len(calls) == before
    c.close()
    assert len(import_collection(workspace)["new"]) == 1
    assert len(import_collection(workspace)["unchanged"]) == 1
    build(workspace)
    hit = connected_search(workspace.database, "졸업설계")["hits"][0]
    assert hit["dataset"] == "guidance" and hit["source_type"] == "official_public_page"

@pytest.mark.parametrize("destination", ["https://127.0.0.1/a", "https://unreviewed.example/a", "https://www.dongguk.edu/login", "http://www.dongguk.edu/page/1"])
def test_redirect_allowlist(workspace, destination):
    c, calls = fixture_collector(workspace, {DIRECTORY: Response(302, {"location": destination}, b"")})
    report = c.run()
    c.close()
    assert destination not in calls
    assert next(s for s in report["sources"] if s["url"] == DIRECTORY)["status"] == "failed"

def test_dns_and_byte_budgets():
    p, budgets = policy(), Budgets(response_bytes=100)
    f = Fetcher(p, budgets, resolver=lambda _: ["127.0.0.1"], transport=lambda *a: pytest.fail("private transport"))
    with pytest.raises(CollectionError, match="non-public"):
        f.fetch(DIRECTORY)
    f = Fetcher(p, budgets, resolver=lambda _: ["93.184.216.34"], transport=lambda *a: response(b"x"*101))
    with pytest.raises(CollectionError, match="byte budget"):
        f.fetch(DIRECTORY)

def test_retry_partial_no_deletion_and_receipt_tamper(workspace):
    add_documents(workspace, [("guidance", "existing", {"content": "기존 안전한 원문", "title": "기존"})])
    c, calls = fixture_collector(workspace, {DIRECTORY: RuntimeError("temporarily unavailable")})
    assert c.run()["states"]["failed"] == 1
    before = len(calls)
    c.run()
    assert len(calls) == before
    c.run(retry_failed=True)
    assert len(calls) > before
    c.close()
    import_collection(workspace)
    with read_only(workspace.database) as db:
        assert db.execute("SELECT status FROM source_documents WHERE document_key='guidance:existing'").fetchone()[0] == "active"

def test_receipt_tampering(workspace):
    c, _ = fixture_collector(workspace, {DIRECTORY: response(html('<a href="/page/1">안내</a>')),
        "https://www.dongguk.edu/page/1": response(html("<p>" + "공식 안내 내용입니다. "*20 + "</p>"))})
    c.run()
    path = c.db.execute("SELECT raw_path FROM receipts WHERE url LIKE '%/page/1'").fetchone()[0]
    Path(path).write_bytes(b"tampered")
    c.close()
    assert import_collection(workspace)["failed"][0]["error"] == "raw receipt hash mismatch"

def test_html_tables_quality_and_dynamic():
    body = '<h2>기준</h2><p>' + '공식 본문 내용 '*10 + '</p><table><tr><th rowspan="2">학과</th><th>기준</th></tr><tr><td>12학점</td></tr></table>'
    extracted = extract_html(html(body), DIRECTORY)
    assert extracted.status == "extracted" and "학과 | 12학점" in extracted.text
    assert "global menu" not in extracted.text and extracted.sections[0]["locator"].startswith("body/section/")
    assert extract_html(html("<p>짧음</p>"), DIRECTORY).status == "failed"
    assert extract_html(b"<title>Dongguk</title><div id='app'></div>", DIRECTORY).status == "unsupported_layout"
    assert extract_html(html("<input type='password'>"), DIRECTORY).status == "failed"

@pytest.mark.parametrize("suffix,part", [("docx", "word/document.xml"), ("pptx", "ppt/slides/slide1.xml"), ("hwpx", "Contents/section0.xml")])
def test_office_hwpx(suffix, part):
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr(part, '<root><p><t>' + '공식 첨부 원문 '*20 + '</t></p></root>')
    result = extract_attachment(stream.getvalue(), "guide."+suffix)
    assert result.status == "extracted" and result.sections[0]["locator"] == part

def test_xlsx_pdf_and_failure_states():
    from openpyxl import Workbook
    workbook = Workbook()
    workbook.active.append(["졸업 기준", "공식 안내 "*15])
    stream = BytesIO()
    workbook.save(stream)
    result = extract_attachment(stream.getvalue(), "guide.xlsx")
    assert result.status == "extracted" and result.sections[0]["locator"] == "sheet/Sheet"
    from pypdf import PdfWriter
    writer, stream = PdfWriter(), BytesIO()
    writer.add_blank_page(width=100,height=100)
    writer.write(stream)
    assert extract_attachment(stream.getvalue(), "scan.pdf").status == "ocr_required"
    assert extract_attachment(b"bad", "legacy.doc").status == "unsupported"
    assert extract_attachment(b"bad", "bad.hwp").status == "failed"
    assert extract_attachment(b"bad", "bad.pdf").status == "failed"

def test_attachment_backfill_preserves_parent(workspace):
    add_documents(workspace, [("notices", "legacy", {"title": "기존 공지", "campus_scope": "seoul",
        "attachments": [{"name": "guide.docx", "url": "https://www.dongguk.edu/file/1.docx"}]})])
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr("word/document.xml", "<root><p>" + "공식 첨부 내용 원문입니다 "*10 + "</p></root>")
    c, _ = fixture_collector(workspace, {"https://www.dongguk.edu/file/1.docx": Response(200, {}, stream.getvalue())})
    engine = create_engine("sqlite:///" + str(workspace.database))
    with Session(engine) as session:
        source = session.query(SourceDocument).one()
        original = (source.id,source.document_key,source.content_hash,source.normalized_payload_json)
        c.backfill_attachments([source])
    engine.dispose()
    c.run()
    c.close()
    assert len(import_collection(workspace)["new"]) == 1
    with read_only(workspace.database) as db:
        row = db.execute("SELECT * FROM source_documents WHERE dataset='notices'").fetchone()
        assert (row["id"],row["document_key"],row["content_hash"],row["normalized_payload_json"]) == original
        row = db.execute("SELECT * FROM source_documents WHERE dataset='attachments'").fetchone()
        p = json.loads(row["normalized_payload_json"])
        assert p["parent_document_key"] == "notices:legacy" and p["campus_scope"] == "seoul"
    build(workspace)
    assert connected_search(workspace.database, "첨부", scope=Scope(campus="seoul"))["hits"][0]["dataset"] == "attachments"

def test_guards_manifest(workspace, tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="marker"):
        StagingWorkspace(tmp_path/"unmarked").validate()
    with pytest.raises(ValueError, match="empty"):
        workspace.initialize()
    monkeypatch.setenv("RAG_DATABASE_FILE", str(workspace.database))
    with pytest.raises(ValueError, match="serving"):
        workspace.validate()
    p = load_policy(Path(__file__).resolve().parents[1]/"data/official_public_inventory.json")
    assert p.entry(DIRECTORY) and p.entry(SITEMAP) and len(p.approved) >= 180
    with pytest.raises(CollectionError):
        p.entry("https://lookalike-dongguk.edu/")

def test_limits_cycles_missing_graph_no_evidence(workspace):
    add_documents(workspace, competency_documents())
    build(workspace)
    with read_only(workspace.database) as db:
        reader = ConnectedReader(db)
        limited = reader.inspect(["courses:course"],max_nodes=2,max_edges=3,max_hops=4)
        assert limited["truncated"] and limited["visited_nodes"] <= 2 and len(limited["paths"]) <= 3
        assert reader.inspect(["missing"])["paths"] == []
        with pytest.raises(ValueError):
            reader.inspect(["courses:course"],max_hops=20)
    assert not connected_search(workspace.database, "아무근거없는문구")["hits"]
    with sqlite3.connect(workspace.database) as db:
        db.execute("DROP TABLE connected_manifest")
    assert connected_search(workspace.database, "졸업")["status"] == "fallback"

def test_runtime_common_helper(workspace, monkeypatch):
    from api import rag_service as service
    from src import database
    add_documents(workspace, competency_documents())
    build(workspace)
    monkeypatch.setattr(database,"DATABASE_FILE",workspace.database)
    kwargs = dict(query="졸업설계",frames=[],major=None,where_filter=None,
                  visibility_filter=service._notice_visibility_where_filter(None),date_filter=None,
                  as_of=date(2026,10,17),entry_year=None,request_id="fixture")
    monkeypatch.setattr(service.rag_config,"RAG_CONNECTED_RETRIEVAL_ENABLED",False)
    assert service._connected_retrieval_frames(**kwargs) == []
    monkeypatch.setattr(service.rag_config,"RAG_CONNECTED_RETRIEVAL_ENABLED",True)
    frames = service._connected_retrieval_frames(**kwargs)
    assert frames
    keys = set()
    for frame in frames:
        assert "connected_path" not in frame
        keys.update(frame["document_key"])
    assert not {"guidance:private","guidance:department","guidance:wise","guidance:old"} & keys
    shortlist = service._build_balanced_shortlist(frames,query="졸업설계",as_of=date(2026,10,17))
    assert {"guidance","attachments"} <= set(shortlist["dataset"])

def test_projection_corruption_falls_back_and_rollback_retains_previous(workspace):
    add_documents(workspace, competency_documents())
    original = build(workspace)
    with sqlite3.connect(workspace.database) as db:
        db.execute("DELETE FROM connected_edges WHERE edge_key=(SELECT edge_key FROM connected_edges LIMIT 1)")
    assert connected_search(workspace.database, "졸업")["status"] == "fallback"
    assert build(workspace)["revision"] == original["revision"]
    with sqlite3.connect(workspace.database) as db:
        db.execute("CREATE TRIGGER reject_connected BEFORE INSERT ON connected_edges BEGIN SELECT RAISE(ABORT,'fixture interruption'); END")
    with pytest.raises(sqlite3.IntegrityError):
        build(workspace)
    assert connected_search(workspace.database, "졸업")["status"] == "ok"


def test_changed_body_keeps_identity_and_failed_refresh_keeps_good_version(workspace):
    url = "https://www.dongguk.edu/page/1"
    routes = {DIRECTORY: response(html('<a href="/page/1">안내</a>')),
              url: response(html('<p>'+'졸업설계 공식 원문 안내 내용 '*15+'</p>'))}
    c, _ = fixture_collector(workspace,routes)
    c.run()
    first = import_collection(workspace)
    key = first["new"][0]
    routes[url] = response(html('<p>'+'졸업설계 변경된 공식 원문 안내 내용 '*15+'</p>'))
    c.db.execute("UPDATE frontier SET status='failed' WHERE url=?",(url,))
    c.db.commit()
    c.run(retry_failed=True)
    changed = import_collection(workspace)
    assert changed["updated"] == [key]
    routes[url] = Response(503,{},b"unavailable")
    c.db.execute("UPDATE frontier SET status='failed' WHERE url=?",(url,))
    c.db.commit()
    c.run(retry_failed=True)
    assert not import_collection(workspace)["updated"]
    with read_only(workspace.database) as db:
        row = db.execute("SELECT * FROM source_documents WHERE document_key=?",(key,)).fetchone()
        assert row["status"] == "updated" and "변경된" in row["normalized_payload_json"]
    c.close()


def test_pacing_request_depth_and_link_discovery_budgets(workspace):
    calls, sleeps, timer = [], [], [0.0]
    p = policy()
    budgets = Budgets(pages=2,requests=2,depth=0,per_host_seconds=1)
    def pause(seconds):
        sleeps.append(seconds)
        timer[0] += seconds
    def transport(url, address, remaining):
        calls.append(url)
        return response(html('<a href="/page/1">안내</a>'))
    f = Fetcher(p,budgets,transport=transport,resolver=lambda _: ["93.184.216.34"],clock=lambda:timer[0],sleep=pause)
    c = Collector(workspace.checkpoint,p,budgets,fetcher=f)
    report = c.run()
    assert len(calls)==2 and sum(sleeps)>=1
    assert report["states"]["depth_limited"] == 1
    assert "https://www.dongguk.edu/page/1" not in calls
    c.close()


def test_pdf_text_hwp_records_and_archive_entity_rejection(monkeypatch):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer, out = PdfWriter(), BytesIO()
    page = writer.add_blank_page(width=300,height=300)
    font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    content = DecodedStreamObject()
    content.set_data(b'BT /F1 12 Tf 10 100 Td (Official graduation procedure verified source text for students.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(content)
    writer.write(out)
    result = extract_attachment(out.getvalue(),'guide.pdf')
    assert result.status=='extracted' and result.sections[0]['locator']=='page/1'
    import olefile, struct
    text = ('공식 한글 문서 본문 자료 '*10).encode('utf-16le')
    header = b'HWP Document File'+b'\0'*(36-len(b'HWP Document File'))+struct.pack('<I',0)
    body = struct.pack('<I',67 | len(text)<<20)+text
    class FakeOle:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def listdir(self):return [['BodyText','Section0']]
        def openstream(self,name):return BytesIO(header if name=='FileHeader' else body)
    monkeypatch.setattr(olefile,'OleFileIO',lambda *a:FakeOle())
    result=extract_attachment(b'fixture','guide.hwp')
    assert result.status=='extracted' and result.sections[0]['locator']=='BodyText/Section0'
    out=BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('word/document.xml','<!DOCTYPE root [<!ENTITY x "test">]><root><p>&x;</p></root>')
    assert extract_attachment(out.getvalue(),'bad.docx').status=='failed'

def test_actual_directory_inventory_is_not_global_navigation():
    fixture=Path(__file__).parent/'fixtures/connected_official/directory.html'
    result=extract_html(fixture.read_bytes(),DIRECTORY,discovery_only=True)
    assert len(result.institutions)==188
    assert len({__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(i['listed_url']).hostname for i in result.institutions})==179
    assert sum(i['listed_url'].startswith('http://') for i in result.institutions)==93
    assert not result.links and all(i['campus_scope']=='unknown' for i in result.institutions)
    assert not any(i['institution']=='전역 메뉴' for i in result.institutions)


def test_body_contact_and_download_adapter_keep_roles_separate(workspace):
    raw=html('<nav><a href="/page/nav">메뉴</a></nav><div class="depth4"><a href="/page/tab">탭</a></div>'+
             '<p>'+'휴학 공식 절차 원문 안내입니다. '*12+'</p><a href="/page/body">본문 링크</a>'+
             '<div class="page_contact"><ul><li><span>담당부서</span><p>학사지원팀</p></li><li><span>연락처</span><p>02-2260-3619</p></li></ul></div>')
    result=extract_html(raw,'https://www.dongguk.edu/page/165')
    assert result.status=='extracted' and result.contacts[0]['담당부서']['value']=='학사지원팀'
    assert [link['url'] for link in result.links]==['https://www.dongguk.edu/page/body']
    assert len(result.discovery_links)==3
    notice=html('<div class="board_view"><div class="tit"><p>휴학 공지</p><div class="info">등록일 2026.10.17</div></div><div class="view_cont"><p>'+'공식 휴학 공지 원문입니다. '*12+'</p></div><div class="view_files"><ul><li><a href="javascript:downGO(\'guide.pdf\',\'/files/2026/\',\'abc.pdf\')">첨부파일</a></li></ul></div></div>')
    parsed=extract_html(notice,'https://www.dongguk.edu/article/NOTICE/detail/123')
    file=next(link for link in parsed.links if link.get('kind')=='attachment')
    assert 'filename=guide.pdf' in file['url'] and 'filerealname=abc.pdf' in file['url']
    assert parsed.metadata['published_at']=='2026-10-17'
    add_documents(workspace,[('staff','office',{'성명':'김**','부서경로':'학사지원팀','전화번호':'02-2260-3619','content':'학사지원팀 연락처'})])
    add_documents(workspace,[('guidance','page',{'title':'휴학 안내','sections':result.sections,'contacts':result.contacts})])
    report=build(workspace)
    with read_only(workspace.database) as db:
        assert db.execute("SELECT count(*) FROM connected_edges WHERE predicate='RESPONSIBLE_OFFICE'").fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM connected_edges WHERE predicate='CONTACT_REFERENCE'").fetchone()[0]==1
        row=db.execute("SELECT metadata_json FROM connected_chunks WHERE document_key='guidance:page' LIMIT 1").fetchone()
        assert 'department' not in json.loads(row[0])
    assert report['semantic_documents']>=1


def test_parent_permission_scope_status_and_default_time_are_rechecked(workspace):
    add_documents(workspace,competency_documents());build(workspace)
    assert not connected_search(workspace.database,'비빔밥')['hits']
    with sqlite3.connect(workspace.database) as db:
        row=db.execute("SELECT normalized_payload_json FROM source_documents WHERE document_key='notices:notice'").fetchone()
        p=json.loads(row[0]);p['visibility']='department';p['department']='다른학과'
        db.execute("UPDATE source_documents SET normalized_payload_json=? WHERE document_key='notices:notice'",(json.dumps(p),))
    assert connected_search(workspace.database,'첨부')['status']=='fallback'
    build(workspace)
    assert not connected_search(workspace.database,'첨부')['hits']
    with read_only(workspace.database) as db:
        inspection=ConnectedReader(db).inspect(['guidance:page'])
        assert all(p['document_key']!='attachments:file' for p in inspection['paths'])


def test_policy_upgrade_and_frontier_overflow_are_resumable(workspace):
    c,_=fixture_collector(workspace,{DIRECTORY:response(html('<a href="https://institution.example/page/1">학교 기관</a>'))})
    c.run();c.close()
    p=policy();manifest=dict(p.manifest);manifest['approved_sources']=list(manifest['approved_sources'])+[{'url':'https://institution.example/','owner':'학교 기관','allow_descendants':True,'verification_url':DIRECTORY}]
    p2=Policy(manifest)
    c=Collector(workspace.checkpoint,p2,Budgets(),fetcher=Fetcher(p2,Budgets(),transport=lambda *a:response(html('<p>'+'공식 기관 안내 원문 '*15+'</p>')),resolver=lambda _:['93.184.216.34'],sleep=lambda _:None))
    assert c.db.execute("SELECT status FROM frontier WHERE url='https://institution.example/page/1'").fetchone()[0]=='pending'
    c.run();c.close()
    assert import_collection(workspace)['new']


@pytest.mark.asyncio
@pytest.mark.parametrize('decision',['accept','refuse','unavailable'])
async def test_connected_candidates_use_actual_http_ask_sse_selector_and_grounding(workspace,monkeypatch,decision):
    import httpx
    from api import rag_service as service
    from tests.test_query_plan import configure
    from src import database
    from src.services import evidence_selector
    from src.services.evidence_selector import EvidenceSelectionDecision,EvidenceGroupDecision
    from src.services.grounding import GroundingResult
    configure(monkeypatch,None)
    add_documents(workspace,[('guidance','answer',{'title':'졸업설계 절차','content':'졸업설계 신청 절차는 공식 학사지원팀에 문의합니다. 원문 안내를 따릅니다.','campus_scope':'seoul'})])
    build(workspace)
    monkeypatch.setattr(database,'DATABASE_FILE',workspace.database)
    monkeypatch.setattr(service.rag_config,'RAG_CONNECTED_RETRIEVAL_ENABLED',True)
    monkeypatch.setattr(service.rag_config,'HYBRID_FUSION_MODE','rrf')
    monkeypatch.setattr(service,'RAG_GROUNDING_CHECK_ENABLED',True)
    monkeypatch.setattr(service,'RAG_STREAM_BUFFER_UNTIL_GROUNDED',True)
    monkeypatch.setattr(service,'RAG_SUGGEST_FOLLOWUPS',False)
    monkeypatch.setattr(service,'_request_temporal_context',lambda _:service.TemporalContext(as_of=date(2026,10,17),academic_year=2026,semester=2,phase='학기중'))
    monkeypatch.setattr(service,'_save_rag_evaluation_log',lambda *a,**k:None)
    monkeypatch.setattr(service,'_update_observability_log',lambda *a,**k:None)
    monkeypatch.setattr(service,'_update_grounding_log',lambda *a,**k:None)
    monkeypatch.setattr(service,'append_manual_history',lambda *a,**k:None)
    async def retrieve(**kw):return [],False,[]
    async def enrich(**kw):return kw['frames'],[]
    monkeypatch.setattr(service,'_retrieve_frames_for_queries',retrieve)
    monkeypatch.setattr(service,'_enrich_staff_lookup_frames',enrich)
    selected=[];grounded=[]
    class Selector:
        async def ainvoke(self,messages):
            data=json.loads(messages[-1].content.split('Candidate documents (JSON data):\n')[1])
            selected.append(data)
            ids=[d['candidate_id'] for d in data]
            parsed=EvidenceSelectionDecision(groups=[] if decision=='refuse' else [EvidenceGroupDecision(document_ids=ids[:1])])
            return {'parsed':parsed,'raw':None,'parsing_error':None}
    monkeypatch.setattr(evidence_selector,'_structured_selector',lambda:Selector())
    async def generate(**kw):return '졸업설계 절차는 학사지원팀에 문의합니다. [문서1]'
    async def stream(**kw):yield await generate(**kw)
    async def ground(*args,**kw):
        grounded.append(args)
        return GroundingResult(checked=decision!='unavailable',grounded=True,score=.95,relevance_score=.95,reason='deterministic fixture')
    monkeypatch.setattr(service,'generate_langchain_answer',generate)
    monkeypatch.setattr(service,'generate_langchain_answer_stream',stream)
    monkeypatch.setattr(service,'check_answer_grounding',ground)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),base_url='http://localhost') as client:
        ask=await client.post('http://localhost/ask',json={'question':'동국대학교 졸업설계 절차','session_id':'connected-fixture'})
        sse=await client.post('http://localhost/ask/stream',json={'question':'동국대학교 졸업설계 절차','session_id':'connected-fixture'})
    assert ask.status_code==200 and sse.status_code==200
    response=ask.json();events=[json.loads(line[6:]) for line in sse.text.splitlines() if line.startswith('data: ')]
    assert events[-1]['type']=='done'
    completion=next(e for e in events if e['type']=='completion')
    assert selected and len(selected)==2  # ordinary selector, no graph/SQL bypass
    if decision=='refuse':
        assert response['fallback_triggered'] and not grounded
    else:
        assert len(grounded)==2
        assert response['verification_status']==('unavailable' if decision=='unavailable' else 'passed')
        assert completion['verification_status']==response['verification_status']
        assert response['sources'] and 'https://www.dongguk.edu/page/answer' in response['citations']
    assert all('connected_path' not in str(data) and 'RESPONSIBLE_OFFICE' not in str(data) for data in selected)

def test_private_hub_documents_cannot_fill_public_work_limits(workspace):
    add_documents(workspace,[('guidance','public',{'title':'휴학 공식 안내','content':'휴학 공식 절차 공개 원문'})])
    build(workspace)
    before=connected_search(workspace.database,'휴학')
    add_documents(workspace,[('guidance',f'private-{n}',{'title':'휴학 숨김','content':'휴학 비공개 원문','visibility':'private'}) for n in range(1100)])
    build(workspace)
    after=connected_search(workspace.database,'휴학')
    assert [h['document_key'] for h in before['hits']]==[h['document_key'] for h in after['hits']]==['guidance:public']
    assert before['truncated']==after['truncated']
    assert after['hits'][0]['connected_match']==0  # self/site membership is not a useful graph bridge


def test_evaluation_and_cache_identity_follow_graph_and_alias_provenance(workspace,monkeypatch):
    from api import rag_service as service
    from src import database
    from src.services.connected_knowledge import connected_provenance
    add_documents(workspace,competency_documents());build(workspace)
    monkeypatch.setattr(database,'DATABASE_FILE',workspace.database)
    monkeypatch.setattr(service.rag_config,'RAG_CONNECTED_RETRIEVAL_ENABLED',False)
    baseline=service._semantic_cache_namespace(None)
    monkeypatch.setattr(service.rag_config,'RAG_CONNECTED_RETRIEVAL_ENABLED',True)
    current=service._semantic_cache_namespace(None)
    assert current!=baseline and connected_provenance(workspace.database)['state']=='current'
    with sqlite3.connect(workspace.database) as db:
        db.execute("UPDATE source_documents SET status='hidden' WHERE document_key='notices:notice'")
    assert service._semantic_cache_namespace(None)!=current
    assert connected_provenance(workspace.database)['state']=='stale'


def test_explicit_public_snapshot_import_is_non_overwriting(workspace,tmp_path):
    from src.pipelines.official_public import import_public_snapshot
    import hashlib
    add_documents(workspace,[('guidance','existing',{'content':'기존 원문'})])
    other=StagingWorkspace(tmp_path/'snapshot-stage');other.initialize()
    add_documents(other,[('guidance','existing',{'content':'다른 원문'}),('guidance','new',{'content':'추가 원문'}),('guidance','private',{'content':'비공개','visibility':'private'})])
    sha=__import__('hashlib').sha256(other.database.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='SHA256'):
        import_public_snapshot(workspace,other.database,'bad')
    report=import_public_snapshot(workspace,other.database,sha)
    assert report['imported']==1 and report['excluded']==1 and report['conflicts']==['guidance:existing']
    with read_only(workspace.database) as db:
        assert '기존 원문' in db.execute("SELECT normalized_payload_json FROM source_documents WHERE document_key='guidance:existing'").fetchone()[0]


def test_compound_runtime_candidates_obey_current_freshness(workspace,monkeypatch):
    from api import rag_service as service
    from src import database
    add_documents(workspace,competency_documents());build(workspace)
    monkeypatch.setattr(database,'DATABASE_FILE',workspace.database)
    monkeypatch.setattr(service.rag_config,'RAG_CONNECTED_RETRIEVAL_ENABLED',True)
    monkeypatch.setattr(service,'_stale_direct_answer',lambda dataset,anchor:object())
    frames=service._connected_retrieval_frames(query='비빔밥',frames=[],major=None,where_filter=None,visibility_filter=None,date_filter=None,as_of=date(2026,10,17),entry_year=None,request_id='compound')
    assert frames==[]

def test_query_bearing_download_and_board_identity_survive_url_checks():
    from src.collectors.official_public import canonical_url
    url="https://www.dongguk.edu/cmmn/fileDown.do?filename=guide.pdf&filepath=%2Ffiles%2F2026%2F&filerealname=a.pdf"
    assert canonical_url(canonical_url(url))==canonical_url(url)
    assert canonical_url('https://official.example/list?board=1')!=canonical_url('https://official.example/list?board=2')
    with pytest.raises(CollectionError):canonical_url('https://www.dongguk.edu/%2fprivate')

def test_connected_supplement_is_bounded_and_keeps_strong_baseline_rank(workspace,monkeypatch):
    import pandas as pd
    from api import rag_service as service
    from src import database
    add_documents(workspace,competency_documents());build(workspace)
    monkeypatch.setattr(database,'DATABASE_FILE',workspace.database)
    monkeypatch.setattr(service.rag_config,'RAG_CONNECTED_RETRIEVAL_ENABLED',True)
    for key,question in [('courses:course','CSE101 졸업설계'),('rules:guide','졸업기준'),('notices:notice','졸업설계 공지'),('staff:office','졸업설계 전화번호')]:
        dataset=key.split(':')[0]
        baseline=pd.DataFrame([{'document_key':key,'doc_id':key,'chunk_id':'baseline:'+key,'dataset':dataset,
            'source':dataset,'title':question,'chunk_text':question+' 공식 근거 원문','url':'https://www.dongguk.edu/fixture/'+dataset,
            'hybrid_score':.8,'vector_score':.8,'sparse_score':.8,'campus_scope':'seoul','matched_query':question}])
        supplement=service._connected_retrieval_frames(query=question,frames=[baseline],major=None,where_filter=None,
            visibility_filter=service._notice_visibility_where_filter(None),date_filter=None,as_of=date(2026,10,17),entry_year=None,request_id='baseline')
        assert sum(map(len,supplement))<=3
        assert all('structured_match' not in frame for frame in supplement)
        shortlist=service._build_balanced_shortlist([baseline]+supplement,query=question,as_of=date(2026,10,17))
        primary=shortlist[shortlist['dataset']==dataset].iloc[0]
        assert primary['document_key']==key and primary['chunk_id']=='baseline:'+key
