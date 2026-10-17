"""Independent falsifications: layout uncertainty and shared academic terms."""
from datetime import date
import json
import pytest
from src.collectors.official_extract import extract_attachment
from src.services.information_scope import temporal_intent
from src.services.connected_knowledge import Scope,connected_search
from tests.test_connected_official_knowledge import workspace,add_documents,build
from tests.test_information_graph import bundle,add_guides,import_actual,F
from tests.test_connected_information_integration import runtime,endpoint


def actual_table():
    return extract_attachment((F/'actual-major-plan.hwp').read_bytes(),'plan.hwp').sections[0]['text'][860:2268]


def test_renamed_flattened_table_is_still_unreviewed(workspace):
    add_documents(workspace,[('attachments','renamed',{'title':'2026학년도 2학기 전공결정','_type':'official_attachment','parser_version':'pdf','sections':[{'locator':'page/1','heading':'신청 대상','text':actual_table().replace('구분','유형')}]})]);build(workspace)
    result=bundle(workspace,'전공결정 신청 대상')
    assert not result['units'] and result['review_candidates']


def test_shared_academic_year_semester_is_not_a_whole_year_request():
    assert temporal_intent('2025학년도와 2026학년도 2학기 전공결정 신청기간 비교').academic_terms==((2025,'2'),(2026,'2'))


def collect_general(workspace):
    from src.collectors.official_public import Collector,Fetcher,Policy,Budgets,Response
    from src.pipelines.official_public import import_collection
    url='https://www.dongguk.edu/page/161'
    policy=Policy({'schema_version':1,'approved_sources':[{'url':url,'owner':'동국대학교','verification_url':url}]});budgets=Budgets(pages=1,requests=1)
    c=Collector(workspace.checkpoint,policy,budgets,fetcher=Fetcher(policy,budgets,transport=lambda *a:Response(200,{'content-type':'text/html'},(F/'actual-guidance-161.html').read_bytes()),resolver=lambda _:['93.184.216.34'],sleep=lambda _:None))
    c.run();c.close();report=import_collection(workspace)
    assert not report['failed'] and len(report['new'])==1
    return report['new'][0]


def test_actual_guidance_refresh_preserves_parent_acl(workspace):
    import sqlite3
    from src.pipelines.canonical import canonical_hash
    from src.pipelines.official_public import import_collection
    from src.services.connected_knowledge import ConnectedReader,read_only
    parent=collect_general(workspace)
    assert parent=='guidance:1256741fb1dfa366165dbef9c858bf4b21ca62bed34d7f969cd99088804c989c'
    with sqlite3.connect(workspace.database) as db:
        p=json.loads(db.execute('SELECT normalized_payload_json FROM source_documents WHERE document_key=?',(parent,)).fetchone()[0]);p.update(visibility='department',department='통계학과');db.execute('UPDATE source_documents SET normalized_payload_json=?,content_hash=? WHERE document_key=?',(json.dumps(p),canonical_hash(p,exclude_fields=('raw_sha256',)),parent))
    add_documents(workspace,[('attachments','dependent',{'title':'휴학 신청 안내','text':'신청 방법: PRIVATE_DESCENDANT 포털에서 신청한다.','parent_document_key':parent})]);build(workspace)
    report=import_collection(workspace);build(workspace)
    with read_only(workspace.database) as db:
        db.execute('BEGIN');reader=ConnectedReader(db,scope=Scope(as_of=date(2026,10,17)))
        assert 'attachments:dependent' not in reader.allowed
        assert 'attachments:dependent' in ConnectedReader(db,scope=Scope(as_of=date(2026,10,17),department='통계학과')).allowed
    with sqlite3.connect(workspace.database) as db:
        assert json.loads(db.execute('SELECT normalized_payload_json FROM source_documents WHERE document_key=?',(parent,)).fetchone()[0])['visibility']=='department'


def test_empty_colon_labels_cannot_satisfy_facets(workspace):
    add_documents(workspace,[('guidance','empty',{'title':'휴학 안내','text':'신청 대상:\n제출 서류:\n신청 방법:'})]);build(workspace)
    result=bundle(workspace,'휴학 신청 대상 제출 서류 신청 방법')
    assert {'eligibility','documents','procedure'}<=set(result['missing_facets'])
    assert not result['units']


@pytest.mark.parametrize('parser,locator',[('hwp','BodyText/Section0'),('pdf','page/1'),('hwpx','Contents/section0.xml')])
@pytest.mark.parametrize('header',['구분','유형','대체 표제',''])
def test_header_independent_review_and_plain_facts(workspace,parser,locator,header):
    table=actual_table().replace('구분',header)
    sections=[{'locator':locator+'/table','heading':'신청 대상','text':table},
              {'locator':locator+'/plain','heading':'신청 방법','text':'신청 방법: SAFE_METHOD nDRIMS에서 신청한다. 다만 대상 조건을 먼저 확인해야 한다.'},
              {'locator':locator+'/date','heading':'신청 기간','text':'신청 기간: 2026. 10. 19. 09:00 ~ 2026. 10. 23. 23:59'}]
    add_documents(workspace,[('attachments','layout',{'title':'2026학년도 2학기 전공결정','_type':'official_attachment','parser_version':parser,'sections':sections})]);build(workspace)
    result=bundle(workspace,'2026학년도 2학기 전공결정 신청 대상 기간 방법')
    assert result['review_candidates'] and all(u['review_status']=='needs_table_layout_review' for u in result['review_candidates'])
    assert 'SAFE_METHOD' in str(result['units']) and '09:00' in str(result['units'])
    assert all('150%' not in u['source_text'] and '인원배정' not in u['source_text'] for u in result['units'])
    assert all('인원배정' not in c['source_text'] for u in result['units'] for c in u['qualifiers'].get('source_applicability_context',[]))
    assert not connected_search(workspace.database,'인원배정',scope=Scope(as_of=date(2026,10,17)),connected=False)['hits']


@pytest.mark.parametrize('parser,locator',[('hwp','BodyText/Section0'),('pdf','page/1'),('hwpx','Contents/section0.xml')])
@pytest.mark.parametrize('association_status',['unreviewed','unvalidated',None])
def test_structural_table_spans_without_numeric_cells_or_header(workspace,parser,locator,association_status):
    text='그룹A 그룹B\n조건A 조건B\n대안A 대안B'
    section={'locator':locator,'heading':'신청 대상','text':text,'layout_evidence':{'source':'native_table_structure','table_spans':[{'span_start':0,'span_end':len(text),'association_status':association_status}]}}
    add_documents(workspace,[('attachments','small',{'title':'전공결정','_type':'official_attachment','parser_version':parser,'sections':[section]})]);build(workspace)
    result=bundle(workspace,'전공결정 신청 대상')
    assert not result['units'] and result['review_candidates']


@pytest.mark.asyncio
@pytest.mark.parametrize('parser',['hwp','pdf','hwpx'])
@pytest.mark.parametrize('mode',['ask','stream'])
async def test_table_never_reaches_nested_selector_or_prompt(workspace,runtime,parser,mode):
    table=actual_table().replace('구분','유형')
    add_documents(workspace,[('attachments','layout',{'title':'2026학년도 2학기 전공결정','_type':'official_attachment','parser_version':parser,'sections':[
      {'locator':'body/table','heading':'신청 대상','text':table},
      {'locator':'body/method','heading':'신청 방법','text':'신청 방법: SAFE_METHOD nDRIMS를 이용한다. 다만 원문의 대상 조건을 확인해야 한다.'},
      {'locator':'body/date','heading':'신청 기간','text':'신청 기간: 2026. 10. 19. 09:00 ~ 2026. 10. 23. 23:59'}]})]);build(workspace)
    result=await endpoint('2026학년도 2학기 전공결정 신청 대상과 기간 방법을 알려줘',mode)
    assert result['verification_status']=='passed' and runtime.selector and runtime.contexts and runtime.grounding
    for material in (str(runtime.selector[-1]),runtime.contexts[-1]['context'],runtime.grounding[-1][2]):
        assert 'SAFE_METHOD' in material and '09:00' in material and '23:59' in material
        assert '인원배정' not in material and '150%' not in material and '가능인원' not in material


@pytest.mark.parametrize('suffix',['년','년도','학년도'])
@pytest.mark.parametrize('connector',['와','과','및'])
def test_shared_semester_suffixes_and_connectors(suffix,connector):
    assert temporal_intent(f'2025{suffix}{connector} 2026{suffix} 2학기 전공결정 기간 비교').academic_terms==((2025,'2'),(2026,'2'))


@pytest.mark.parametrize('query,expected',[
 ('2025학년도 전체와 2026학년도 2학기 전공결정 기간 비교',((2025,None),(2026,'2'))),
 ('2025-1과 2026-2 전공결정 기간 비교',((2025,'1'),(2026,'2'))),
 ('2025학년도 1학기 및 2026학년도 2학기 전공결정 기간 비교',((2025,'1'),(2026,'2'))),
 ('2025학번과 2026학년도 2학기 전공결정 기간',((2026,'2'),)),
 ('2025년 2월과 2026학년도 2학기 전공결정 기간',((2026,'2'),)),
 ('2024학년도와 2025학년도 및 2026학년도 2학기 전공결정 기간 비교',((2024,'2'),(2025,'2'),(2026,'2'))),
])
def test_shared_qualifier_never_overrides_explicit_other_scope(query,expected):
    assert temporal_intent(query).academic_terms==expected


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ask','stream'])
@pytest.mark.parametrize('phrase,whole',[('2025학년도와 2026학년도 2학기',False),('2025년도 및 2026년도 2학기',False),('2025학년도 전체와 2026학년도 2학기',True)])
async def test_actual_e25_e26_shared_scope_in_selector_and_generation(workspace,runtime,mode,phrase,whole):
    import_actual(workspace);add_guides(workspace);build(workspace)
    query=phrase+' 전공결정 신청 기간을 비교해줘'
    inspected=bundle(workspace,query)
    assert not inspected['missing_scope_facets']
    for u in inspected['units']:
        q=u['qualifiers']
        if not whole:assert q.get('semester')!='1'
        elif str(q.get('academic_year'))=='2026':assert q.get('semester')!='1'
    result=await endpoint(query,mode)
    assert result['verification_status']=='passed'
    for material in (str(runtime.selector[-1]),runtime.contexts[-1]['context'],runtime.grounding[-1][2]):
        assert '2025. 10. 20.' in material and '2026. 10. 19.' in material
        assert '2026. 04. 13.' not in material
        assert ('2025. 04. 14.' in material)==whole


@pytest.mark.parametrize('label,facet',[('신청 대상:','eligibility'),('제출 서류:','documents'),('신청 방법:','procedure'),('신청 대상：','eligibility')])
def test_empty_labels_missing_at_every_evidence_boundary(workspace,label,facet):
    import pandas as pd
    from src.services.information_graph import information_hits
    from src.services.connected_knowledge import ConnectedReader,read_only,referenced_information
    from src.services.evidence_selector import _bounded_candidates
    from api import rag_service as service
    add_documents(workspace,[('guidance','labels',{'title':'휴학 안내','text':label})]);build(workspace)
    result=bundle(workspace,'휴학 신청 대상 서류 방법')
    assert facet in result['missing_facets'] and not result['units']
    assert _bounded_candidates([{'candidate_id':'c1','dataset':'guidance','text':label,'information_match':1,'information_evidence_units':[{'unit_key':'empty','source_text':label,'qualifiers':{}}]}])==[]
    frame=pd.DataFrame([{'dataset':'guidance','title':'휴학 안내','citation_number':1,'evidence_group':1,'information_match':1,'information_evidence_units':[{'unit_key':'empty','source_text':label,'qualifiers':{}}]}])
    assert service._build_selected_evidence_context(frame)==''
    assert not connected_search(workspace.database,'휴학 신청 방법',scope=Scope(as_of=date(2026,10,17)),connected=False)['hits']
    assert not referenced_information(workspace.database,document_key='guidance:labels',dataset='guidance',query='그 문서 다시 보여줘',scope=Scope(as_of=date(2026,10,17)))['hits']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ask','stream'])
@pytest.mark.parametrize('reference',[False,True])
async def test_all_empty_labels_never_invoke_models(workspace,runtime,mode,reference):
    from src.services import langchain_chat as chat
    add_documents(workspace,[('guidance','labels',{'title':'휴학 안내','text':'신청 대상:\n제출 서류:\n신청 방법:'})]);build(workspace)
    if reference:chat.append_manual_history(mode,'휴학 안내 알려줘','이전 내용',[{'source':'guidance','title':'휴학 안내','citation_number':1,'metadata':{'doc_id':'guidance:labels'}}])
    result=await endpoint('그 문서 다시 보여줘' if reference else '휴학 신청 대상 서류 방법',mode,mode)
    assert result['sources']==[] and not runtime.selector and not runtime.contexts and not runtime.grounding


@pytest.mark.parametrize('text,facet,status',[
 ('신청 대상: 재학생','eligibility',None),('신청 대상:\n재학생','eligibility',None),
 ('제출 서류: 재학증명서','documents',None),('신청 방법:\nnDRIMS에서 신청한다.','procedure',None),
 ('신청 기간: 미정','period','stated_unknown'),('신청 기간:\n추후 안내 예정','period','stated_unknown'),
 ('수수료: 0','amount',None),
])
def test_populated_fields_zero_and_stated_unknown_are_retained(workspace,text,facet,status):
    add_documents(workspace,[('guidance','value',{'title':'휴학 안내','text':text})]);build(workspace)
    result=bundle(workspace,'휴학 신청 대상 서류 방법 기간 수수료')
    assert facet in result['available_facets'] and any(text==u['source_text'] for u in result['units'])
    if status:assert any(u['qualifiers'].get('source_value_status')==status for u in result['units'])


def test_one_populated_field_does_not_fill_neighbor_empty_fields(workspace):
    add_documents(workspace,[('guidance','mixed',{'title':'휴학 안내','text':'신청 대상:\n제출 서류:\n신청 방법: nDRIMS에서 신청한다.'})]);build(workspace)
    result=bundle(workspace,'휴학 신청 대상 서류 방법')
    assert 'procedure' in result['available_facets']
    assert {'eligibility','documents'}<=set(result['missing_facets'])


def rewrite_canonical(workspace,key,changes,*,status=None,source_type=None):
    import sqlite3
    from src.pipelines.canonical import canonical_hash
    with sqlite3.connect(workspace.database) as db:
        p=json.loads(db.execute('SELECT normalized_payload_json FROM source_documents WHERE document_key=?',(key,)).fetchone()[0]);p.update(changes)
        db.execute('UPDATE source_documents SET normalized_payload_json=?,content_hash=? WHERE document_key=?',(json.dumps(p),canonical_hash(p,exclude_fields=('raw_sha256',)),key))
        if status:db.execute('UPDATE source_documents SET status=? WHERE document_key=?',(status,key))
        if source_type:db.execute('UPDATE source_documents SET source_type=? WHERE document_key=?',(source_type,key))
        return tuple(db.execute('SELECT status,normalized_payload_json,content_hash,raw_payload_json FROM source_documents WHERE document_key=?',(key,)).fetchone())


@pytest.mark.parametrize('dataset',['guidance','notices','attachments'])
@pytest.mark.parametrize('visibility',['private','department'])
def test_every_dataset_restricted_refresh_keeps_curated_body_and_scope(workspace,dataset,visibility):
    import sqlite3
    from src.pipelines.official_public import import_collection
    from src.services.connected_knowledge import ConnectedReader,read_only
    import_actual(workspace)
    with sqlite3.connect(workspace.database) as db:key=db.execute('SELECT document_key FROM source_documents WHERE dataset=?',(dataset,)).fetchone()[0]
    expected=rewrite_canonical(workspace,key,{'visibility':visibility,'department':'통계학과','content':'CURATED_PRIOR_BODY','sections':[{'locator':'curated','text':'신청 방법: CURATED_PRIOR_BODY를 확인한다.'}]})
    add_documents(workspace,[('attachments','dependent',{'title':'휴학 신청 안내','text':'신청 방법: PRIVATE_DESCENDANT를 확인한다.','parent_document_key':key})]);build(workspace)
    report=import_collection(workspace);build(workspace)
    assert report['failed']
    with sqlite3.connect(workspace.database) as db:assert tuple(db.execute('SELECT status,normalized_payload_json,content_hash,raw_payload_json FROM source_documents WHERE document_key=?',(key,)).fetchone())==expected
    with read_only(workspace.database) as db:
        db.execute('BEGIN');reader=ConnectedReader(db,scope=Scope(as_of=date(2026,10,17)))
        assert key not in reader.allowed and 'attachments:dependent' not in reader.allowed


@pytest.mark.parametrize('status',['hidden','withdrawn','provenance','active_nonanswerable'])
def test_refresh_never_reactivates_nonpublished_parent(workspace,status):
    import sqlite3
    from src.pipelines.official_public import import_collection
    key=collect_general(workspace)
    flags={'content':'CURATED_INACTIVE_BODY'}
    if status in {'provenance','active_nonanswerable'}:flags.update(provenance_only=True,answerable=False)
    expected=rewrite_canonical(workspace,key,flags,status='active' if status=='active_nonanswerable' else status,source_type='official_discovery_parent' if status=='provenance' else None)
    report=import_collection(workspace)
    assert report['failed'] and not report['updated']
    with sqlite3.connect(workspace.database) as db:assert tuple(db.execute('SELECT status,normalized_payload_json,content_hash,raw_payload_json FROM source_documents WHERE document_key=?',(key,)).fetchone())==expected


@pytest.mark.parametrize('dataset',['guidance','notices','attachments'])
def test_public_refresh_retains_curated_applicability(workspace,dataset):
    import sqlite3
    from src.pipelines.official_public import import_collection
    import_actual(workspace)
    with sqlite3.connect(workspace.database) as db:key=db.execute('SELECT document_key FROM source_documents WHERE dataset=?',(dataset,)).fetchone()[0]
    fields={'visibility':'public','campus_scope':'seoul','audience':'undergraduate','department':'통계학과','major':'통계학과','entry_year':'2026','academic_year':'2026','semester':'2','college_name':'이과대학','program_name':'전공결정','effective_from':'2026-01-01','effective_to':'2026-12-31','curriculum_year':'2023','availability_status':'curriculum_only','offered_semesters':'unverified'}
    rewrite_canonical(workspace,key,{**fields,'content':'OLD_PUBLIC_BODY','sections':[{'locator':'old','text':'신청 방법: OLD_PUBLIC_BODY에서 신청한다.'}]})
    report=import_collection(workspace)
    with sqlite3.connect(workspace.database) as db:
        p=json.loads(db.execute('SELECT normalized_payload_json FROM source_documents WHERE document_key=?',(key,)).fetchone()[0])
    if dataset=='attachments':
        # The current broad parent cannot overwrite this child's narrower
        # independent scope; a conflict may be retained for review.
        assert all(p[k]==v for k,v in fields.items())
    else:
        assert key in report['updated'] and 'OLD_PUBLIC_BODY' not in p['content']
        assert all(p[k]==v for k,v in fields.items())


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ask','stream'])
@pytest.mark.parametrize('restricted',['parent','child'])
@pytest.mark.parametrize('visibility',['private','department'])
async def test_import_acl_boundary_reaches_public_ask_sse(workspace,runtime,mode,restricted,visibility):
    import sqlite3
    from src.pipelines.official_public import import_collection
    from src.services.connected_knowledge import ConnectedReader,read_only
    if restricted=='parent':
        parent=collect_general(workspace)
        child='attachments:dependent'
        add_documents(workspace,[('attachments','dependent',{'title':'휴학 신청 안내','text':'신청 방법: PRIVATE_DESCENDANT 포털에서 신청한다.','parent_document_key':parent})])
        key=parent
    else:
        import_actual(workspace)
        with sqlite3.connect(workspace.database) as db:child=db.execute("SELECT document_key FROM source_documents WHERE dataset='attachments'").fetchone()[0]
        key=child
    expected=rewrite_canonical(workspace,key,{'visibility':visibility,'department':'경영학과'})
    build(workspace);import_collection(workspace);build(workspace)
    with sqlite3.connect(workspace.database) as db:assert tuple(db.execute('SELECT status,normalized_payload_json,content_hash,raw_payload_json FROM source_documents WHERE document_key=?',(key,)).fetchone())==expected
    with read_only(workspace.database) as db:
        db.execute('BEGIN');assert child not in ConnectedReader(db,scope=Scope(as_of=date(2026,10,17))).allowed
    result=await endpoint('휴학 신청 방법' if restricted=='parent' else '전공결정 신청 예외와 방법',mode)
    assert all(s.get('metadata',{}).get('doc_id')!=child for s in result['sources'])
    assert 'PRIVATE_DESCENDANT' not in str(runtime.selector)+str(runtime.contexts)+str(runtime.grounding)


def test_native_hwp_layout_spans_are_structural_and_preserve_plain_text():
    result=extract_attachment((F/'actual-major-plan.hwp').read_bytes(),'plan.hwp')
    assert result.status=='extracted'
    section=result.sections[0];spans=section['layout_evidence']['table_spans']
    outer=next(s for s in spans if s.get('rows')==5 and s.get('columns')==4)
    assert outer['association_status']=='unreviewed' and outer['span_start']==936 and outer['span_end']==2268
    assert '150%' in section['text'][outer['span_start']:outer['span_end']]
    assert '09:00' in section['text'][:outer['span_start']] and '5지망' in section['text'][:outer['span_start']]
    assert any(s['association_status']=='single_cell_text' for s in spans)


def test_native_hwpx_table_without_header_preserves_separate_plain_sections(workspace):
    from io import BytesIO
    import zipfile
    xml='''<sec><p>1. 전공결정 신청 방법</p><p>신청 방법: SAFE_XML nDRIMS에서 신청하며, 다만 대상 조건을 먼저 확인해야 한다.</p><p>2. 신청 대상</p><tbl><tr><tc><p>그룹A</p></tc><tc><p>그룹B</p></tc></tr><tr><tc><p>조건A</p></tc><tc><p>조건B</p></tc></tr></tbl><p>3. 신청 기간</p><p>신청 기간: 2026. 10. 19. 09:00 ~ 2026. 10. 23. 23:59</p></sec>'''
    raw=BytesIO()
    with zipfile.ZipFile(raw,'w') as archive:archive.writestr('Contents/section0.xml',xml)
    result=extract_attachment(raw.getvalue(),'plan.hwpx');assert result.status=='extracted'
    assert result.sections[0]['layout_evidence']['table_spans']
    add_documents(workspace,[('attachments','xml',{'title':'2026학년도 2학기 전공결정','_type':'official_attachment','parser_version':'hwpx','sections':result.sections})]);build(workspace)
    answer=bundle(workspace,'2026학년도 2학기 전공결정 대상 방법 기간')
    assert answer['review_candidates']
    assert 'SAFE_XML' in str(answer['units']) and '09:00' in str(answer['units']) and '그룹A' not in str(answer['units'])


@pytest.mark.parametrize('row_count',[2,3])
def test_pdf_position_uncertainty_without_numeric_or_named_header(workspace,monkeypatch,row_count):
    import pypdf
    prefix='1. 전공결정 신청 방법\n신청 방법: SAFE_PDF nDRIMS에서 신청하며 다만 원문의 조건을 확인해야 한다.\n2. 신청 대상\n'
    cells=[('그룹A 조건A\n','그룹A','조건A'),('그룹B 조건B\n','그룹B','조건B'),('그룹C 조건C\n','그룹C','조건C')]
    cells=cells[:row_count]
    suffix='3. 신청 기간\n신청 기간: 2026. 10. 19. 09:00 ~ 2026. 10. 23. 23:59'
    text=prefix+''.join(row[0] for row in cells)+suffix
    class Page:
        def extract_text(self,visitor_text):
            def emit(value,x,y):visitor_text(value,[1,0,0,1,0,0],[1,0,0,1,x,y],None,10)
            emit(prefix,0,900)
            for index,(_,left,right) in enumerate(cells):emit(left,0,700-index*20);emit(right,100,700-index*20)
            emit(suffix,0,500);return text
    class Reader:
        is_encrypted=False
        pages=[Page()]
    monkeypatch.setattr(pypdf,'PdfReader',lambda _:Reader())
    extracted=extract_attachment(b'%PDF-fixture','plan.pdf');assert extracted.status=='extracted'
    assert len(extracted.sections[0]['layout_evidence']['table_spans'])==row_count
    add_documents(workspace,[('attachments','pdf',{'title':'2026학년도 2학기 전공결정','_type':'official_attachment','parser_version':'pdf','sections':extracted.sections})]);build(workspace)
    result=bundle(workspace,'2026학년도 2학기 전공결정 대상 방법 기간')
    assert result['review_candidates'] and 'SAFE_PDF' in str(result['units']) and '09:00' in str(result['units'])
    assert '그룹A' not in str(result['units'])


def test_provenance_refresh_preserves_current_applicability_and_nonanswerable_state(workspace):
    import sqlite3
    from tests.test_connected_information_integration import collect_actual_discovery_parent
    from src.pipelines.official_public import import_collection
    from src.services.connected_knowledge import ConnectedReader,read_only
    collect_actual_discovery_parent(workspace);initial=import_collection(workspace)
    parent=initial['provenance_parents'][0];child=initial['new'][0]
    fields={'campus_scope':'seoul','audience':'graduate','entry_year':'2026','effective_from':'2026-01-01','effective_to':'2026-12-31'}
    rewrite_canonical(workspace,parent,fields);build(workspace)
    import_collection(workspace);build(workspace)
    with sqlite3.connect(workspace.database) as db:
        row=db.execute('SELECT status,normalized_payload_json FROM source_documents WHERE document_key=?',(parent,)).fetchone();p=json.loads(row[1])
        assert row[0]=='provenance' and p['provenance_only'] and not p['answerable']
        assert all(p[k]==v for k,v in fields.items())
        assert db.execute('SELECT count(*) FROM information_units WHERE document_key=?',(parent,)).fetchone()[0]==0
    with read_only(workspace.database) as db:
        db.execute('BEGIN');assert child not in ConnectedReader(db,scope=Scope(as_of=date(2026,10,17))).allowed


def test_two_unlabeled_numeric_cells_are_not_verified_column_values(workspace):
    add_documents(workspace,[('attachments','numeric',{'title':'전공결정','_type':'official_attachment','parser_version':'pdf','sections':[{'locator':'page/1','heading':'신청 대상','text':'신청 대상\n1\n2'}]})]);build(workspace)
    result=bundle(workspace,'전공결정 신청 대상')
    assert not result['units'] and result['review_candidates']
