"""Current-main integration. Deterministic models test protocols, not quality."""
from datetime import date
import json
import sqlite3
from types import SimpleNamespace
import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from api import rag_service as service
from src.services.connected_knowledge import Scope, connected_search, referenced_information
from src.services.information_scope import temporal_intent
from tests.test_connected_official_knowledge import workspace, add_documents, build
from tests.test_information_graph import add_guides, import_actual, bundle, F
from tests.test_conversation_continuity import install_local_history
from src.services import langchain_chat as chat, evidence_selector
from src.services.evidence_selector import EvidenceSelectionDecision, EvidenceGroupDecision
from src.services.grounding import GroundingResult
AS_OF=date(2026,10,17)

@pytest.mark.parametrize('query,terms,calendar',[
 ('2027년 봄 졸업대상자 영어성적 제출 마감일',(),(2027,)),
 ('2026-2-14 신청 일정',(),(2026,)),('2026년 2월 신청 일정',(),(2026,)),
 ('2026학년도 2학기 휴학',((2026,'2'),),()),('2026-2 휴학',((2026,'2'),),()),
 ('2026학년도 1학기와 2학기 전공결정 기간 비교',((2026,'1'),(2026,'2')),()),
 ('2025-1과 2026-2 전공결정 기간 비교',((2025,'1'),(2026,'2')),()),
 ('2025년과 2026년 2학기 전공결정 비교',((2025,'2'),(2026,'2')),()),
])
def test_typed_temporal_intent(query,terms,calendar):
    intent=temporal_intent(query)
    assert intent.academic_terms==terms and intent.calendar_years==calendar


def test_actual_two_term_and_paired_term_coverage(workspace):
    add_guides(workspace);build(workspace)
    result=bundle(workspace,'2026학년도 1학기와 2학기 전공결정 신청 기간을 비교해줘')
    text=str(result['units']);assert '2026. 04. 13.' in text and '2026. 10. 19.' in text
    for term in ('1','2'):assert result['requested_scope_coverage'][f'academic:2026-{term}']['period']
    pairs=bundle(workspace,'2025-1과 2026-2 전공결정 신청 기간을 비교해줘')
    assert set(pairs['requested_scope_coverage'])=={'academic:2025-1','academic:2026-2'}
    for unit in pairs['units']:
        q=unit['qualifiers'];assert (str(q.get('academic_year')),q.get('semester')) not in {('2025','2'),('2026','1')}


def test_actual_conditional_statement_queryable_in_both_directions(workspace):
    import_actual(workspace);add_guides(workspace);build(workspace)
    for query in ('2026년 2학기에 입학한 외국인 신입생도 이번 10월에 전공결정을 신청해?', '2027학년도 1학기에 전공결정을 신청할 수 있다고 안내된 외국인 신입생은 어느 입학 학기야?'):
        result=bundle(workspace,query)
        assert len({u['document_key'] for u in result['units'] if u['qualifiers'].get('conditional_term_notes')})>=3
        assert all('2026' in n['source_text'] and '2027' in n['source_text'] for n in result['conditional_term_notes'])
        assert result['missing_calendar_window']
        assert not any('application_window' in u['qualifiers'].get('temporal_subroles',[]) and not u['qualifiers'].get('conditional_term_notes') for u in result['units'])


def test_multiple_public_targets_and_degrees_preserve_authority(workspace):
    rows=[('guidance',m,{'title':m+' 졸업 요건','major':m,'text':'졸업 요건: '+m+' PUBLIC_RULE 필수 조건을 충족해야 한다.'}) for m in ('통계학과','경영학과')]
    rows.extend([('rules','private',{'title':'경영학과 졸업 요건','major':'경영학과','department':'경영학과','visibility':'department','text':'졸업 요건: PRIVATE_B 조건이다.'}),('guidance','mixed',{'title':'학부 및 대학원 휴학 안내','audience':'unknown','sections':[{'heading':'학부생 휴학 신청','locator':'UG','text':'신청 방법: UG_RULE 포털에서 신청한다.'},{'heading':'대학원생 휴학 신청','locator':'GR','text':'신청 방법: GR_RULE 행정실에 신청한다.'}]})])
    add_documents(workspace,rows);build(workspace)
    for requester in (None,'통계학과'):
        result=bundle(workspace,'통계학과와 경영학과 졸업요건을 비교해줘',Scope(department=requester,as_of=AS_OF))
        assert result['target_resolution']['status']=='resolved_public_targets'
        assert {u['document_key'] for u in result['units']}=={'guidance:통계학과','guidance:경영학과'}
        assert 'PRIVATE_B' not in str(result)
    result=bundle(workspace,'학부와 대학원 휴학 신청 방법을 비교해줘')
    assert {'undergraduate','graduate'}=={u['qualifiers']['audience'] for u in result['units']}
    assert all(result['requested_scope_coverage'][f'audience:{d}']['procedure'] for d in ('undergraduate','graduate'))


@pytest.fixture
def runtime(workspace,monkeypatch):
    from src import database
    engine=create_engine('sqlite:///'+str(workspace.database));monkeypatch.setattr(service,'SessionLocal',sessionmaker(bind=engine));monkeypatch.setattr(database,'DATABASE_FILE',workspace.database)
    monkeypatch.setattr(service.rag_config,'RAG_CONNECTED_RETRIEVAL_ENABLED',True)
    for flag in ('RAG_STRUCTURED_RETRIEVAL_ENABLED','RAG_ONTOLOGY_CANDIDATES_ENABLED','RAG_ONTOLOGY_SHADOW_ENABLED'):monkeypatch.setattr(service.rag_config,flag,False)
    for name,value in [('USE_QUERY_ANALYSIS',False),('RAG_SEMANTIC_CACHE_ENABLED',False),('RAG_GROUNDING_CHECK_ENABLED',True),('RAG_STREAM_BUFFER_UNTIL_GROUNDED',True),('RAG_SUGGEST_FOLLOWUPS',False)]:monkeypatch.setattr(service,name,value)
    monkeypatch.setattr(service,'_request_temporal_context',lambda _:service.TemporalContext(as_of=AS_OF,academic_year=2026,semester=2,phase='학기중'))
    monkeypatch.setattr(service,'_prime_query_embedding',lambda *a:False)
    for name in ('_try_direct_answer','_chat_course_recommendation','_stale_direct_answer'):monkeypatch.setattr(service,name,lambda *a:None)
    for name in ('_save_rag_evaluation_log','_update_observability_log','_update_grounding_log'):monkeypatch.setattr(service,name,lambda *a,**kw:None)
    async def retrieve(**kw):return [],False,[]
    async def enrich(**kw):return kw['frames'],[]
    monkeypatch.setattr(service,'_retrieve_frames',retrieve);monkeypatch.setattr(service,'_enrich_staff_lookup_frames',enrich)
    install_local_history(monkeypatch);calls=SimpleNamespace(contexts=[],selector=[],grounding=[])
    class Selector:
        async def ainvoke(self,messages):
            rows=json.loads(messages[-1].content.split('Candidate documents (JSON data):\n')[1]);calls.selector.append(rows);ids=[r['candidate_id'] for r in rows]
            return {'parsed':EvidenceSelectionDecision(groups=[EvidenceGroupDecision(document_ids=ids[n:n+3]) for n in range(0,len(ids),3)]),'raw':None,'parsing_error':None}
    monkeypatch.setattr(evidence_selector,'_structured_selector',lambda:Selector())
    async def generate(**kw):calls.contexts.append(kw);return '현재 원문을 확인해 주세요. [문서1]'
    async def stream(**kw):yield await generate(**kw)
    async def ground(*args,**kw):calls.grounding.append(args);return GroundingResult(checked=True,grounded=True,score=1.,relevance_score=1.,reason='deterministic integration protocol fixture')
    monkeypatch.setattr(service,'generate_langchain_answer',generate);monkeypatch.setattr(service,'generate_langchain_answer_stream',stream);monkeypatch.setattr(service,'check_answer_grounding',ground)
    yield calls
    engine.dispose()

async def endpoint(question,mode='ask',session='integration',major=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),base_url='http://localhost') as client:
        response=await client.post('http://localhost/ask'+('/stream' if mode=='stream' else ''),json={'question':question,'session_id':session,'major':major})
    assert response.status_code==200
    if mode=='ask':return response.json()
    events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert events[-1]['type']=='done'
    result=next(e for e in events if e['type']=='completion')
    result.setdefault('answer',''.join(e.get('content','') for e in events if e['type'] in {'text','token'}))
    return result

@pytest.mark.asyncio
@pytest.mark.parametrize('dataset',['guidance','attachments'])
@pytest.mark.parametrize('query',['그 문서 다시 보여줘','그 신청 방법은?','두 번째 문서 자세히','공식 휴학 신청 안내 자세히 알려줘'])
async def test_new_current_reference_real_transports(workspace,runtime,monkeypatch,dataset,query):
    add_documents(workspace,[(dataset,'anchor',{'title':'공식 휴학 신청 안내','text':'신청 방법: CURRENT_ANCHOR 포털에서 신청한다.'}),('guidance','other',{'title':'휴학 신청 안내','text':'신청 방법: UNREQUESTED_SAME_TOPIC 다른 절차이다.'})]);build(workspace)
    monkeypatch.setattr(service,'_ensure_dataset',lambda *a:pytest.fail('new datasets have no legacy artifact loader'))
    for mode in ('ask','stream'):
        chat.append_manual_history(mode,'휴학 신청 방법 알려줘','OLD ANSWER',[{'source':dataset,'title':'공식 휴학 신청 안내','citation_number':2,'chunk_id':'old-chunk','metadata':{'doc_id':dataset+':anchor'},'snippet':'STALE_MEMORY_FACT'}])
        result=await endpoint(query,mode,mode)
        assert result['verification_status']=='passed'
        assert len(result['sources'])==1 and result['sources'][0]['source']==dataset
        context=runtime.contexts[-1]['context'];assert 'CURRENT_ANCHOR' in context and 'UNREQUESTED' not in context and 'STALE_MEMORY' not in context
    assert len(runtime.selector)==len(runtime.grounding)==2

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ask','stream'])
async def test_actual_calendar_and_two_terms_reach_generation(workspace,runtime,mode):
    fixture=json.loads((F/'actual-calendar-year-role.json').read_text());event=next(e for e in fixture['events'] if '영어성적' in e['내용'] and e['start']=='2027-01-15')
    add_documents(workspace,[('schedule','actual',{'title':event['내용'],'내용':event['내용'],'academic_year':event['학년도'],'start_date':event['start'],'end_date':event['end'],'_url':fixture['source_url']})]);add_guides(workspace);build(workspace)
    result=await endpoint('2027년 봄 졸업대상자 영어성적 제출 마감일을 알려줘',mode,'calendar')
    assert result['verification_status']=='passed' and '2027-01-15' in runtime.contexts[-1]['context']
    result=await endpoint('2026학년도 1학기와 2학기 전공결정 신청 기간을 비교해줘',mode,'terms')
    assert result['verification_status']=='passed'
    context=runtime.contexts[-1]['context'];assert '2026. 04. 13.' in context and '2026. 10. 19.' in context

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ask','stream'])
async def test_future_canonical_publication_reaches_planner(workspace,runtime,mode):
    add_documents(workspace,[('guidance','future',{'title':'2027학년도 휴학 신청 안내','academic_year':'2027','published_at':'2026-10-16','text':'신청 기간: 2027. 2. 10. ~ 2027. 2. 14. 신청 방법: KNOWN_FUTURE 포털에서 신청한다.'})])
    with sqlite3.connect(workspace.database) as db:db.execute("UPDATE source_documents SET published_at='2026-10-16' WHERE document_key='guidance:future'")
    build(workspace);result=await endpoint('2027학년도 휴학 신청 기간과 방법을 알려줘',mode)
    assert result['verification_status']=='passed' and 'KNOWN_FUTURE' in runtime.contexts[-1]['context']


def test_source_field_and_curriculum_preserved_in_final_context(workspace):
    add_documents(workspace,[('rules','graduation',{'title':'2026학번 졸업 요건','entry_year':'2026','text':'필수 이수 교과목: CSE101'}),('courses','CSE101',{'title':'자료구조','course_code':'CSE101','credit':'3','curriculum_year':'2023','availability_status':'curriculum_only'})]);build(workspace)
    result=connected_search(workspace.database,'2026학번 졸업 요건 CSE101 학점',scope=Scope(as_of=AS_OF),limit=8)
    import pandas as pd
    frame=pd.DataFrame(result['hits']);frame['citation_number']=range(1,len(frame)+1);frame['evidence_group']=1;context=service._build_selected_evidence_context(frame)
    assert '학점\n3' in context and '교육과정 연도: 2023' in context and '실제 개설 및 졸업요건 충족 미확인' in context

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ask','stream'])
@pytest.mark.parametrize('state',['missing','hidden','private','wise','after_asof','stale','corrupt','no_graph'])
async def test_reference_cannot_revive_unavailable_new_source(workspace,runtime,mode,state):
    data={'title':'휴학 신청 안내','text':'신청 방법: FORBIDDEN_FACT 포털에서 신청한다.'}
    if state=='hidden':data['_status']='hidden'
    if state=='private':data.update(visibility='department',department='경영학과',major='경영학과')
    if state=='wise':data['campus_scope']='wise'
    if state!='missing':add_documents(workspace,[('guidance','anchor',data)])
    build(workspace)
    with sqlite3.connect(workspace.database) as db:
        if state=='after_asof':db.execute("UPDATE source_documents SET published_at='2026-10-18'")
        if state=='stale':db.execute("UPDATE source_documents SET title=title||' CHANGED'")
        if state=='corrupt':db.execute("UPDATE information_units SET source_text='CORRUPTED'")
        if state=='no_graph':db.execute('DROP TABLE connected_manifest')
    if state=='after_asof':build(workspace)
    chat.append_manual_history(mode,'휴학 신청 안내 알려줘','HISTORY_ONLY_FACT',[{'source':'guidance','title':'휴학 신청 안내','citation_number':1,'metadata':{'doc_id':'guidance:anchor'},'snippet':'FORBIDDEN_FACT'}])
    result=await endpoint('그 문서 다시 보여줘',mode,mode,major='통계학과')
    assert result['sources']==[] and not runtime.contexts and not runtime.grounding

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ask','stream'])
@pytest.mark.parametrize('state',['term_only','hidden','private','wise','after_asof','stale','corrupt','no_graph','flag_off'])
async def test_future_guard_requires_current_authorized_calendar_assertion(workspace,runtime,monkeypatch,mode,state):
    data={'title':'2027학년도 휴학 신청 안내','academic_year':'2027','text':'신청 기간: 2027. 2. 10. ~ 2027. 2. 14. KNOWN_FUTURE 신청 안내'}
    if state=='term_only':data['text']='신청 대상: 2026학년도 2학기 외국인 신입생은 2027학년도 1학기에 신청 가능. 정확한 날짜는 미정이다.'
    if state=='hidden':data['_status']='hidden'
    if state=='private':data.update(visibility='department',department='경영학과',major='경영학과')
    if state=='wise':data['campus_scope']='wise'
    add_documents(workspace,[('guidance','future',data)])
    with sqlite3.connect(workspace.database) as db:db.execute('UPDATE source_documents SET published_at=?',('2026-10-18' if state=='after_asof' else '2026-10-16',))
    build(workspace)
    with sqlite3.connect(workspace.database) as db:
        if state=='stale':db.execute("UPDATE source_documents SET title=title||' CHANGED'")
        if state=='corrupt':db.execute("UPDATE information_units SET source_text='CORRUPTED'")
        if state=='no_graph':db.execute('DROP TABLE connected_manifest')
    if state=='flag_off':monkeypatch.setattr(service.rag_config,'RAG_CONNECTED_RETRIEVAL_ENABLED',False)
    result=await endpoint('2027학년도 휴학 신청 기간과 방법을 알려줘',mode)
    assert not runtime.contexts and not runtime.grounding and result['sources']==[]
    assert '발표' in result['answer'] or '공고' in result['answer']

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ask','stream'])
async def test_actual_notice_explicit_attachment_child_current_body(workspace,runtime,mode):
    import_actual(workspace);add_guides(workspace);build(workspace)
    with sqlite3.connect(workspace.database) as db:
        parent=db.execute("SELECT document_key,title FROM source_documents WHERE dataset='notices'").fetchone()
        child=db.execute("SELECT document_key FROM source_documents WHERE dataset='attachments'").fetchone()[0]
    chat.append_manual_history(mode,'전공결정 공지의 시행 계획 알려줘','OLD ANSWER',[{'source':'notices','title':parent[1],'citation_number':1,'metadata':{'doc_id':parent[0]},'snippet':'NOT_THE_CHILD_BODY'}])
    result=await endpoint('그 첨부파일의 신청 예외를 알려줘',mode,mode)
    assert result['verification_status']=='passed' and len(result['sources'])==1
    source=result['sources'][0];assert source['source']=='attachments' and source['metadata']['doc_id']==child
    context=runtime.contexts[-1]['context'];assert '2027학년도 1학기' in context and '외국인' in context
    assert 'NOT_THE_CHILD_BODY' not in context and '인원배정' not in context

@pytest.mark.asyncio
async def test_attachment_ambiguity_clarifies_and_exact_parent_stays_exact(workspace,runtime):
    links=[{'kind':'attachment','url':'https://www.dongguk.edu/file/'+k,'label':k+' 시행 계획'} for k in ('A','B')]
    add_documents(workspace,[('notices','parent',{'title':'휴학 신청 공지','text':'신청 방법: PARENT_ONLY 신청한다.','links':links})]+[('attachments',k,{'title':'휴학 시행 계획 '+k,'text':'신청 예외: CHILD_'+k+' 조건이다.','parent_document_key':'notices:parent','_url':link['url']}) for k,link in zip(('A','B'),links)])
    build(workspace)
    for session,query in [('ambiguous','그 첨부파일의 신청 예외를 알려줘'),('exact','그 문서 다시 보여줘')]:
        chat.append_manual_history(session,'휴학 신청 공지 알려줘','OLD',[{'source':'notices','title':'휴학 신청 공지','citation_number':1,'metadata':{'doc_id':'notices:parent'}}])
        result=await endpoint(query,session=session)
        if session=='ambiguous':assert result['sources']==[] and not runtime.contexts
        else:
            assert result['verification_status']=='passed' and len(result['sources'])==1
            assert result['sources'][0]['source']=='notices'
            context=runtime.contexts[-1]['context']
            assert 'PARENT_ONLY' in context and 'CHILD_A' not in context and 'CHILD_B' not in context

@pytest.mark.parametrize('board,expected',[('qna',True),('freeboard',True),('notice',False),('interlang4_5',False)])
def test_query_board_authority_identity(board,expected):
    from src.services.source_authority import requires_authority_review
    assert requires_authority_review('https://gsedu.dongguk.edu/bbs/board.php?bo_table='+board+'&wr_id=123')==expected
    assert not requires_authority_review('https://www.dongguk.edu/page/146')

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ask','stream'])
@pytest.mark.parametrize('case',['terms','degrees','departments','paired_terms','calendar_date'])
async def test_explicit_target_sets_and_date_roles_survive_common_selector(workspace,runtime,mode,case):
    rows=[]
    if case in {'terms','paired_terms'}:
        for year in (2025,2026):
            for semester,month in [('1','04'),('2','10')]:
                rows.append(('guidance',f'{year}-{semester}',{'title':f'{year}학년도 {semester}학기 휴학 신청','academic_year':str(year),'semester':semester,'text':f'신청 기간: {year}. {month}. 13. ~ {year}. {month}. 17. TERM_{year}_{semester}'}))
        query='2026학년도 1학기와 2학기 휴학 신청 기간을 비교해줘' if case=='terms' else '2025-1과 2026-2 휴학 신청 기간을 비교해줘'
        expected=['TERM_2026_1','TERM_2026_2'] if case=='terms' else ['TERM_2025_1','TERM_2026_2']
        forbidden=['TERM_2025_1','TERM_2025_2'] if case=='terms' else ['TERM_2025_2','TERM_2026_1']
    elif case=='degrees':
        rows=[('guidance',d,{'title':label+' 휴학 신청','audience':d,'text':'신청 방법: '+marker+' 포털에서 신청한다.'}) for d,label,marker in [('undergraduate','학부생','UG_RULE'),('graduate','대학원생','GR_RULE')]]
        query='학부와 대학원 휴학 신청 방법을 비교해줘';expected=['UG_RULE','GR_RULE'];forbidden=[]
    elif case=='departments':
        rows=[('guidance',m,{'title':m+' 졸업 요건','major':m,'text':'졸업 요건: '+marker+' 필수 조건을 충족해야 한다.'}) for m,marker in [('통계학과','PUBLIC_A'),('경영학과','PUBLIC_B')]]
        rows.append(('rules','private',{'title':'경영학과 졸업 요건','major':'경영학과','department':'경영학과','visibility':'department','text':'졸업 요건: PRIVATE_B 비공개 조건이다.'}))
        query='통계학과와 경영학과 졸업 요건을 비교해줘';expected=['PUBLIC_A','PUBLIC_B'];forbidden=['PRIVATE_B']
    else:
        rows=[('schedule',key,{'title':'휴학 신청 일정','academic_year':2025,'start_date':day,'end_date':day,'내용':'휴학 신청 기간 '+marker}) for key,day,marker in [('date','2026-02-14','EXACT_FEB'),('other','2026-10-14','WRONG_OCT')]]
        query='2026-2-14 휴학 신청 일정을 알려줘';expected=['EXACT_FEB'];forbidden=['WRONG_OCT']
    add_documents(workspace,rows);build(workspace)
    result=await endpoint(query,mode,major='통계학과' if case=='departments' else None)
    assert result['verification_status']=='passed'
    context=runtime.contexts[-1]['context'];selector=str(runtime.selector[-1]);grounding=runtime.grounding[-1][2]
    assert all(marker in context and marker in selector and marker in grounding for marker in expected)
    assert all(marker not in context and marker not in selector for marker in forbidden)


def test_missing_requested_term_is_reported_instead_of_false_complete(workspace):
    add_documents(workspace,[('guidance','only-first',{'title':'2026학년도 1학기 전공결정','academic_year':'2026','semester':'1','text':'신청 기간: 2026. 04. 13. ~ 2026. 04. 17.'})]);build(workspace)
    result=bundle(workspace,'2026학년도 1학기와 2학기 전공결정 신청 기간을 비교해줘')
    assert result['requested_scope_coverage']['academic:2026-1']['period']
    assert result['missing_scope_facets']['academic:2026-2']==['period']

@pytest.mark.parametrize('state',['private_parent','hidden_parent','private_child','hidden_child','missing_child','unrelated_child','stale'])
def test_explicit_child_requires_current_parent_and_exact_link(workspace,state):
    parent={'title':'휴학 신청 공지','text':'신청 방법: 부모 공지에서 신청한다.','links':[{'kind':'attachment','label':'시행 계획','url':'https://www.dongguk.edu/file/H'}]}
    child={'title':'휴학 시행 계획','text':'신청 예외: CHILD_BODY 다만 외국인은 다음 학기에 신청한다.','parent_document_key':'notices:N','_url':'https://www.dongguk.edu/file/H'}
    if state=='private_parent':parent.update(visibility='department',department='경영학과')
    if state=='hidden_parent':parent['_status']='hidden'
    if state=='private_child':child.update(visibility='department',department='경영학과')
    if state=='hidden_child':child['_status']='hidden'
    if state=='unrelated_child':child['parent_document_key']='notices:OTHER'
    add_documents(workspace,[('notices','N',parent)]+([] if state=='missing_child' else [('attachments','H',child)]));build(workspace)
    if state=='stale':
        with sqlite3.connect(workspace.database) as db:db.execute("UPDATE source_documents SET title=title||' CHANGED' WHERE document_key='attachments:H'")
    result=referenced_information(workspace.database,document_key='notices:N',dataset='notices',query='그 첨부파일의 신청 예외',scope=Scope(as_of=AS_OF,department='통계학과'),attachment_child=True)
    assert not result['hits']

@pytest.mark.parametrize('query,status',[('통계학과와 경영학과 졸업요건 알려줘','resolved_public_targets'),('통계학과 경영학과 졸업요건 알려줘','ambiguous')])
def test_explicit_public_list_is_distinct_from_ambiguous_target(workspace,query,status):
    add_documents(workspace,[('guidance',m,{'title':m+' 졸업요건','major':m,'text':'졸업 요건: '+m+' 필수 조건이다.'}) for m in ('통계학과','경영학과')]);build(workspace)
    result=bundle(workspace,query)
    assert result['target_resolution']['status']==status
    assert bool(result['units'])==(status=='resolved_public_targets')


def collect_actual_discovery_parent(workspace):
    """Replay one of root's seven actual failures, preserving both raw hashes."""
    from src.collectors.official_public import Collector,Fetcher,Policy,Budgets,Response,canonical_url
    records=json.loads((F/'actual-discovery-parent-receipt.json').read_text())
    manifest={'schema_version':1,'approved_sources':[{'url':r['url'],'owner':'공식 공개 기관','verification_url':records[0]['url'],'seed':i==0,'campus_scope':'unknown'} for i,r in enumerate(records)]}
    policy=Policy(manifest);budget=Budgets(pages=2,requests=2)
    raw={canonical_url(r['url']):Response(200,{'content-type':'text/html' if i==0 else 'application/octet-stream'},(F/r['file']).read_bytes()) for i,r in enumerate(records)}
    c=Collector(workspace.checkpoint,policy,budget,fetcher=Fetcher(policy,budget,transport=lambda url,*a:raw[url],resolver=lambda _:['93.184.216.34'],sleep=lambda _:None))
    c.run();c.close()
    return records


def test_actual_discovery_only_parent_imports_file_without_answerable_navigation(workspace):
    from src.pipelines.official_public import import_collection
    records=collect_actual_discovery_parent(workspace);report=import_collection(workspace)
    assert not report['failed'] and len(report['new'])==1 and len(report['provenance_parents'])==1
    parent=report['provenance_parents'][0];child=report['new'][0]
    assert import_collection(workspace)['unchanged']==[child]
    build(workspace)
    with sqlite3.connect(workspace.database) as db:
        p=json.loads(db.execute('SELECT normalized_payload_json FROM source_documents WHERE document_key=?',(parent,)).fetchone()[0])
        cp=json.loads(db.execute('SELECT normalized_payload_json FROM source_documents WHERE document_key=?',(child,)).fetchone()[0])
        assert p['provenance_only'] and p['answerable'] is False and p['sections']==[] and p['content']==''
        assert db.execute('SELECT status FROM source_documents WHERE document_key=?',(parent,)).fetchone()[0]=='provenance'
        assert db.execute("SELECT count(*) FROM source_documents WHERE dataset='guidance' AND status IN ('active','updated')").fetchone()[0]==0
        assert cp['parent_document_key']==parent and cp['content'].strip()
        assert cp['raw_sha256']==records[1]['raw_sha256'] and p['raw_sha256']==records[0]['raw_sha256']
        assert db.execute('SELECT count(*) FROM connected_chunks WHERE document_key=?',(parent,)).fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM information_units WHERE document_key=?',(parent,)).fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM connected_chunks WHERE document_key=?',(child,)).fetchone()[0]>0
    # Current parent permission changes invalidate the child's evidence at
    # query time even after rebuilding; no copied public payload can revive it.
    with sqlite3.connect(workspace.database) as db:
        p['visibility']='department';p['department']='경영학과';db.execute('UPDATE source_documents SET normalized_payload_json=? WHERE document_key=?',(json.dumps(p),parent))
    build(workspace)
    exact=referenced_information(workspace.database,document_key=child,dataset='attachments',query='그 문서 다시 보여줘',scope=Scope(as_of=AS_OF))
    assert not exact['hits']

@pytest.mark.parametrize('state',['failed','http_failed','hash','extraction','unlinked','private','hidden','authority','policy','history'])
def test_discovery_parent_proof_failures_never_create_or_broaden_identity(workspace,state):
    from src.pipelines.official_public import import_collection
    records=collect_actual_discovery_parent(workspace);parent_url=records[0]['url']
    if state in {'private','hidden'}:
        add_documents(workspace,[('guidance','existing',{'title':'현재 정본 부모','_url':parent_url,'_status':'hidden' if state=='hidden' else 'active','visibility':'department' if state=='private' else 'public','department':'경영학과','text':'GOOD_PRIOR_BODY'})])
    with sqlite3.connect(workspace.checkpoint) as db:
        if state=='failed':db.execute("UPDATE frontier SET status='failed' WHERE url=?",(parent_url,))
        elif state=='http_failed':db.execute('UPDATE receipts SET http_status=403 WHERE url=?',(parent_url,))
        elif state=='hash':db.execute("UPDATE receipts SET raw_sha256='wrong' WHERE url=?",(parent_url,))
        elif state=='extraction':
            row=db.execute('SELECT extraction_json FROM receipts WHERE url=?',(parent_url,)).fetchone();p=json.loads(row[0]);p['discovery_links']=[];db.execute('UPDATE receipts SET extraction_json=? WHERE url=?',(json.dumps(p),parent_url))
        elif state=='unlinked':
            from pathlib import Path
            from dataclasses import asdict
            from src.collectors.official_extract import extract_html
            from src.collectors.official_public import digest
            row=db.execute('SELECT raw_path FROM receipts WHERE url=?',(parent_url,)).fetchone();original=Path(row[0]).read_bytes();raw=original.replace(b'2023_cover_letter.hwp',b'other_file.hwp');assert raw!=original
            path=workspace.directory/'raw'/digest(raw);path.write_bytes(raw)
            parsed=extract_html(raw,parent_url)
            db.execute('UPDATE receipts SET raw_path=?,raw_sha256=?,bytes=?,extraction_json=? WHERE url=?',(str(path),digest(raw),len(raw),json.dumps(asdict(parsed)),parent_url))
        elif state in {'policy','authority'}:
            row=db.execute("SELECT value FROM metadata WHERE key='manifest_json'").fetchone();p=json.loads(row[0])
            if state=='policy':p['approved_sources']=[e for e in p['approved_sources'] if e['url']!=parent_url]
            else:p['approved_sources'][0]['source_authority']='requires_authority_review'
            db.execute("UPDATE metadata SET value=? WHERE key='manifest_json'",(json.dumps(p),))
        elif state=='history':db.execute("UPDATE receipts SET manifest_hash='unrecorded-history' WHERE url=?",(parent_url,))
    report=import_collection(workspace)
    assert report['failed'] and not report['new'] and not report['provenance_parents']
    with sqlite3.connect(workspace.database) as db:
        assert db.execute("SELECT count(*) FROM source_documents WHERE dataset='attachments'").fetchone()[0]==0
        if state in {'private','hidden'}:
            row=db.execute("SELECT status,normalized_payload_json FROM source_documents WHERE document_key='guidance:existing'").fetchone()
            assert 'GOOD_PRIOR_BODY' in row[1] and row[0]==('hidden' if state=='hidden' else 'active')

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ask','stream'])
async def test_course_field_scope_reaches_real_selector_and_generation(workspace,runtime,mode):
    add_documents(workspace,[('rules','requirement',{'title':'2026학번 통계학과 졸업 요건','major':'통계학과','entry_year':'2026','text':'필수 이수 교과목: CSE101'}),('courses','CSE101',{'title':'자료구조','major':'통계학과','course_code':'CSE101','credit':'3','curriculum_year':'2023','availability_status':'curriculum_only'})]);build(workspace)
    result=await endpoint('2026학번 통계학과 졸업 요건 CSE101 학점을 알려줘',mode,major='통계학과')
    assert result['verification_status']=='passed'
    for evidence in (str(runtime.selector[-1]),runtime.contexts[-1]['context'],runtime.grounding[-1][2]):
        assert '학점' in evidence and '2023' in evidence and '실제 개설 및 졸업요건 충족 미확인' in evidence
        assert 'CSE101' in evidence and '필수 이수 교과목' in evidence

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['ask','stream'])
async def test_actual_conditional_reverse_reaches_common_transport(workspace,runtime,mode):
    import_actual(workspace);add_guides(workspace);build(workspace)
    result=await endpoint('2027학년도 1학기에 전공결정을 신청할 수 있다고 안내된 외국인 신입생은 어느 입학 학기야?',mode)
    assert result['verification_status']=='passed'
    assert '전공결정' in runtime.contexts[-1]['question'] and '전공식 결석정' not in runtime.contexts[-1]['question']
    for evidence in (str(runtime.selector[-1]),runtime.contexts[-1]['context'],runtime.grounding[-1][2]):
        assert '2026학년도 2학기' in evidence and '2027학년도 1학기' in evidence and '외국인' in evidence
        assert '09:00' not in evidence and '23:59' not in evidence
