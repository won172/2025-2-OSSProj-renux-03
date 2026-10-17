"""Independent source-oracle regressions; fixtures are not human relevance labels."""
from datetime import date
from pathlib import Path
import hashlib,json,sqlite3
import pytest
from tests.test_connected_official_knowledge import workspace,add_documents,build
from src.services.connected_knowledge import ConnectedReader,Scope,read_only,connected_search
from src.services.information_graph import information_bundle
from src.collectors.official_extract import extract_html
from src.collectors.official_public import Collector,Fetcher,Budgets,Policy,Response,canonical_url
from src.pipelines.official_public import import_collection
F=Path(__file__).parent/'fixtures/connected_official'


def bundle(w,query,scope=Scope(as_of=date(2026,10,17))):
    with read_only(w.database) as db:
        db.execute('BEGIN');return information_bundle(ConnectedReader(db,scope=scope),query)


def add_guides(w):
    import pandas as pd
    frame=pd.read_csv(Path(__file__).resolve().parents[1]/'data/dongguk_entry_year_guide_sections.csv').fillna('')
    for _,row in frame.iterrows():
        if str(row['entry_year']) in {'2025','2026'} and row['section']=='학적 및 학생 관련':
            p=row.to_dict();p.update(_type='entry_year_guide_pdf',_url=p['source_url']);add_documents(w,[('rules','E'+str(row['entry_year'])[-2:],p)])


def import_actual(w):
    m=json.loads((F/'actual-source-manifest.json').read_text())
    rows=[next(r for r in m if r['file']==name) for name in ('actual-guidance-161.html','cms-detail-probes-1.html','actual-major-plan.hwp')]
    sources=[{'url':canonical_url(r['url']),'owner':'공식 공개 source fixture','verification_url':r['url'],'seed':i<2} for i,r in enumerate(rows)]
    p=Policy({'schema_version':1,'approved_sources':sources});routes={canonical_url(r['url']):Response(200,{'content-type':'text/html' if i<2 else 'application/octet-stream'},(F/r['file']).read_bytes()) for i,r in enumerate(rows)}
    b=Budgets(pages=3,requests=3);c=Collector(w.checkpoint,p,b,fetcher=Fetcher(p,b,transport=lambda url,*a:routes[url],resolver=lambda _:['93.184.216.34'],sleep=lambda _:None))
    c.run();c.close();report=import_collection(w)
    assert len(report['new'])==3 and not report['failed'],report


def test_college_identity_unit_membership_and_course_credit_attributes(workspace):
    for college in ('공과대학','경영대학'):
        add_documents(workspace,[('rules',college,{'title':'2026학번 졸업 기준','college_name':college,'entry_year':'2026','campus_scope':'seoul','text':'졸업 요건: 해당 대학의 필수 조건을 충족해야 한다.'})])
    add_documents(workspace,[('schedule','two',{'title':'휴학 및 복학 신청 기간','academic_year':'2026','start_date':'2026-08-01','end_date':'2026-08-31','campus_scope':'seoul'}),('courses','CSE101',{'title':'자료구조','course_name':'자료구조','course_code':'CSE101','credit':'3','campus_scope':'seoul','availability_status':'curriculum_only'})]);build(workspace)
    with read_only(workspace.database) as db:
        assert db.execute("SELECT count(DISTINCT subject_key) FROM information_units WHERE document_key IN ('rules:공과대학','rules:경영대학')").fetchone()[0]==2
        assert db.execute('SELECT count(*) FROM information_assertions a JOIN information_units u USING(unit_key) WHERE a.subject_key!=u.subject_key').fetchone()[0]==0
        assert [r[0] for r in db.execute("SELECT predicate FROM information_assertions a JOIN information_units u USING(unit_key) WHERE u.locator LIKE '$.credit#%'")]==['HAS_CREDITS']
    result=bundle(workspace,'졸업 요건',Scope(campus='seoul',college='공과대학',entry_year=2026,as_of=date(2026,10,17)))
    assert {u['document_key'] for u in result['units']}=={'rules:공과대학'}


def test_actual_guides_inherit_parent_topic_and_preserve_event_years(workspace):
    add_guides(workspace);build(workspace)
    result=bundle(workspace,'전공결정 신청 기간 방법 예외',Scope(as_of=date(2026,10,17),academic_years=(2025,2026)))
    texts={key:'\n'.join(u['source_text'] for u in result['units'] if u['document_key']==key) for key in ('rules:E26','rules:E25')}
    assert '2026. 10. 19.' in texts['rules:E26'] and '2025. 10. 20.' in texts['rules:E25']
    assert '2027학년도 1학기' in texts['rules:E26']
    assert all('학년별 수료기준 학점' not in text for text in texts.values())
    with read_only(workspace.database) as db:
        for row in db.execute("SELECT * FROM information_units WHERE document_key IN ('rules:E26','rules:E25')"):
            p=json.loads(db.execute('SELECT normalized_payload_json FROM source_documents WHERE document_key=?',(row['document_key'],)).fetchone()[0])
            assert p['text'][row['span_start']:row['span_end']]==row['source_text']
            for h in json.loads(row['qualifiers_json']).get('source_heading_spans',[]):assert p['text'][h['start']:h['end']]==h['text']


def test_actual_g_n_h_e26_e25_oracle(workspace):
    import_actual(workspace);add_guides(workspace);build(workspace)
    assert hashlib.sha256((F/'actual-major-plan.hwp').read_bytes()).hexdigest()=='44397835494a08aa58e431a3496b8079faf63efd0c3417b09e3955926da27ca5'
    result=bundle(workspace,'전공결정 대상 자격 신청 기간 방법 결과 발표 예외 선발기준 미신청 불이익 첨부 제출서류',Scope(as_of=date(2026,10,17),academic_year=2026,semester='2'))
    text='\n'.join(u['source_text'] for u in result['units'])
    for expected in ('학과별 또는 전공확정 후 입학생 제외','2026. 10. 19.','09:00','23:59','2026. 11. 06.','추후 안내 예정','5지망','물리·반도체과학부','2027학년도 1학기','후순위','강제 배정 가능'):
        assert expected in text,expected
    assert 'documents' in result['missing_facets']
    assert any(u['facet']=='plan_attachment' for u in result['units'])
    assert result['source_differences'] and all(r['status']=='different_source_statements_not_adjudicated' for r in result['source_differences'])
    assert any(r['predicate']=='ATTACHMENT_DETAIL_FOR' for r in result['references'])
    assert 'rules:E26' in {u['document_key'] for u in result['units']}
    assert any(u['qualifiers'].get('layout_status')=='flattened_table_requires_column_review' for u in result['review_candidates'])
    assert all(u['review_status']!='needs_table_layout_review' for u in result['units'])
    assert all(c.get('review_status')=='deterministic_explicit' and '인원배정' not in c['source_text'] for u in result['units'] for c in u['qualifiers'].get('source_applicability_context',[]))
    with read_only(workspace.database) as db:
        assert [r[0] for r in db.execute("SELECT source_text FROM information_units u JOIN information_assertions a USING(unit_key) WHERE a.predicate='PUBLISHED_ON' AND document_key LIKE 'notices:%'")]==['2026-10-16']
        assert db.execute("SELECT count(*) FROM information_references WHERE predicate='EXPLICIT_COURSE_REQUIREMENT'").fetchone()[0]==0
    raw=connected_search(workspace.database,'인원배정',scope=Scope(as_of=date(2026,10,17)))
    assert all(h['dataset']!='attachments' for h in raw['hits'])
    assert 'review_required_evidence_omitted' in raw['truncated']
    missing=bundle(workspace,'전공결정 필수 교과목 학수번호와 제출 서류',Scope(as_of=date(2026,10,17),academic_year=2026))
    assert {'course_reference','documents'}<=set(missing['missing_facets'])


def test_actual_cms_adapter_pagination_and_image_ocr_states():
    m=json.loads((F/'actual-source-manifest.json').read_text())
    for r in [r for r in m if r['file'].startswith('department-subpage')]:
        parsed=extract_html((F/r['file']).read_bytes(),r['url']);assert parsed.status=='extracted',(r['url'],parsed.error)
    for r in [r for r in m if r['file'].startswith('cms-board')]:
        parsed=extract_html((F/r['file']).read_bytes(),r['url']);assert parsed.status=='discovery_only' and any(a.get('kind')=='pagination' for a in parsed.discovery_links)
    r=next(r for r in m if r['file']=='cms-detail-probes-0.html');parsed=extract_html((F/r['file']).read_bytes(),r['url'])
    assert parsed.status=='ocr_required' and parsed.images


def test_log_insert_keeps_revision_cache_permission_change_invalidates(workspace,monkeypatch):
    import src.services.connected_knowledge as k
    add_documents(workspace,[('guidance','one',{'title':'휴학','text':'신청 방법: nDRIMS에서 신청한다.'})]);build(workspace)
    k._validated_cache.clear();calls=[];original=k.load_documents
    def load(db):calls.append(1);return original(db)
    monkeypatch.setattr(k,'load_documents',load)
    connected_search(workspace.database,'휴학 방법');assert len(calls)==1
    with sqlite3.connect(workspace.database) as db:db.execute("INSERT INTO rag_query_logs(question) VALUES('unrelated fixture')")
    connected_search(workspace.database,'휴학 방법');assert len(calls)==1
    with sqlite3.connect(workspace.database) as db:db.execute("UPDATE source_documents SET status='hidden' WHERE document_key='guidance:one'")
    assert connected_search(workspace.database,'휴학 방법')['status']=='fallback' and len(calls)==2


def test_actual_calendar_named_process_future_event_year_and_manager_scope(workspace):
    fixture=json.loads((F/'actual-calendar.json').read_text())
    for i,row in enumerate(fixture['events']):
        p={'title':row['내용'],'academic_year':str(row['학년도']),'department':row['주관부서'],'start_date':row['start'],'end_date':row['end'],'_type':'academic_schedule','_url':fixture['source_url']}
        add_documents(workspace,[('schedule',str(i),p)])
        with sqlite3.connect(workspace.database) as db:db.execute('UPDATE source_documents SET published_at=? WHERE document_key=?',(row['start'],'schedule:'+str(i)))
    build(workspace)
    upcoming=bundle(workspace,'전공결정 신청 기간',Scope(as_of=date(2026,10,17),academic_year=2026))
    assert any('2026-10-19' in u['source_text'] for u in upcoming['units'])
    leave=bundle(workspace,'2027학년도 1학기 휴학 신청 기간',Scope(as_of=date(2026,10,17),academic_year=2027,semester='1',department='컴퓨터공학과'))
    assert any('2027-01-14' in u['source_text'] for u in leave['units'])
    assert all(u['qualifiers']['academic_year']=='2027' and u['qualifiers'].get('calendar_container_year')=='2026' for u in leave['units'])
    assert all(u['qualifiers'].get('major','')!='학사지원팀' for u in upcoming['units']+leave['units'])
    with read_only(workspace.database) as db:
        assert not db.execute("SELECT 1 FROM information_assertions WHERE predicate='PUBLISHED_ON'").fetchone()


def test_scholarship_graduation_and_compound_leave_information_paths(workspace):
    rows=[
      ('guidance','award',{'title':'장학금 A','program_name':'장학금 A','sections':[{'locator':'s/1','heading':'지원 자격','text':'지원 자격: 공고의 성적 조건을 충족한 재학생.'},{'locator':'s/2','heading':'신청 방법','text':'신청 방법: nDRIMS에서 신청한다.'}]}),
      ('notices','award',{'title':'장학금 A 모집','program_name':'장학금 A','sections':[{'locator':'s/1','heading':'신청 기간','text':'신청 기간: 2026. 10. 19. ~ 2026. 10. 23.'},{'locator':'s/2','heading':'제출 서류','text':'제출 서류: 장학금 신청서와 재학증명서.'}]}),
      ('rules','degree',{'title':'2026학번 졸업 요건','entry_year':'2026','sections':[{'locator':'s/1','heading':'졸업 요건','text':'필수 이수 교과목: CSE101'},{'locator':'s/2','heading':'예외','text':'대체 인정 조건: 별도 심사를 받아야 한다. CSE102는 참고 과목이다.'}]}),
      ('courses','a',{'title':'자료구조','course_name':'자료구조','course_code':'CSE101','credit':'3','availability_status':'curriculum_only'}),
      ('courses','b',{'title':'데이터베이스','course_name':'데이터베이스','course_code':'CSE102','credit':'3'}),
      ('guidance','both',{'title':'휴학/복학','sections':[{'locator':'s/1','heading':'일반휴학','text':'### 일반휴학\n신청 대상: 1학년 2학기부터 신청 가능.'},{'locator':'s/2','heading':'복학','text':'### 복학\n신청 방법: 복학 신청 메뉴에서 신청한다.'}]}),
      ('meals','blank',{'title':'상록원 식단 미발표','date':'2026-11-01','restaurant':'상록원','menu_text':'','is_closed':False})]
    add_documents(workspace,rows);build(workspace)
    award=bundle(workspace,'장학금 A 자격 신청 기간 서류 방법')
    assert {'eligibility','period','documents','procedure'}.isdisjoint(award['missing_facets'])
    assert {'guidance:award','notices:award'}<= {u['document_key'] for u in award['units']}
    degree=bundle(workspace,'2026학번 졸업 요건 필수 교과목 학점',Scope(as_of=date(2026,10,17),entry_year=2026))
    assert any(r['predicate']=='EXPLICIT_COURSE_REQUIREMENT' for r in degree['references'])
    assert not any(r['predicate']=='EXPLICIT_COURSE_REQUIREMENT' and 'CSE102' in r['source_text'] for r in degree['references'])
    returned=bundle(workspace,'복학 방법')
    assert any('복학 신청 메뉴' in u['source_text'] for u in returned['units'])
    assert not any('1학년 2학기부터' in u['source_text'] for u in returned['units'])
    assert connected_search(workspace.database,'상록원 메뉴',scope=Scope(as_of=date(2026,11,1)))['hits']==[]


def test_guarded_normal_ingestion_maintains_information_projection(workspace,monkeypatch):
    from src.services import derivative_dag,ingest_runtime
    from src import config,database
    from contextlib import contextmanager
    add_documents(workspace,[('guidance','leave',{'title':'휴학','text':'신청 방법: nDRIMS에서 신청한다.'})])
    monkeypatch.setattr(config,'RAG_CONNECTED_MAINTENANCE_ENABLED',True)
    monkeypatch.setattr(database,'DATABASE_FILE',workspace.database)
    calls=[]
    @contextmanager
    def lock(**kwargs):calls.append(kwargs);yield
    monkeypatch.setattr(ingest_runtime,'serialized_ingest_write',lock)
    monkeypatch.setattr(derivative_dag,'build_canonical_lineage_report',lambda **kw:{'gate_passed':False,'violations':['fixture']})
    assert derivative_dag.run_post_ingestion_dag('meals',ingestion_run_id=1)['failed_stage']=='canonical_lineage'
    assert not calls
    monkeypatch.setattr(derivative_dag,'build_canonical_lineage_report',lambda **kw:{'gate_passed':True,'violations':[]})
    report=derivative_dag.run_post_ingestion_dag('meals',ingestion_run_id=2)
    assert report['status']=='success' and report['stages'][-1]['name']=='connected_information'
    assert calls[0]['operation']=='connected_information_projection'
    assert connected_search(workspace.database,'휴학 방법')['hits']


@pytest.mark.asyncio
@pytest.mark.parametrize('followup',[False,True])
@pytest.mark.parametrize('fusion',['rrf','weighted'])
@pytest.mark.parametrize('scenario',['compound','comparison'])
async def test_actual_source_information_reaches_both_http_transports(workspace,monkeypatch,followup,fusion,scenario):
    import httpx
    from dataclasses import replace
    from api import rag_service as service
    from tests.test_query_plan import configure
    from src import database
    from src.services import evidence_selector
    from src.services.evidence_selector import EvidenceSelectionDecision,EvidenceGroupDecision
    from src.services.grounding import GroundingResult
    import_actual(workspace);add_guides(workspace);build(workspace);configure(monkeypatch,None)
    resolved='2026학년도 2학기 전공결정 신청 대상 기간 방법과 예외' if scenario=='compound' else '2025년과 2026년 2학기 전공결정 신청기간을 비교해줘'
    question='그 절차와 기간은?' if followup else resolved
    if followup:
        original=service._plan_query
        async def plan(**kw):return replace(await original(**kw),query_for_retrieval=resolved,semantic_query=resolved)
        monkeypatch.setattr(service,'_plan_query',plan)
    monkeypatch.setattr(database,'DATABASE_FILE',workspace.database)
    monkeypatch.setattr(service.rag_config,'RAG_CONNECTED_RETRIEVAL_ENABLED',True)
    monkeypatch.setattr(service.rag_config,'HYBRID_FUSION_MODE',fusion)
    monkeypatch.setattr(service,'RAG_GROUNDING_CHECK_ENABLED',True)
    monkeypatch.setattr(service,'RAG_STREAM_BUFFER_UNTIL_GROUNDED',True)
    monkeypatch.setattr(service,'RAG_SUGGEST_FOLLOWUPS',False)
    monkeypatch.setattr(service,'_request_temporal_context',lambda _:service.TemporalContext(as_of=date(2026,10,17),academic_year=2026,semester=2,phase='학기중'))
    for name in ('_save_rag_evaluation_log','_update_observability_log','_update_grounding_log','append_manual_history'):monkeypatch.setattr(service,name,lambda *a,**k:None)
    async def retrieve(**kw):return [],False,[]
    async def enrich(**kw):return kw['frames'],[]
    monkeypatch.setattr(service,'_retrieve_frames_for_queries',retrieve)
    monkeypatch.setattr(service,'_enrich_staff_lookup_frames',enrich)
    selections=[];contexts=[];groundings=[]
    class Selector:
        async def ainvoke(self,messages):
            rows=json.loads(messages[-1].content.split('Candidate documents (JSON data):\n')[1]);selections.append(rows)
            assert '09:00' in str(rows)
            assert ('5지망' if scenario=='compound' else '2025. 10. 20.') in str(rows)
            assert '추후 안내 예정' in str(rows) and '2026. 11. 06.' in str(rows)
            assert '인원배정' not in str(rows) and '150%' not in str(rows)
            assert '1학기-2025학년도2학기외국인신입생' not in ''.join(str(rows).split())
            ids=[row['candidate_id'] for row in rows]
            return {'parsed':EvidenceSelectionDecision(groups=[EvidenceGroupDecision(document_ids=ids[n:n+3]) for n in range(0,len(ids),3)]),'raw':None,'parsing_error':None}
    monkeypatch.setattr(evidence_selector,'_structured_selector',lambda:Selector())
    async def generate(**kw):contexts.append(kw);return '전공결정 조건과 기간은 각 원문을 확인해 주세요. [문서1]'
    async def stream(**kw):yield await generate(**kw)
    async def ground(*args,**kw):groundings.append(args);return GroundingResult(checked=True,grounded=True,score=.95,relevance_score=.95,reason='source-bound deterministic fixture')
    monkeypatch.setattr(service,'generate_langchain_answer',generate)
    monkeypatch.setattr(service,'generate_langchain_answer_stream',stream)
    monkeypatch.setattr(service,'check_answer_grounding',ground)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),base_url='http://localhost') as client:
        a=await client.post('http://localhost/ask',json={'question':question,'session_id':'information-source-fixture'})
        s=await client.post('http://localhost/ask/stream',json={'question':question,'session_id':'information-source-fixture'})
    assert a.status_code==s.status_code==200
    events=[json.loads(line[6:]) for line in s.text.splitlines() if line.startswith('data: ')];completion=next(e for e in events if e['type']=='completion')
    result=a.json();assert result['verification_status']==completion['verification_status']=='passed'
    assert len(selections)==len(groundings)==2 and events[-1]['type']=='done'
    assert {'guidance','notices','attachments','rules'}<={row['dataset'] for row in selections[0]}
    assert any('source_applicability' in row for row in selections[0])
    assert '09:00' in str(contexts) and ('5지망' if scenario=='compound' else '2025. 10. 20.') in str(contexts)
    assert '인원배정' not in str(contexts) and '150%' not in str(contexts)
    assert '1학기-2025학년도2학기외국인신입생' not in ''.join(str(contexts).split())
    assert all('HAS_ELIGIBILITY' not in str(rows) and 'SAME_TOPIC_SCOPE_VARIANT' not in str(rows) for rows in selections)
    assert any(source['metadata'].get('information_unit_keys') for source in result['sources'])


def test_frontier_capacity_is_active_queue_not_lifetime_corpus_limit(workspace):
    sources=[{'url':f'https://www.dongguk.edu/page/{i}','owner':'공식 fixture','verification_url':'https://www.dongguk.edu/'} for i in range(8)]
    p=Policy({'schema_version':1,'approved_sources':sources});budgets=Budgets(pages=2,requests=2,frontier=2)
    def fetcher():return Fetcher(p,budgets,transport=lambda *a:Response(200,{'content-type':'text/html'},('<main><p>'+'공식 본문 안내 '*30+'</p></main>').encode()),resolver=lambda _:['93.184.216.34'],sleep=lambda _:None)
    c=Collector(workspace.checkpoint,p,budgets,fetcher=fetcher());c.seed=lambda:None
    for i,source in enumerate(sources):c.enqueue(source['url'],parent='https://www.dongguk.edu/page/parent',parent_document_key='notices:parent',kind='attachment' if i==7 else 'page',filename='plan.hwp' if i==7 else '',locator='body/a[1]')
    assert dict(c.db.execute('SELECT status,count(*) FROM frontier GROUP BY status'))=={'pending':2,'deferred':6}
    counts=[]
    for _ in range(4):
        c.fetcher=fetcher();report=c.run();counts.append(report['processed_this_run'])
        assert c.db.execute("SELECT count(*) FROM frontier WHERE status IN ('pending','fetching')").fetchone()[0]<=2
    assert counts==[2,2,2,2]
    assert c.db.execute('SELECT count(*) FROM frontier').fetchone()[0]==8
    assert not c.db.execute("SELECT 1 FROM frontier WHERE status='deferred'").fetchone()
    assert c.db.execute("SELECT parent_document_key FROM frontier WHERE url=?",(sources[-1]['url'],)).fetchone()[0]=='notices:parent'
    assert not report['frontier_truncated'];c.close()


def test_explicit_information_college_does_not_change_requester_authorization(workspace):
    rows=[]
    for college in ('공과대학','경영대학'):
        rows.append(('rules',college,{'title':'졸업 기준','college_name':college,'entry_year':'2026','text':'졸업 요건: 해당 대학의 조건을 충족해야 한다.'}))
    rows.append(('rules','restricted',{'title':'졸업 기준','college_name':'경영대학','entry_year':'2026','department':'경영학과','visibility':'department','text':'졸업 요건: 비공개 테스트 조건.'}))
    add_documents(workspace,rows);build(workspace)
    result=bundle(workspace,'공과대학 2026학번 졸업 요건')
    assert {u['document_key'] for u in result['units']}=={'rules:공과대학'}
    result=bundle(workspace,'경영대학 2026학번 졸업 요건',Scope(department='컴퓨터공학과',as_of=date(2026,10,17)))
    assert {u['document_key'] for u in result['units']}=={'rules:경영대학'}
    assert all('비공개' not in str(s) for s in result['subjects'])


def test_official_host_user_questions_require_authority_review_but_static_faq_works(workspace):
    qna='https://www.dongguk.edu/qna/list';faq='https://www.dongguk.edu/page/146'
    # URL/path from root's actual authority audit; synthetic body never copies
    # or prints personal Q&A rows.
    raw=('<html><title>동국대학교</title><main><h2>등록금 FAQ</h2><p>'+'등록금 납부 방법: 공식 안내를 확인하세요. '*20+'</p></main></html>').encode()
    assert extract_html(raw,qna).status=='requires_authority_review'
    assert extract_html(raw,faq).status=='extracted'
    p=Policy({'schema_version':1,'approved_sources':[{'url':u,'owner':'동국대학교','verification_url':'https://www.dongguk.edu/sitemap/detail'} for u in (qna,faq)]});b=Budgets(pages=2,requests=2)
    c=Collector(workspace.checkpoint,p,b,fetcher=Fetcher(p,b,transport=lambda *a:Response(200,{'content-type':'text/html'},raw),resolver=lambda _:['93.184.216.34'],sleep=lambda _:None));c.seed=lambda:None
    c.enqueue(qna);c.enqueue(faq);report=c.run();c.close()
    assert report['states']['requires_authority_review']==1
    imported=import_collection(workspace);assert len(imported['new'])==1 and not imported['failed']
    add_documents(workspace,[('guidance','previous-unreviewed',{'title':'등록금 납부 개인 질문','text':'등록금 납부 방법: 개인 질문은 공식 지침이 아니다.','_url':qna})]);build(workspace)
    with read_only(workspace.database) as db:
        assert not db.execute("SELECT 1 FROM information_units WHERE document_key='guidance:previous-unreviewed'").fetchone()
    hits=connected_search(workspace.database,'등록금 납부 방법')['hits']
    assert any(h['url']==faq for h in hits) and all(h['url']!=qna for h in hits)


def test_audience_identity_and_invalid_aggregate_course_rows_do_not_seed_subjects(workspace):
    add_documents(workspace,[('guidance','undergrad',{'title':'휴학','audience':'undergraduate','text':'신청 대상: 학부 재학생.'}),('guidance','graduate',{'title':'휴학','audience':'graduate','text':'신청 대상: 대학원 재학생.'}),('courses','aggregate',{'title':'1','course_code':'1','course_name':'1','credit':'2'})]);build(workspace)
    with read_only(workspace.database) as db:
        assert db.execute("SELECT count(DISTINCT subject_key) FROM information_units WHERE document_key IN ('guidance:undergrad','guidance:graduate')").fetchone()[0]==2
        assert not db.execute("SELECT 1 FROM information_assertions a JOIN information_units u USING(unit_key) WHERE u.document_key='courses:aggregate'").fetchone()
        assert db.execute("SELECT s.kind FROM information_subjects s JOIN information_units u USING(subject_key) WHERE document_key='courses:aggregate' LIMIT 1").fetchone()[0]=='unclassified_subject'
    result=bundle(workspace,'1학기 학점')
    assert not any(s['concept_key'].startswith('unclassified_course:') for s in result['subjects'])


def test_meal_serving_date_is_distinct_from_reference_and_publication(workspace,monkeypatch):
    from api import rag_service as service
    from src import database
    from src.utils.date_parser import QueryDateFilter
    rows=[]
    for key,day,menu,published in [('future','2026-10-18','내일 점심: 비빔밥','2026-10-16'),('past','2026-10-16','지난 점심: 된장찌개','2026-10-15'),('unpublished','2026-10-19','',''),('not-known','2026-10-18','아직 발표되지 않은 테스트 메뉴','2026-10-18')]:
        rows.append(('meals',key,{'title':'상록원 식단','restaurant':'상록원','date':day,'menu_text':menu,'upstream_published_at':published,'campus_scope':'seoul'}))
    add_documents(workspace,rows);build(workspace)
    assert connected_search(workspace.database,'상록원 점심 메뉴',scope=Scope(as_of=date(2026,10,17)))['hits']==[]
    for day,expected in [(date(2026,10,18),'meals:future'),(date(2026,10,16),'meals:past')]:
        result=connected_search(workspace.database,'상록원 점심 메뉴',scope=Scope(as_of=date(2026,10,17),meal_start=day,meal_end=day))
        assert [h['document_key'] for h in result['hits']]==[expected]
        assert result['hits'][0]['information_match']==1
    assert not connected_search(workspace.database,'상록원 메뉴',scope=Scope(as_of=date(2026,10,17),meal_start=date(2026,10,19)))['hits']
    monkeypatch.setattr(database,'DATABASE_FILE',workspace.database);monkeypatch.setattr(service.rag_config,'RAG_CONNECTED_RETRIEVAL_ENABLED',True)
    checks=[];monkeypatch.setattr(service,'_stale_direct_answer',lambda dataset,as_of:checks.append((dataset,as_of)))
    frames=service._connected_retrieval_frames(query='상록원 내일 점심 메뉴와 신청 절차',frames=[],major=None,where_filter=None,visibility_filter=None,date_filter=QueryDateFilter(start=date(2026,10,18),end=date(2026,10,18),label='tomorrow'),as_of=date(2026,10,17),entry_year=None,request_id='meal-scope-fixture')
    assert frames and [k for f in frames for k in f['document_key']]==['meals:future']
    assert checks==[('meals',date(2026,10,17))]
    selected=frames[0].copy();selected['evidence_group']=1;selected['citation_number']=1
    context=service._build_selected_evidence_context(selected,as_of=date(2026,10,17))
    assert '2026-10-18' in context and '2026-10-16' in context and '비빔밥' in context


def test_actual_guide_method_contexts_follow_local_semester_and_h_branches_stay_alternatives(workspace):
    import_actual(workspace);add_guides(workspace);build(workspace)
    result=bundle(workspace,'2026학년도 2학기 전공결정 신청 기간 방법')
    guide_units=[u for u in result['units'] if u['document_key']=='rules:E26' and set(u['facets'])&{'period','procedure'}]
    assert guide_units
    for unit in guide_units:
        for context in unit['qualifiers'].get('source_applicability_context',[]):
            assert context['scope'].get('semester')!='1'
            assert '1학기 - 2025학년도 2학기 외국인 신입생' not in context['source_text']
            assert context['branch_id'] and context['review_status']=='deterministic_explicit'
    methods=[u for u in result['units'] if u['document_key'].startswith('attachments:') and '5지망' in u['source_text']]
    assert methods
    contexts=methods[0]['qualifiers']['source_applicability_context']
    assert len({c['branch_id'] for c in contexts})==2
    assert all(c['branch_relation']=='source_alternative_condition' for c in contexts)
    assert any('졸업대상자가 아닌 일반 재학생도 신청 가능' in c['source_text'] for c in contexts)


def test_real_source_document_collected_at_proves_future_menu_observation(workspace):
    import src.services.connected_knowledge as knowledge
    add_documents(workspace,[('meals','captured',{'title':'상록원 내일 식단','restaurant':'상록원','date':'2026-10-18','menu_text':'비빔밥'})])
    with sqlite3.connect(workspace.database) as db:
        fields={r[1] for r in db.execute('PRAGMA table_info(source_documents)')}
        assert 'collected_at' in fields and 'raw_payload_json' in fields and 'first_seen_at' not in fields
        db.execute("UPDATE source_documents SET published_at='2026-10-18',collected_at='2026-10-16 12:00:00',raw_payload_json=normalized_payload_json")
    build(workspace);scope=Scope(as_of=date(2026,10,17),meal_start=date(2026,10,18),meal_end=date(2026,10,18))
    result=connected_search(workspace.database,'상록원 메뉴',scope=scope)
    assert [h['document_key'] for h in result['hits']]==['meals:captured']
    assert result['hits'][0]['published_at']==''
    assert result['hits'][0]['evidence_known_at']=='2026-10-16 12:00:00'
    assert result['hits'][0]['evidence_known_at_role']=='canonical_collected_at'
    with sqlite3.connect(workspace.database) as db:db.execute("UPDATE source_documents SET collected_at='2026-10-18 12:00:00'")
    assert connected_search(workspace.database,'상록원 메뉴',scope=scope)['status']=='fallback'
    build(workspace)
    assert connected_search(workspace.database,'상록원 메뉴',scope=scope)['hits']==[]


@pytest.mark.parametrize('parser,locator',[('hwp','BodyText/Section0'),('pdf','page/1'),('hwpx','Contents/section0.xml'),('docx','word/document.xml')])
def test_flattened_attachment_review_is_parser_shape_invariant(workspace,parser,locator):
    from src.collectors.official_extract import extract_attachment
    table=extract_attachment((F/'actual-major-plan.hwp').read_bytes(),'plan.hwp').sections[0]['text'][860:2268]
    add_documents(workspace,[('attachments','shape',{'title':'2026학년도 2학기 전공결정','_type':'official_attachment','parser_version':parser,'sections':[{'locator':locator+'/plain','heading':'신청 방법','text':'신청 방법: nDRIMS를 이용한다.'},{'locator':locator,'heading':'신청 대상','text':table}]})]);build(workspace)
    result=bundle(workspace,'전공결정 신청 대상 방법')
    assert any(u['review_status']=='needs_table_layout_review' for u in result['review_candidates'])
    assert all('인원배정' not in u['source_text'] for u in result['units'])
    assert any('nDRIMS를 이용한다' in u['source_text'] for u in result['units'])
    assert all('인원배정' not in c['source_text'] for u in result['units'] for c in u['qualifiers'].get('source_applicability_context',[]))
    assert all(h['document_key']!='attachments:shape' for h in connected_search(workspace.database,'인원배정')['hits'])


def test_private_duplicate_course_does_not_change_public_explicit_requirement(workspace):
    add_documents(workspace,[('courses','public',{'title':'공개 과목','course_name':'공개 과목','course_code':'CSE101','campus_scope':'seoul'}),('rules','public',{'title':'졸업 요건','text':'필수 이수 교과목: CSE101','campus_scope':'seoul'})]);build(workspace)
    def references():
        with read_only(workspace.database) as db:return [tuple(r) for r in db.execute("SELECT a.document_key,a.target_subject_key FROM information_references a WHERE predicate='EXPLICIT_COURSE_REQUIREMENT' AND document_key='rules:public'")]
    before=references();assert len(before)==1
    add_documents(workspace,[('courses','private',{'title':'학과 제한 과목','course_name':'학과 제한 과목','course_code':'CSE101','campus_scope':'seoul','visibility':'department','department':'컴퓨터공학과'}),('rules','private',{'title':'졸업 요건','text':'필수 이수 교과목: CSE101','campus_scope':'seoul','visibility':'department','department':'컴퓨터공학과'})]);build(workspace)
    assert references()==before
    result=bundle(workspace,'졸업 요건 필수 교과목',Scope(department='컴퓨터공학과',campus='seoul',as_of=date(2026,10,17)))
    assert any(r['document_key']=='rules:private' and r['predicate']=='EXPLICIT_COURSE_REQUIREMENT' for r in result['references'])
    with read_only(workspace.database) as db:
        for row in db.execute("SELECT a.document_key,s.qualifiers_json FROM information_references a JOIN information_subjects s ON s.subject_key=a.target_subject_key WHERE a.document_key='rules:public'"):assert json.loads(row[1])['visibility']=='public'


def test_runtime_explicit_historical_comparison_preserves_old_notice(workspace,monkeypatch):
    from api import rag_service as service
    from src import database
    add_documents(workspace,[('notices','old',{'title':'2025학년도 2학기 전공결정','academic_year':'2025','semester':'2','is_latest':False,'text':'신청 기간: 2025. 10. 20. ~ 2025. 10. 24.'}),('notices','new',{'title':'2026학년도 2학기 전공결정','academic_year':'2026','semester':'2','is_latest':True,'text':'신청 기간: 2026. 10. 19. ~ 2026. 10. 23.'})]);build(workspace)
    monkeypatch.setattr(database,'DATABASE_FILE',workspace.database);monkeypatch.setattr(service.rag_config,'RAG_CONNECTED_RETRIEVAL_ENABLED',True)
    frames=service._connected_retrieval_frames(query='2025년과 2026년 2학기 전공결정 신청기간 비교',frames=[],major=None,where_filter=None,visibility_filter=service._notice_visibility_where_filter(None),date_filter=None,as_of=date(2026,10,17),entry_year=2025,request_id='years')
    assert {key for frame in frames for key in frame['document_key']}=={'notices:old','notices:new'}
    assert 'notices:old' not in {h['document_key'] for h in connected_search(workspace.database,'전공결정 신청 기간',scope=Scope(as_of=date(2026,10,17)))['hits']}


@pytest.mark.asyncio
@pytest.mark.parametrize('requester',[None,'통계학과','경영학과'])
async def test_http_public_target_does_not_grant_private_department_access(workspace,monkeypatch,requester):
    import httpx
    from api import rag_service as service
    from tests.test_query_plan import configure
    from src import database
    from src.services import evidence_selector
    from src.services.evidence_selector import EvidenceSelectionDecision,EvidenceGroupDecision
    from src.services.grounding import GroundingResult
    add_documents(workspace,[('guidance','public-B',{'title':'경영학과 전공결정','department':'경영학과','text':'신청 방법: PUBLIC_ROUTE_B nDRIMS에서 공개 안내를 확인한다.'}),('guidance','private-B',{'title':'경영학과 전공결정','department':'경영학과','visibility':'department','text':'신청 방법: PRIVATE_ROUTE_B nDRIMS의 학과 제한 안내를 확인한다.'})]);build(workspace);configure(monkeypatch,None)
    monkeypatch.setattr(database,'DATABASE_FILE',workspace.database);monkeypatch.setattr(service.rag_config,'RAG_CONNECTED_RETRIEVAL_ENABLED',True);monkeypatch.setattr(service.rag_config,'HYBRID_FUSION_MODE','rrf')
    monkeypatch.setattr(service,'RAG_GROUNDING_CHECK_ENABLED',True);monkeypatch.setattr(service,'RAG_STREAM_BUFFER_UNTIL_GROUNDED',True);monkeypatch.setattr(service,'RAG_SUGGEST_FOLLOWUPS',False)
    monkeypatch.setattr(service,'_request_temporal_context',lambda _:service.TemporalContext(as_of=date(2026,10,17),academic_year=2026,semester=2,phase='학기중'))
    for name in ('_save_rag_evaluation_log','_update_observability_log','_update_grounding_log','append_manual_history'):monkeypatch.setattr(service,name,lambda *a,**k:None)
    async def retrieve(**kw):return [],False,[]
    async def enrich(**kw):return kw['frames'],[]
    monkeypatch.setattr(service,'_retrieve_frames_for_queries',retrieve);monkeypatch.setattr(service,'_enrich_staff_lookup_frames',enrich)
    seen=[];prompts=[]
    class Selector:
        async def ainvoke(self,messages):
            rows=json.loads(messages[-1].content.split('Candidate documents (JSON data):\n')[1]);seen.append(rows)
            assert 'PUBLIC_ROUTE_B' in str(rows)
            assert ('PRIVATE_ROUTE_B' in str(rows))==(requester=='경영학과')
            return {'parsed':EvidenceSelectionDecision(groups=[EvidenceGroupDecision(document_ids=[r['candidate_id'] for r in rows])]),'raw':None,'parsing_error':None}
    monkeypatch.setattr(evidence_selector,'_structured_selector',lambda:Selector())
    async def generate(**kw):prompts.append(kw);return '공개 안내 원문을 확인해 주세요. [문서1]'
    async def stream(**kw):yield await generate(**kw)
    async def ground(*args,**kw):return GroundingResult(checked=True,grounded=True,score=.95,relevance_score=.95,reason='scoped deterministic source fixture')
    monkeypatch.setattr(service,'generate_langchain_answer',generate);monkeypatch.setattr(service,'generate_langchain_answer_stream',stream);monkeypatch.setattr(service,'check_answer_grounding',ground)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),base_url='http://localhost') as client:
        payload={'question':'경영학과 전공결정 신청 방법','session_id':'public-target','major':requester}
        answer=await client.post('http://localhost/ask',json=payload);streamed=await client.post('http://localhost/ask/stream',json=payload)
    assert answer.status_code==streamed.status_code==200
    events=[json.loads(line[6:]) for line in streamed.text.splitlines() if line.startswith('data: ')];completion=next(e for e in events if e['type']=='completion')
    assert answer.json()['verification_status']==completion['verification_status']=='passed'
    assert len(seen)==len(prompts)==2
    assert ('PRIVATE_ROUTE_B' in str(prompts))==(requester=='경영학과')


def test_public_url_reference_survives_private_shadow_and_hidden_parent_is_not_scope_seed(workspace):
    url='https://www.dongguk.edu/page/reference'
    add_documents(workspace,[('guidance','source',{'title':'졸업 요건','text':'근거 규정: 공개 규정 확인','links':[{'url':url,'label':'공개 규정','locator':'body/a[1]'}]}),('rules','public-target',{'title':'졸업 요건','text':'졸업 요건: 공식 공개 규정 원문.','_url':url})]);build(workspace)
    def refs():
        with read_only(workspace.database) as db:
            return ([tuple(r) for r in db.execute("SELECT subject,object FROM connected_edges WHERE document_key='guidance:source' AND predicate='LINKS_TO'")],[tuple(r) for r in db.execute("SELECT unit_key,target_subject_key FROM information_references WHERE document_key='guidance:source'")])
    before=refs();assert all(before)
    add_documents(workspace,[('rules','private-shadow',{'title':'졸업 요건','text':'졸업 요건: 비공개 규정 원문.','_url':url,'department':'경영학과','visibility':'department'}),('guidance','hidden-child',{'title':'마케팅학과 졸업 요건','department':'마케팅학과','parent_document_key':'rules:private-shadow','text':'졸업 요건: 제한된 상위 문서의 안내.'})]);build(workspace)
    assert refs()==before
    result=bundle(workspace,'마케팅학과 졸업 요건')
    assert result['target_resolution']['department'] is None
    assert 'guidance:hidden-child' not in {u['document_key'] for u in result['units']}


def test_actual_hwp_lexical_plain_units_survive_review_boundaries_with_exact_spans(workspace):
    import_actual(workspace);build(workspace)
    for query,expected in [('5지망','5지망까지 모두 기재 필요'),('09:00','09:00')]:
        result=connected_search(workspace.database,query,scope=Scope(as_of=date(2026,10,17)),connected=False)
        hits=[h for h in result['hits'] if h['dataset']=='attachments']
        assert hits and expected in str(hits[0]['information_evidence_units'])
        assert all('인원배정' not in u['source_text'] for u in hits[0]['information_evidence_units'])
        with read_only(workspace.database) as db:
            d=db.execute('SELECT normalized_payload_json FROM source_documents WHERE document_key=?',(hits[0]['document_key'],)).fetchone()[0];text=json.loads(d)['sections'][0]['text']
            start,end=map(int,hits[0]['source_locator'].rsplit('#chars=',1)[1].split(':'))
            assert text[start:end]==hits[0]['chunk_text']
    assert not any(h['dataset']=='attachments' for h in connected_search(workspace.database,'인원배정',scope=Scope(as_of=date(2026,10,17)),connected=False)['hits'])


@pytest.mark.parametrize('prefix',['','요청자 학과: 통계학과\n'])
def test_complete_condition_unit_budget_is_terminal_and_preserves_other_units(prefix):
    import pandas as pd
    from api import rag_service as service
    from src.services.evidence_selector import _bounded_candidates
    long='신청 대상: 모든 재학생이 신청 가능하다. '+('설명 '*2400)+'단, 외국인 신입생은 제외한다.'
    short='신청 대상: 재학생. 단, 외국인 신입생은 제외한다.'
    def row(text,key):return {'dataset':'guidance','title':'휴학 신청 대상','chunk_id':key,'chunk_text':text,'information_match':1,'citation_number':1,'evidence_group':1,'information_evidence_units':[{'unit_key':key,'source_text':text,'qualifiers':{},'facets':['eligibility','exception']}]}
    selected=pd.DataFrame([row(long,'long')]);assert service._build_selected_evidence_context(selected,prefix=prefix)==''
    assert selected.attrs['information_context_omitted_unit_keys']==['long'] and selected.attrs['information_context_has_evidence'] is False
    assert _bounded_candidates([{'candidate_id':'long','information_match':1,'text':long}])==[]
    selected=pd.DataFrame([row(long,'long'),row(short,'short')]);selected.loc[1,'citation_number']=2
    context=service._build_selected_evidence_context(selected,prefix=prefix)
    assert short in context and long not in context and len(context)<=service.MAX_CONTEXT_LENGTH
    bounded=_bounded_candidates([{'candidate_id':'long','information_match':1,'text':long},{'candidate_id':'short','information_match':1,'text':short}])
    assert len(bounded)==1 and bounded[0]['candidate_id']=='short' and short==bounded[0]['text']


@pytest.mark.asyncio
@pytest.mark.parametrize('oversized',[False,True])
@pytest.mark.parametrize('major',[None,'통계학과'])
async def test_http_complete_conditions_or_explicit_no_answer_without_prefix_fallback(workspace,monkeypatch,oversized,major):
    import httpx
    from api import rag_service as service
    from tests.test_query_plan import configure
    from src import database
    from src.services import evidence_selector
    from src.services.evidence_selector import EvidenceSelectionDecision,EvidenceGroupDecision
    from src.services.grounding import GroundingResult
    body='신청 대상: 모든 재학생이 신청 가능하다. '+('설명 '*2400 if oversized else '원문의 일반 조건. ')+'단, 외국인 신입생은 제외한다.'
    add_documents(workspace,[('guidance','conditions',{'title':'휴학 신청 대상','text':body})]);build(workspace);configure(monkeypatch,None)
    monkeypatch.setattr(database,'DATABASE_FILE',workspace.database);monkeypatch.setattr(service.rag_config,'RAG_CONNECTED_RETRIEVAL_ENABLED',True);monkeypatch.setattr(service.rag_config,'HYBRID_FUSION_MODE','rrf')
    monkeypatch.setattr(service,'RAG_GROUNDING_CHECK_ENABLED',True);monkeypatch.setattr(service,'RAG_STREAM_BUFFER_UNTIL_GROUNDED',True);monkeypatch.setattr(service,'RAG_SUGGEST_FOLLOWUPS',False)
    monkeypatch.setattr(service,'_request_temporal_context',lambda _:service.TemporalContext(as_of=date(2026,10,17),academic_year=2026,semester=2,phase='학기중'))
    for name in ('_save_rag_evaluation_log','_update_observability_log','_update_grounding_log','append_manual_history'):monkeypatch.setattr(service,name,lambda *a,**k:None)
    async def retrieve(**kw):return [],False,[]
    async def enrich(**kw):return kw['frames'],[]
    monkeypatch.setattr(service,'_retrieve_frames_for_queries',retrieve);monkeypatch.setattr(service,'_enrich_staff_lookup_frames',enrich)
    seen=[];contexts=[]
    class Selector:
        async def ainvoke(self,messages):
            rows=json.loads(messages[-1].content.split('Candidate documents (JSON data):\n')[1]);seen.append(rows)
            assert not oversized and '단, 외국인 신입생은 제외한다.' in str(rows)
            return {'parsed':EvidenceSelectionDecision(groups=[EvidenceGroupDecision(document_ids=[r['candidate_id'] for r in rows])]),'raw':None,'parsing_error':None}
    monkeypatch.setattr(evidence_selector,'_structured_selector',lambda:Selector())
    async def generate(**kw):
        contexts.append(kw);assert not oversized and '단, 외국인 신입생은 제외한다.' in kw['context']
        return '원문은 외국인 신입생을 제외한다고 명시합니다. [문서1]'
    async def stream(**kw):yield await generate(**kw)
    async def ground(*args,**kw):return GroundingResult(checked=True,grounded=True,score=.95,relevance_score=.95,reason='complete-condition deterministic fixture')
    monkeypatch.setattr(service,'generate_langchain_answer',generate);monkeypatch.setattr(service,'generate_langchain_answer_stream',stream);monkeypatch.setattr(service,'check_answer_grounding',ground)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),base_url='http://localhost') as client:
        payload={'question':'휴학 신청 자격','session_id':'whole-unit-budget','major':major}
        a=await client.post('http://localhost/ask',json=payload);s=await client.post('http://localhost/ask/stream',json=payload)
    assert a.status_code==s.status_code==200
    events=[json.loads(line[6:]) for line in s.text.splitlines() if line.startswith('data: ')];completion=next(e for e in events if e['type']=='completion')
    if oversized:
        assert not seen and not contexts
        assert a.json()['sources']==completion['sources']==[]
        assert a.json()['fallback_reason']==completion['fallback_reason']=='information_budget_exceeded'
        assert a.json()['verification_status']!='passed'
        assert '모든 재학생이 신청 가능하다' not in a.json()['answer']
    else:assert len(seen)==len(contexts)==2 and a.json()['verification_status']==completion['verification_status']=='passed'


@pytest.mark.parametrize('title,body,expected',[('2026학년도 2학기 대학원 휴학 신청 안내','신청 대상: 대학원 석사/박사 재학생.','graduate'),('동국대학교 - 대학/대학원 - 대학 - 경영대학','학부 안내. 문의처: 대학원실에도 문의할 수 있습니다.','undergraduate'),('[3-1-5] 대학원 학사운영규정 - 2025.01.01.hwp','신청 대상: 대학원 석사/박사 재학생.','graduate'),('휴학 안내','대학원실 전화와 일반 문의를 안내합니다.','unknown'),('학부 및 대학원 휴학 신청 안내','공식 적용 대상에 관한 안내.','common')])
def test_import_audience_uses_explicit_identity_not_incidental_body(workspace,title,body,expected):
    url='https://www.dongguk.edu/page/degree-fixture';raw=('<html><title>'+title+'</title><main><h2>공식 안내</h2><p>'+body*15+'</p></main></html>').encode()
    p=Policy({'schema_version':1,'approved_sources':[{'url':url,'owner':'동국대학교','verification_url':'https://www.dongguk.edu/sitemap/detail'}]});b=Budgets(pages=1,requests=1)
    c=Collector(workspace.checkpoint,p,b,fetcher=Fetcher(p,b,transport=lambda *a:Response(200,{'content-type':'text/html'},raw),resolver=lambda _:['93.184.216.34'],sleep=lambda _:None));c.seed=lambda:None;c.enqueue(url);c.run();c.close()
    report=import_collection(workspace);assert len(report['new'])==1 and not report['failed']
    with sqlite3.connect(workspace.database) as db:payload=json.loads(db.execute('SELECT normalized_payload_json FROM source_documents').fetchone()[0])
    assert payload['audience']==expected and payload['audience_evidence']['audience']==expected
    build(workspace)
    from src.services.connected_knowledge import eligible,document_metadata
    with read_only(workspace.database) as db:
        reader=ConnectedReader(db,scope=Scope(as_of=date(2026,10,17)));d=next(iter(reader.documents.values()))
        assert document_metadata(d)['audience']==expected
        assert eligible(d,Scope(audience='undergraduate',as_of=date(2026,10,17)))==(expected!='graduate')
        if expected=='graduate':assert eligible(d,Scope(audience='graduate',as_of=date(2026,10,17)))


def test_actual_graduate_identity_pages_and_labelled_conditions_have_scoped_degree(workspace):
    from src.services.connected_knowledge import document_metadata,eligible
    for index,row in enumerate(json.loads((F/'actual-degree-pages.json').read_text())):
        add_documents(workspace,[('guidance','actual-degree-'+str(index),{'title':row['title'],'sections':row['sections'],'audience':'common','_type':'official_public_page','_url':row['url']})])
    add_documents(workspace,[('guidance','labelled',{'title':'휴학 안내','sections':[{'locator':'body/1','heading':'신청 대상','text':'### 신청 대상\n대학원 석사/박사 재학생'},{'locator':'body/2','heading':'신청 방법','text':'### 신청 방법\nnDRIMS에서 신청합니다.'}],'audience':'unknown'})]);build(workspace)
    with read_only(workspace.database) as db:
        reader=ConnectedReader(db,scope=Scope(as_of=date(2026,10,17)))
        for key,d in reader.documents.items():
            if key.startswith('guidance:actual-degree-'):
                assert document_metadata(d)['audience']=='graduate'
                assert not eligible(d,Scope(audience='undergraduate',as_of=date(2026,10,17)))
    result=bundle(workspace,'휴학 신청 자격 방법',Scope(audience='undergraduate',as_of=date(2026,10,17)))
    assert all(u['document_key']!='guidance:labelled' for u in result['units'])
    result=bundle(workspace,'대학원 휴학 신청 자격 방법',Scope(as_of=date(2026,10,17)))
    assert any(u['document_key']=='guidance:labelled' and u['qualifiers']['audience']=='graduate' for u in result['units'])


@pytest.mark.asyncio
@pytest.mark.parametrize('graduate',[False,True])
async def test_http_degree_target_scope_reaches_selector_and_prompt(workspace,monkeypatch,graduate):
    import httpx
    from api import rag_service as service
    from tests.test_query_plan import configure
    from src import database
    from src.services import evidence_selector
    from src.services.evidence_selector import EvidenceSelectionDecision,EvidenceGroupDecision
    from src.services.grounding import GroundingResult
    add_documents(workspace,[('guidance','UG',{'title':'동국대학교 - 대학/대학원 - 대학 - 휴학 신청 안내','text':'신청 대상: 학부 재학생\n신청 방법: UG_DEGREE nDRIMS에서 신청한다.','audience':'unknown'}),('guidance','GR',{'title':'2026학년도 대학원 휴학 신청 안내','text':'신청 대상: 대학원 석사/박사 재학생\n신청 방법: GR_DEGREE nDRIMS에서 신청한다.','audience':'common'}),('guidance','UG-general',{'title':'휴학 신청 안내','text':'신청 대상: 학부 재학생\n신청 방법: UG_DEGREE nDRIMS 일반 안내.','audience':'undergraduate'})]);build(workspace);configure(monkeypatch,None)
    monkeypatch.setattr(database,'DATABASE_FILE',workspace.database);monkeypatch.setattr(service.rag_config,'RAG_CONNECTED_RETRIEVAL_ENABLED',True);monkeypatch.setattr(service.rag_config,'HYBRID_FUSION_MODE','rrf')
    monkeypatch.setattr(service,'RAG_GROUNDING_CHECK_ENABLED',True);monkeypatch.setattr(service,'RAG_STREAM_BUFFER_UNTIL_GROUNDED',True);monkeypatch.setattr(service,'RAG_SUGGEST_FOLLOWUPS',False)
    monkeypatch.setattr(service,'_request_temporal_context',lambda _:service.TemporalContext(as_of=date(2026,10,17),academic_year=2026,semester=2,phase='학기중'))
    for name in ('_save_rag_evaluation_log','_update_observability_log','_update_grounding_log','append_manual_history'):monkeypatch.setattr(service,name,lambda *a,**k:None)
    async def retrieve(**kw):return [],False,[]
    async def enrich(**kw):return kw['frames'],[]
    monkeypatch.setattr(service,'_retrieve_frames_for_queries',retrieve);monkeypatch.setattr(service,'_enrich_staff_lookup_frames',enrich)
    expected='graduate' if graduate else 'undergraduate';token='GR_DEGREE' if graduate else 'UG_DEGREE';forbidden='UG_DEGREE' if graduate else 'GR_DEGREE';seen=[];prompts=[]
    class Selector:
        async def ainvoke(self,messages):
            rows=json.loads(messages[-1].content.split('Candidate documents (JSON data):\n')[1]);seen.append(rows)
            assert token in str(rows) and forbidden not in str(rows)
            assert all(json.loads(r['source_applicability'])['audience']==expected for r in rows)
            return {'parsed':EvidenceSelectionDecision(groups=[EvidenceGroupDecision(document_ids=[r['candidate_id'] for r in rows])]),'raw':None,'parsing_error':None}
    monkeypatch.setattr(evidence_selector,'_structured_selector',lambda:Selector())
    async def generate(**kw):
        prompts.append(kw);assert token in kw['context'] and forbidden not in kw['context'];assert '"audience": "'+expected+'"' in kw['context']
        return '해당 학위과정의 원문 안내를 확인해 주세요. [문서1]'
    async def stream(**kw):yield await generate(**kw)
    async def ground(*args,**kw):return GroundingResult(checked=True,grounded=True,score=.95,relevance_score=.95,reason='degree-scope deterministic source fixture')
    monkeypatch.setattr(service,'generate_langchain_answer',generate);monkeypatch.setattr(service,'generate_langchain_answer_stream',stream);monkeypatch.setattr(service,'check_answer_grounding',ground)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),base_url='http://localhost') as client:
        payload={'question':('대학원 ' if graduate else '')+'휴학 신청 방법','session_id':'degree-scope','major':'통계학과'}
        a=await client.post('http://localhost/ask',json=payload);s=await client.post('http://localhost/ask/stream',json=payload)
    assert a.status_code==s.status_code==200
    events=[json.loads(line[6:]) for line in s.text.splitlines() if line.startswith('data: ')];completion=next(e for e in events if e['type']=='completion')
    assert len(seen)==len(prompts)==2 and a.json()['verification_status']==completion['verification_status']=='passed'


def test_resumed_policy_pending_overflow_is_deferred_without_losing_metadata(workspace):
    p=Policy({'schema_version':1,'approved_sources':[{'url':'https://www.dongguk.edu/','allow_descendants':True,'seed':False,'owner':'공식 fixture','verification_url':'https://www.dongguk.edu/sitemap/detail'}]})
    c=Collector(workspace.checkpoint,p,Budgets(frontier=2))
    for i in range(4):c.enqueue(f'https://www.dongguk.edu/page/{i}',parent_document_key='notices:parent',locator='body/link')
    c.db.execute("UPDATE frontier SET status='pending'");c.db.commit()  # legacy/policy-upgrade checkpoint shape
    c._admit_deferred()
    assert dict(c.db.execute('SELECT status,count(*) FROM frontier GROUP BY status'))=={'pending':2,'deferred':2}
    assert c.db.execute("SELECT count(*) FROM frontier WHERE parent_document_key='notices:parent' AND locator='body/link'").fetchone()[0]==4
    c.close()


def test_structured_mixed_degree_headings_scope_units_without_broadcasting_page(workspace):
    add_documents(workspace,[('guidance','mixed-degree',{'title':'학부 및 대학원 휴학 안내','audience':'unknown','sections':[{'locator':'body/UG','heading':'학부생 휴학 신청','text':'신청 방법: UG_SECTION nDRIMS에서 신청한다.'},{'locator':'body/GR','heading':'대학원 휴학 신청','text':'신청 방법: GR_SECTION nDRIMS에서 신청한다.'}]})]);build(workspace)
    for degree,expected,forbidden in [('undergraduate','UG_SECTION','GR_SECTION'),('graduate','GR_SECTION','UG_SECTION')]:
        result=bundle(workspace,'휴학 신청 방법',Scope(audience=degree,as_of=date(2026,10,17)))
        assert expected in str(result['units']) and forbidden not in str(result['units'])
        assert all(u['qualifiers']['audience']==degree for u in result['units'])


def test_generic_scholarship_titles_do_not_become_one_identified_programme(workspace):
    add_documents(workspace,[('guidance','generic-a',{'title':'장학금 안내','text':'신청 대상: 성적 조건을 충족한 학생.'}),('notices','generic-b',{'title':'장학금 안내','text':'신청 기간: 2026. 10. 19. ~ 2026. 10. 23.'})]);report=build(workspace)
    assert len(report['information']['unresolved_programme_documents'])==2
    with read_only(workspace.database) as db:
        assert not db.execute("SELECT 1 FROM information_subjects WHERE kind='scholarship_program'").fetchone()
        assert db.execute('SELECT count(DISTINCT subject_key) FROM information_units').fetchone()[0]==2
    result=bundle(workspace,'장학금 신청 자격 기간')
    assert result['subjects']==[] and result['units']==[] and result['missing_facets']


def test_cli_coverage_distinguishes_missing_stale_and_export_never_overwrites_db(workspace,capsys):
    from scripts.connected_official_knowledge import main
    args=['--staging-dir',str(workspace.directory)]
    assert main(args+['coverage'])==1
    assert json.loads(capsys.readouterr().out)['status']=='missing_graph'
    add_documents(workspace,[('guidance','one',{'title':'휴학','text':'신청 방법: nDRIMS를 이용한다.'})]);build(workspace)
    before=hashlib.sha256(workspace.database.read_bytes()).hexdigest()
    assert main(args+['export','--output',str(workspace.database)])==2
    capsys.readouterr();assert hashlib.sha256(workspace.database.read_bytes()).hexdigest()==before
    output=workspace.directory/'graph.json'
    assert main(args+['export','--output',str(output)])==0
    capsys.readouterr();saved=output.read_bytes()
    with sqlite3.connect(workspace.database) as db:db.execute("UPDATE source_documents SET status='hidden'")
    assert main(args+['coverage'])==1
    assert json.loads(capsys.readouterr().out)['status']=='fallback'
    assert main(args+['export','--output',str(output)])==2
    capsys.readouterr();assert output.read_bytes()==saved


def test_compound_subjects_reserve_each_requested_information_facet(workspace):
    add_documents(workspace,[('guidance','both-procedures',{'title':'휴학/복학','sections':[{'locator':'body/leave','heading':'휴학','text':'### 휴학\n신청 방법: LEAVE_CHANNEL nDRIMS에서 신청한다.'},{'locator':'body/return','heading':'복학','text':'### 복학\n신청 방법: RETURN_CHANNEL nDRIMS에서 신청한다.'}]}),('meals','today',{'title':'상록원 식단','restaurant':'상록원','date':'2026-10-17','menu_text':'TODAY_MENU 비빔밥'})]);build(workspace)
    result=bundle(workspace,'휴학과 복학 신청 방법')
    assert 'LEAVE_CHANNEL' in str(result['units']) and 'RETURN_CHANNEL' in str(result['units'])
    assert all('procedure' in result['topic_coverage'][topic]['selected_facets'] for topic in ('leave','return'))
    result=connected_search(workspace.database,'휴학 신청 방법과 상록원 오늘 점심 메뉴',scope=Scope(as_of=date(2026,10,17)))
    assert 'TODAY_MENU' in str(result['hits']) and 'LEAVE_CHANNEL' in str(result['hits'])
    assert {'guidance','meals'}<={h['dataset'] for h in result['hits']}


def test_application_menu_with_date_does_not_override_academic_intent():
    from src.services.information_graph import concepts_in
    assert [c['key'] for c in concepts_in('AI소프트웨어융합학부 지망 오늘 신청 메뉴는 뭐야')]==['major_selection']
    assert not concepts_in('오늘 신청 메뉴')
    assert [c['key'] for c in concepts_in('오늘 메뉴')]==['meal']
