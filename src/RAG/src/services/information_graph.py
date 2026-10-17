"""Information-centric projection: subjects -> source-bound typed assertions.

Only explicit headings, labelled fields/rows and condition phrases classify
facets. Exact paragraphs remain the objects: no inferred amounts, entitlement,
course equivalence, contradiction, or administrative authority. Topic membership
is navigation; it is deliberately separate from HAS_<FACET> assertions.
"""
from __future__ import annotations
from collections import Counter,defaultdict
from pathlib import Path
import json
import re
from functools import lru_cache
from src.pipelines.canonical import canonical_hash,canonical_json

CONCEPT_PATH=Path(__file__).resolve().parents[2]/'data/information_concepts.json'
FACETS=('overview','eligibility','requirement','exception','period','published_on','plan_attachment','reference_guidance','consequence','approval_condition','course_attribute','credits','documents','procedure','amount','reference_rule','course_reference','location','operating_state','contact')
SCHEMA='''
CREATE TABLE IF NOT EXISTS information_subjects(subject_key TEXT PRIMARY KEY,concept_key TEXT,label TEXT,kind TEXT,qualifiers_json TEXT,resolution TEXT);
CREATE INDEX IF NOT EXISTS ix_information_concept ON information_subjects(concept_key);
CREATE TABLE IF NOT EXISTS information_units(unit_key TEXT PRIMARY KEY,subject_key TEXT,document_key TEXT,source_url TEXT,locator TEXT,section_heading TEXT,span_start INTEGER,span_end INTEGER,source_text TEXT,qualifiers_json TEXT,document_revision TEXT,graph_revision TEXT,classification TEXT);
CREATE INDEX IF NOT EXISTS ix_information_unit_subject ON information_units(subject_key);
CREATE INDEX IF NOT EXISTS ix_information_unit_document ON information_units(document_key);
CREATE TABLE IF NOT EXISTS information_assertions(assertion_key TEXT PRIMARY KEY,subject_key TEXT,predicate TEXT,unit_key TEXT,derivation TEXT,review_status TEXT,qualifiers_json TEXT,graph_revision TEXT);
CREATE INDEX IF NOT EXISTS ix_information_assertion_subject ON information_assertions(subject_key);
CREATE INDEX IF NOT EXISTS ix_information_assertion_unit ON information_assertions(unit_key);
CREATE TABLE IF NOT EXISTS information_references(reference_key TEXT PRIMARY KEY,unit_key TEXT,target_subject_key TEXT,predicate TEXT,document_key TEXT,source_url TEXT,locator TEXT,source_text TEXT,review_status TEXT,graph_revision TEXT);
CREATE TABLE IF NOT EXISTS information_subject_links(link_key TEXT PRIMARY KEY,subject_key TEXT,target_subject_key TEXT,predicate TEXT,derivation TEXT,review_status TEXT,qualifiers_json TEXT,graph_revision TEXT);
'''

ROLE_PATTERNS={
 'eligibility':r'신청\s*대상|지원\s*자격|신청\s*자격|대\s*상\s*(?:자|:|：|\|)|자격\s*[:：|]|학년.*신청\s*가능|재학\s*중인\s*자',
 'requirement':r'학기말.*평점평균.*미만|성적\s*경고.*기준|이수\s*요건|졸업\s*(?:요건|기준|학점)|결정\s*기준|배정\s*원칙|필수\s*이수|이수\s*조건|제한\s*사항|모두.*기재.*필요|하여야|해야\s*함|해야\s*한다|반드시',
 'exception':r'예외|다만|단,|단\s*[,，]|면제|대체\s*인정|제외|특례|(?:^|\n)\s*※',
 'period':r'신청\s*(?:기간|일정)|접수\s*(?:기간|일정)|마감|기한|결정\s*시기|발표\s*일정|확정\s*공고|\d{4}[.\-/년].*(?:~|부터|까지)',
 'documents':r'제출\s*서류|구비\s*서류|신청\s*서류|첨부\s*서류|진단서|신청서|휴학원|양식',
 'procedure':r'신청\s*방법|신청\s*절차|신청\s*채널|처리\s*절차|신청\s*경로|nDRIMS|엔드림스|방문.*신청|제출.*신청',
 'amount':r'금액\s*[:：|]|반환\s*금액|등록금.*(?:분의|[0-9]+\s*원)|수수료|장학\s*금액',
 'reference_rule':r'근거\s*규정|관련\s*규정|학칙\s*제\s*\d+\s*조|규정\s*제\s*\d+\s*조',
 'reference_guidance':r'가이드\s*참조|세부\s*지침|별도.*확인|별도.*안내|학과\s*홈페이지',
 'consequence':r'미신청.*제한|강제\s*배정|임의\s*배정|증명서.*(?:중지|중단|불가능)|후순위|유예',
 'course_reference':r'학수\s*번호|필수\s*이수\s*교과목|대체\s*인정\s*교과목|[A-Z]{2,6}[0-9]{3,5}',
 'location':r'위치\s*[:：|]|장소\s*[:：|]|소재지\s*[:：|]',
 'operating_state':r'운영\s*시간|운영\s*상태|휴무|휴관|영업\s*시간',
 'contact':r'담당부서\s*[:：]|연락처\s*[:：]|전화번호\s*[:：]'
}
PREDICATES={role:'HAS_'+role.upper() for role in FACETS}
PREDICATES['credits']='HAS_CREDITS'
PREDICATES['published_on']='PUBLISHED_ON'
QUERY_ROLES={
 'eligibility':r'자격|대상|신청할\s*수|신청\s*가능', 'requirement':r'요건|조건|기준|학점|필수|반드시|지망|써야|기재',
 'exception':r'예외|대체|면제', 'period':r'기간|언제|마감|기한|일정|발표',
 'documents':r'서류|준비물|양식|진단서|작성.*제출|제출해야|업로드','procedure':r'방법|절차|어떻게|채널|경로|신청\s*메뉴',
 'plan_attachment':r'첨부|hwp|계획서','published_on':r'게시일|등록일|공지.*언제.*올라',
 'course_attribute':r'교과목.*구분|과목.*구분','approval_condition':r'승인|확정','credits':r'학점|credit','reference_guidance':r'지침|가이드|참조','consequence':r'미신청|안\s*하면|후순위|유예|불이익',
 'amount':r'얼마|금액|수수료|반환|환불','reference_rule':r'규정|학칙|근거',
 'course_reference':r'과목|교과목|학수','location':r'어디|위치|장소',
 'operating_state':r'운영|휴무|휴관','contact':r'연락|전화|문의|담당'
}


@lru_cache(maxsize=8)
def _catalog(raw):return json.loads(raw)['concepts']


def catalog():return _catalog(CONCEPT_PATH.read_bytes())


def _url_identity(url):
    from src.collectors.official_public import canonical_url
    try:return canonical_url(url)
    except (ValueError,TypeError):return url


def compact(text):return re.sub(r'\s+','',str(text))


def concepts_in(text):
    material=compact(text)
    matches=[c for c in catalog() if c['key']!='meal' and any(compact(alias) in material for alias in c['aliases'])]
    if any(c['key']=='retake' for c in matches) and not re.search(r'(?<!재)수강신청|수강정정|수강취소',material):
        matches=[c for c in matches if c['key']!='registration']
    if not matches and re.search(r'(?:지망|전공배정).*(?:메뉴|신청|써야|기재)',material):
        matches=[c for c in catalog() if c['key']=='major_selection']
    strong_food=bool(re.search(r'학식|식단|상록원|학생식당|점심|저녁|식당메뉴',material))
    dated_food=bool(re.search(r'(?:오늘|내일|모레)(?:의|은|는)?메뉴',material))
    if strong_food or (dated_food and not matches):matches.extend(c for c in catalog() if c['key']=='meal')
    return matches


def requested_roles(query):
    roles=[role for role,pattern in QUERY_ROLES.items() if re.search(pattern,query,re.I)]
    if any(c['key']=='meal' for c in concepts_in(query)) and 'overview' not in roles:roles.append('overview')
    if re.search(r'외국인|신입생|신청해|할\s*수|신청\s*(?:기준|조건)',query) and 'eligibility' not in roles:roles.append('eligibility')
    if re.search(r'이번\s*\d{1,2}월|이번.*신청|10월.*신청',query) and 'period' not in roles:roles.append('period')
    if set(roles)&{'eligibility','requirement','period','procedure'} and 'exception' not in roles:roles.append('exception')
    return roles or ['overview','eligibility','requirement','period','documents','procedure','exception','approval_condition']


def _qualifiers(document,metadata,title):
    p=json.loads(document.get('normalized_payload_json') or '{}')
    q={k:metadata.get(k) for k in ('campus_scope','visibility','department','department_name','major','college_name','audience','entry_year','academic_year','semester','effective_from','effective_to','date','meal_date','curriculum_year','evidence_known_at','evidence_known_at_role','publication_field_status') if metadata.get(k) not in (None,'')}
    if metadata.get('audience_evidence'):q['audience_evidence']=metadata['audience_evidence']
    if document['dataset']=='schedule':
        q.pop('department',None);q['responsible_office']=metadata.get('responsible_office','');q['calendar_container_year']=metadata.get('calendar_container_year','')
        q['event_start']=p.get('start_date') or p.get('start','');q['event_end']=p.get('end_date') or p.get('end','')
    # An explicitly declared title scope is different from incidental years in
    # a condition/example; e.g. a 2026 plan can mention 2027 exceptions.
    year=re.search(r'(20\d{2})\s*학년도',title)
    semester=re.search(r'([12])\s*학기',title)
    cohort=re.search(r'(20\d{2})\s*학번',title)
    if year:q['academic_year']=year.group(1)
    if semester:q['semester']=semester.group(1)
    if cohort:q['entry_year']=cohort.group(1)
    for field in ('program_name','program_scope_text','facility_name','restaurant','corner'):
        if p.get(field):q[field]=p[field]
    return q


def _heading_level(line):
    if re.match(r"^#{2,6}\s",line):return len(line)-len(line.lstrip('#'))
    if re.match(r"^(?:제\s*\d+\s*조\s*\(|[0-9]{1,2}\.\s)",line):return 10
    if re.match(r"^[가-힣]\.\s",line):return 20
    if re.match(r"^(?:\([0-9]+\)|[0-9]+\))\s*",line):return 30
    if re.match(r"^\([가-힣]\)\s*",line):return 40
    return None


def _blocks(section):
    """Exact spans plus a source heading stack; siblings pop their children.

    A guide's `9. 전공결정 -> 나. 신청 기간 -> (2) 2학기` keeps its
    enclosing process. `10. 졸업 및 수료` ends that inheritance. All heading
    spans are retained so inherited roles have inspectable source evidence.
    """
    text=section['text'];start=current=0;stack=[];current_path=[]
    for line in text.splitlines(keepends=True):
        stripped=line.strip();level=_heading_level(stripped)
        table=text[start:current].count('[표]')>text[start:current].count('[/표]')
        if level is not None and not table:
            if current>start and text[start:current].strip():
                yield start,current,' > '.join(h['text'] for h in current_path) or section.get('heading',''),text[start:current],current_path
            start=current
            while stack and stack[-1]['level']>=level:stack.pop()
            offset=current+line.find(stripped)
            stack.append({'level':level,'text':stripped,'start':offset,'end':offset+len(stripped)})
            current_path=[dict(h) for h in stack]
        current+=len(line)
    if text[start:].strip():yield start,len(text),' > '.join(h['text'] for h in current_path) or section.get('heading',''),text[start:],current_path


def _heading_concept(path,heading):
    # An enclosing topic wins over incidental process names in a child
    # condition (e.g. a major-selection sanction can mention leave/registration).
    for item in path:
        matches=concepts_in(item['text'])
        if len(matches)==1:return matches
    matches=concepts_in(heading)
    return matches if len(matches)==1 else []


def _roles(text,heading):
    leaf=heading.split(' > ')[-1]
    stripped=text.strip()
    # Standalone labels are context. A value in a numbered child, colon field,
    # paragraph or complete table is source information.
    structural=(len(stripped.splitlines())<=1 and not re.search(r'[:：|]|nDRIMS|하여야|해야|승인됨|신청가능|재학생|신입생|\d{2}:\d{2}',stripped)
                and bool(re.search(r'(?:대상자|대상|방법|일정|기간|사항|원칙|기준|전공결정|기간 및 방법)$',stripped)))
    if structural:return []
    result=[]
    for role,pattern in ROLE_PATTERNS.items():
        material=text
        if role in {'eligibility','requirement','documents','procedure'}:
            # The nearest facet label can describe a value in a child row.
            nearest=next((part for part in reversed(heading.split(' > ')) if re.search(pattern,part,re.I)),'')
            material=nearest+'\n'+text
        if re.search(pattern,material,re.I):result.append(role)
    if 'period' in result and not re.search(r'20\d{2}\s*[.\-/년]\s*\d|결정\s*시기|신청기간에\s*한하여|매\s*학기.*기간|확정\s*공고|추후\s*안내',text) and re.search(r'미신청|신청.*하지|강제|제한|후순위',text):result.remove('period')
    if re.search(r'자동\s*승인|승인됨',text):result.append('approval_condition')
    return list(dict.fromkeys(result))


def local_semester(hierarchy):
    for heading in reversed(hierarchy):
        found=re.match(r'^\([0-9]+\)\s*([12])\s*학기\s*[-–:]',heading['text'])
        if found:return found.group(1)
    return None


def applicability_context_text(contexts):
    parts=[]
    for context in contexts:
        if context.get('review_status')!='deterministic_explicit':continue
        label='원문의 적용 대상 대안' if context.get('branch_relation')=='source_alternative_condition' else '원문의 예외 조건' if context.get('branch_relation')=='conditional_application_exception' else '원문의 대상 조건'
        parts.append(label+': '+context['heading']+'\n'+context['source_text'])
    return '\n'.join(parts)


def source_unit_text(unit):
    context=applicability_context_text((unit.get('qualifiers') or {}).get('source_applicability_context',[]))
    return (context+'\n' if context else '')+str(unit.get('source_text') or '')


def pack_source_units(units,limit):
    """Whole condition units only; omission is explicit and never a slice."""
    parts=[];kept=[];omitted=[];used=0;seen_contexts=set()
    for unit in units:
        context=applicability_context_text((unit.get('qualifiers') or {}).get('source_applicability_context',[]))
        text=((context+'\n') if context and context not in seen_contexts else '')+str(unit.get('source_text') or '')
        extra=len(text)+(2 if parts else 0)
        if text.strip() and used+extra<=limit:
            parts.append(text);kept.append(unit);used+=extra
            if context:seen_contexts.add(context)
        else:omitted.append(unit.get('unit_key','unknown'))
    return '\n\n'.join(parts),kept,omitted


def scoped_applicability_contexts(contexts,scope,text):
    selected=[]
    printed_years=set(re.findall(r'(20\d{2})\s*[.\-/년]\s*\d{1,2}',text))
    for context in contexts:
        target=context.get('scope',{})
        if scope.get('audience') not in (None,'','unknown','common') and target.get('audience') not in (None,'','unknown','common',scope['audience']):continue
        semester=target.get('semester') or target.get('application_semester')
        year=target.get('application_academic_year')
        if scope.get('semester') and semester and str(scope['semester'])!=str(semester):continue
        if year and ((scope.get('academic_year') and str(scope['academic_year'])!=year) or (printed_years and year not in printed_years)):continue
        selected.append(context)
    return selected


def requires_table_layout_review(document, section, text):
    payload=json.loads(document.get('normalized_payload_json') or '{}')
    parser=str(payload.get('parser_version') or payload.get('parser') or '').lower()
    source_type=str(document.get('source_type') or '').lower()
    flattened=section['locator'].startswith('BodyText/') or parser in {'pdf','hwp','hwpx','docx','pptx','xlsx'} or bool(re.search(r'(?:^|[_:.-])(?:pdf|hwpx?|docx|pptx|xlsx)(?:$|[_:.-])',source_type))
    return flattened and bool(re.search(r'(?:^|\n)구\s*분',text)) and len(text)>500


def build_information_projection(documents,metadata,sections_for,document_revision,revision):
    subjects,units,assertions,references={},{},{},{}
    unclassified=[];role_counts=Counter();document_roles=defaultdict(set);concept_documents=defaultdict(set)
    course_subjects=defaultdict(list);unresolved_programmes=set()

    def subject(concept,q,document,variant=''):
        identity={k:q.get(k,'') for k in ('campus_scope','visibility','audience','academic_year','semester','entry_year','department','department_name','major','college_name','program_name','program_scope_text','facility_name','restaurant','corner','date','curriculum_year','event_start','event_end')}
        if q.get('visibility','public')!='public':identity['private_source']=document['document_key']
        identity['variant']=variant
        key='subject:'+canonical_hash([concept['key'],identity])[:32]
        subjects.setdefault(key,(key,concept['key'],concept['name'],concept['kind'],canonical_json({**q,'variant':variant}),'unresolved' if concept['kind']=='unclassified_subject' else 'explicit_or_reviewed_identity'))
        return key

    def unit(document,subject_key,section,start,end,heading,text,q,roles,method):
        if not text.strip():return
        key='unit:'+canonical_hash([document['document_key'],subject_key,section['locator'],start,end,text])[:32]
        locator=f"{section['locator']}#chars={start}:{end}"
        q=dict(q);roles=list(roles)
        from src.services.official_provenance import guide_snapshot
        snapshot=guide_snapshot(document)
        if snapshot:q['official_snapshot']=snapshot
        if 'period' in roles:q['period_precision']='explicit_calendar_fields' if method=='explicit_structured_event_window' or re.search(r'20\d{2}\s*[.\-/년]\s*\d{1,2}',text) else 'relative_or_unspecified_window'
        if 'period' in roles:
            context=next((h['text'] for h in reversed(q.get('source_heading_spans',[])) if re.search(r'신청.*(?:기간|일정)|확정.*공고|결정.*시기|발표',h['text'])),'')
            leaf=heading.split(' > ')[-1]+'\n'+context+'\n'+text
            q['temporal_subroles']=[name for name,pattern in (
                ('result_announcement',r'확정\s*공고|결과\s*발표|확정\s*안내|발표\s*예정'),
                ('application_window',r'신청\s*(?:일정|기간)|접수\s*(?:일정|기간)'),
                ('decision_timing',r'결정\s*시기'),('deferred_application_term',r'외국인.*(?:2027|2026).*신청|외국인.*\n.*신청')) if re.search(pattern,leaf,re.S)] or ['source_unspecified_period']
            q['printed_time_precision']='clock_time' if re.search(r'\d{1,2}:\d{2}',text) else 'date_or_relative_only'
            q['timezone']='not_printed'
            q['explicit_event_years']=sorted(set(re.findall(r'(20\d{2})\s*[.\-/년]\s*\d{1,2}',text)))
        if 'procedure' in roles:q['procedure_subrole']='result_lookup' if re.search(r'확정\s*공고|결과.*조회',text) else 'application_channel' if re.search(r'신청|nDRIMS|엔드림스',text) else 'source_procedure'
        if 'consequence' in roles:q['consequence_subroles']=[name for name,pattern in (('lower_priority',r'후순위'),('possible_forced_allocation',r'강제\s*배정|임의\s*배정'),('restriction',r'중지|중단|제한|불가능'),('deferral_policy',r'유예')) if re.search(pattern,text)]
        q['source_modalities']=[name for name,pattern in (('possible_not_guaranteed',r'할\s*수\s*있|가능'),('lower_priority_not_ineligible',r'후순위'),('conditional',r'경우|한하여|충족|조건'),('source_note_exception',r'※')) if re.search(pattern,text)]
        notes=[]
        for line in text.splitlines():
            if '외국인' in line and re.search(r'20\d{2}\s*학년도\s*[12]\s*학기.*(?:신청|전공결정)',line):
                matches=list(re.finditer(r'(20\d{2})\s*학년도\s*([12])\s*학기',line))
                if matches:
                    target=matches[-1];notes.append({'kind':'conditional_application_term','academic_year':target.group(1),'semester':target.group(2),'source_text':line,'calendar_dates':'not_inferred'})
        if notes:
            q['conditional_term_notes']=notes
            if any(len(re.findall(r'20\d{2}\s*학년도\s*[12]\s*학기',note['source_text']))>=2 for note in notes):
                if 'exception' not in roles:roles.append('exception')
                if 'period' not in roles:roles.append('period')
                q['temporal_subroles']=list(dict.fromkeys(q.get('temporal_subroles',[])+['deferred_application_term']))
                q.setdefault('period_precision','term_only_no_calendar_dates')
        if requires_table_layout_review(document,section,text):
            q['layout_status']='flattened_table_requires_column_review'
            unclassified.append({'document_key':document['document_key'],'unit_key':key,'reason':'table_column_association_unreviewed'})
        row=(key,subject_key,document['document_key'],document.get('source_url') or '',locator,heading,start,end,text,canonical_json(q),document_revision(document),revision,'classified' if roles else 'unclassified')
        if key in units and units[key]!=row:raise ValueError('information unit identity collision:'+key)
        units[key]=row
        for role in roles:
            assertion_key=canonical_hash([subject_key,role,key])
            assertions[assertion_key]=(assertion_key,subject_key,PREDICATES[role],key,method,'needs_table_layout_review' if q.get('layout_status') else 'deterministic_explicit' if document.get('source_url') else 'verified_snapshot_remote_url_unresolved' if snapshot else 'unresolved_attribution',canonical_json(q),revision)
            role_counts[role]+=1;document_roles[document['document_key']].add(role)
        if not roles:unclassified.append({'document_key':document['document_key'],'unit_key':key,'reason':'no_explicit_facet_label_or_condition'})
        concept_documents[subjects[subject_key][1]].add(document['document_key'])

    for d in documents:
        from src.services.source_authority import requires_authority_review
        if requires_authority_review(d.get('source_url'),json.loads(d.get('normalized_payload_json') or '{}')):
            unclassified.append({'document_key':d['document_key'],'reason':'requires_source_authority_review'})
            continue
        m=metadata[d['document_key']];p=json.loads(d.get('normalized_payload_json') or '{}')
        title=str(m.get('title') or '').rsplit(' - ',1)[-1]
        q=_qualifiers(d,m,title)
        base=concepts_in(title)
        block_records=[];context_by_concept=defaultdict(list)
        if d['dataset'] not in {'courses','staff','schedule','meals'}:
            for section in sections_for(d):
                for record in _blocks(section):
                    block_records.append((section,record))
                    start,end,head,body,path=record
                    chosen=_heading_concept(path,head) or (base if len(base)==1 else [])
                    if 'eligibility' in _roles(body,head) and not requires_table_layout_review(d,section,body):
                        for concept in chosen:
                            context_scope={}
                            semester=local_semester(path)
                            if semester:context_scope={'semester':semester,'origin':'explicit_local_heading'}
                            from src.services.source_audience import labelled_condition_degree
                            degree=labelled_condition_degree(body,head)
                            if degree['audience']!='unknown':context_scope['audience']=degree['audience']
                            relation='source_alternative_condition' if concept['key']=='major_selection' and any(re.search(r'신청\s*대상',h['text']) for h in path) else 'source_condition'
                            if re.match(r'^\([0-9]+\).*외국인',body.strip()):
                                terms=re.findall(r'(20\d{2})\s*학년도\s*([12])\s*학기',body)
                                if len(terms)>=2:
                                    context_scope.update(application_academic_year=terms[-1][0],application_semester=terms[-1][1]);relation='conditional_application_exception'
                            context_by_concept[concept['key']].append({'locator':f"{section['locator']}#chars={start}:{end}",'source_text':body,'heading':head,'role':'explicit_source_eligibility_context','review_status':'deterministic_explicit','document_key':d['document_key'],'source_url':d.get('source_url') or '', 'span_start':start,'span_end':end,'branch_id':canonical_hash([d['document_key'],section['locator'],start,end]),'branch_relation':relation,'scope':context_scope})
            if d['dataset']=='notices':
                contexts=[item['source_text'] for values in context_by_concept.values() for item in values]
                targets=set(re.findall(r'[가-힣·]+대학\s*\(광역화[^)]*\)', '\n'.join(contexts)))
                if len(targets)==1:q['program_scope_text']=next(iter(targets))
        if len(base)==1 and d.get('published_at') and d['dataset']!='schedule':
            sk=subject(base[0],q,d);text=str(d['published_at'])
            unit(d,sk,{'locator':'SourceDocument.published_at'},0,len(text),'게시일',text,q,['published_on'],'explicit_canonical_publication_field')
        if len(base)==1:
            for index,link in enumerate(p.get('links',[]) if isinstance(p.get('links'),list) else []):
                if isinstance(link,dict) and link.get('kind')=='attachment' and re.search(r'계획|시행',link.get('label','')):
                    sk=subject(base[0],q,d);text=canonical_json(link)
                    unit(d,sk,{'locator':f'$.links[{index}]'},0,len(text),'시행 계획 첨부',text,q,['plan_attachment'],'explicit_plan_link_not_submission_form')
        if d['dataset']=='courses':
            code=str(p.get('course_code') or p.get('학수번호') or '')
            name=str(p.get('course_name') or title)
            valid_code=bool(re.fullmatch(r'[A-Z]{2,6}[0-9]{3,5}',code.upper()))
            if valid_code:q['course_code']=code.upper()
            else:unclassified.append({'document_key':d['document_key'],'reason':'invalid_or_aggregate_course_code','value':code})
            concept={'key':('course:' if valid_code else 'unclassified_course:')+canonical_hash([code if valid_code else d['document_key'],q.get('department_name') or q.get('major')])[:20],'name':name,'kind':'course' if valid_code else 'unclassified_subject'}
            key=subject(concept,q,d)
            if valid_code:course_subjects[code.upper()].append((key,q))
            fields=[('course_code','학수번호','overview'),('course_name','교과목명','overview'),('credit','학점','credits'),('credit_value','학점','credits'),('course_type','교과목 구분','course_attribute'),('availability_status','개설/교육과정 상태','operating_state'),('curriculum_year','교육과정 연도','period'),('description','교과목 설명','overview')]
            emitted=False
            for field,label,role in fields:
                if p.get(field) not in (None,''):
                    text=str(p[field]);unit(d,key,{'locator':'$.'+field},0,len(text),label,text,{**q,'offering_evidence':'curriculum_only_unless_explicitly_offered'},[role] if valid_code else [],'explicit_canonical_field');emitted=True
            if emitted:continue
        if d['dataset']=='meals':
            concept={'key':'meal:'+str(p.get('restaurant','unknown')),'name':str(p.get('restaurant','식당'))+' 식단','kind':'meal_offering'}
            key=subject(concept,q,d)
            menu_field=next((field for field in ('menu_text','menu','content') if p.get(field)),None)
            menu=p.get(menu_field,'') if menu_field else ''
            closed=p.get('is_closed') in (True,1,'1','true','True')
            role='operating_state' if closed else 'overview'
            if menu or closed:
                text=str(menu) if menu else str(p['is_closed'])
                unit(d,key,{'locator':'$.'+menu_field if menu else '$.is_closed'},0,len(text),'식단/운영 상태',text,q,[role],'explicit_canonical_field')
            else:unclassified.append({'document_key':d['document_key'],'reason':'unpublished_meal_no_assertion'})
            continue
        if d['dataset']=='schedule':
            events=base or [{'key':'event:'+canonical_hash(title)[:20],'name':title,'kind':'academic_event'}]
            for c in events:
                key=subject(c,q,d)
                text=canonical_json({field:p[field] for field in ('title','내용','start_date','end_date','start','end') if p.get(field)})
                unit(d,key,{'locator':'$.title|$.내용|$.start_date|$.end_date|$.start|$.end'},0,len(text),'학사일정',text,q,['period'],'explicit_structured_event_window')
            continue
        if d['dataset']=='staff':
            c={'key':'contact:'+canonical_hash(d['document_key'])[:20],'name':title,'kind':'public_contact_leaf'}
            key=subject(c,q,d)
            fields={k:p[k] for k in ('부서경로','department_path','성명','담당업무','전화번호','phone','email') if p.get(k)}
            if fields:
                text=canonical_json(fields)
                unit(d,key,{'locator':'$.[public_contact_fields]'},0,len(text),'공개 연락처',text,q,['contact'],'explicit_canonical_contact_fields')
            continue
        for section,record in block_records:
            start,end,heading,text,hierarchy=record
            explicit=_heading_concept(hierarchy,heading)
            # A compound document never broadcasts a section's assertions
            # to every concept mentioned by its page title.
            selected=explicit if len(explicit)==1 else base if len(base)==1 else []
            if section['locator']=='div.page_contact':selected=base
            if not selected and len(explicit)>1:
                unclassified.append({'document_key':d['document_key'],'locator':section['locator'],'reason':'ambiguous_compound_section_subject'})
            if not selected:
                # Named programs/facilities are independent identities;
                # generic "장학금" does not merge all scholarship programs.
                name=str(p.get('program_name') or p.get('facility_name') or title or d['document_key'])
                stable=re.sub(r'20\d{2}\s*학년도|[12]\s*학기|\s*(?:신청|모집|시행)?\s*안내\s*$','',name).strip()
                generic_programme=bool(re.fullmatch(r'(?:교내|교외)?장학(?:금)?(?:제도|지급|신청|안내|계획|정보|공고|관련|내용|공지|문의)*',compact(stable)))
                kind='scholarship_program' if re.search(r'장학금|장학$',stable) and not generic_programme else 'facility' if p.get('facility_name') or re.search(r'도서관|기숙사|학생식당',name) else 'unclassified_subject'
                prefix='unclassified_programme' if generic_programme else kind
                if generic_programme and d['document_key'] not in unresolved_programmes:
                    unresolved_programmes.add(d['document_key']);unclassified.append({'document_key':d['document_key'],'reason':'programme_identifier_required_generic_title'})
                selected=[{'key':prefix+':'+canonical_hash(stable if kind!='unclassified_subject' else d['document_key'])[:20],'name':stable,'kind':kind}]
            roles=_roles(text,heading)
            unit_scope=dict(q)
            from src.services.source_audience import labelled_degree
            if not hierarchy:
                degree=labelled_degree(heading)
                if degree!='unknown':
                    unit_scope['audience']=degree;unit_scope['audience_evidence']={'status':'explicit_section_label','evidence':[{'source_text':heading,'locator':section['locator']+'/heading','source_field':'section_heading'}]}
            for source_heading in reversed(hierarchy):
                degree=labelled_degree(source_heading['text'])
                if degree!='unknown':
                    unit_scope['audience']=degree;unit_scope['audience_evidence']={'status':'explicit_section_label','evidence':[{'source_text':source_heading['text'],'locator':section['locator'],'span_start':source_heading['start'],'span_end':source_heading['end']}]}
                    break
            from src.services.source_audience import labelled_condition_degree
            condition_degree=labelled_condition_degree(text,heading)
            if condition_degree['audience']!='unknown':
                unit_scope['audience']=condition_degree['audience'];unit_scope['audience_evidence']=condition_degree
            local=local_semester(hierarchy)
            if local:unit_scope['semester']=local;unit_scope['semester_scope_origin']='local_heading'
            for c in selected:
                variant=heading if c['key']=='leave' and any(v in heading for v in ('질병','군휴학','병사','임신','육아','창업')) else ''
                sk=subject(c,q,d,variant)
                unit_scope.pop('source_applicability_context',None)
                contexts=scoped_applicability_contexts(context_by_concept.get(c['key'],[])[:8],unit_scope,text) if 'eligibility' not in roles and set(roles)&{'procedure','period','requirement','consequence','approval_condition','documents','amount'} else []
                if contexts:
                    unit_scope['source_applicability_context']=contexts
                    context_degrees={context.get('scope',{}).get('audience','unknown') for context in contexts}
                    if unit_scope.get('audience') in (None,'','unknown') and len(context_degrees)==1 and 'unknown' not in context_degrees:unit_scope['audience']=next(iter(context_degrees))
                unit(d,sk,section,start,end,heading,text,{**unit_scope,'variant':variant,'source_heading_spans':hierarchy},roles,'explicit_heading_row_or_condition')
    # Precise identifiers connect requirements to courses. A reference is not
    # a claim that any department course satisfies any graduation requirement.
    for uk,row in units.items():
        text=row[8];q=json.loads(row[9])
        for code in set(re.findall(r'(?<![A-Za-z0-9])[A-Z]{2,6}[0-9]{3,5}(?![A-Za-z0-9])',text)):
            candidates={key for key,cq in course_subjects.get(code,[]) if cq.get('campus_scope')==q.get('campus_scope') and cq.get('visibility','public')==q.get('visibility','public') and (q.get('visibility','public')=='public' or (cq.get('department') or cq.get('major') or '')==(q.get('department') or q.get('major') or '')) and (not q.get('major') or cq.get('major') in (None,'',q['major']))}
            if len(candidates)==1:
                target=next(iter(candidates));key=canonical_hash([uk,target])
                clause=next((line for line in text.splitlines() if re.search(r'^\s*(?:필수\s*이수\s*교과목)\s*[:：|]',line) and re.search(r'\b'+re.escape(code)+r'\b',line)),None)
                predicate='EXPLICIT_COURSE_REQUIREMENT' if clause else 'REFERENCES_COURSE'
                if not clause and '필수' in text:unclassified.append({'unit_key':uk,'code':code,'reason':'course_requirement_row_association_unvalidated'})
                references[key]=(key,uk,target,predicate,row[2],row[3],row[4],text,'deterministic_explicit_code',revision)
            elif candidates:unclassified.append({'unit_key':uk,'reason':'ambiguous_course_reference','code':code})
    source_subjects=defaultdict(set);source_units=defaultdict(list);url_documents=defaultdict(list)
    for uk,row in units.items():source_subjects[row[2]].add(row[1]);source_units[row[2]].append((uk,row))
    # A validated table row explicitly classifies a named component. This is
    # navigation only; its conditions/methods stay on the component's units.
    for uk,row in units.items():
        if subjects[row[1]][1]!='multiple_majors':continue
        for target in source_subjects[row[2]]:
            target_row=subjects[target]
            if target==row[1] or not re.search(r'^다전공\s*\|\s*'+re.escape(target_row[2])+r'\s*\|',row[8],re.M):continue
            rk=canonical_hash([uk,target,'EXPLICIT_TOPIC_COMPONENT'])
            references[rk]=(rk,uk,target,'EXPLICIT_TOPIC_COMPONENT',row[2],row[3],row[4],row[8],'explicit_table_classification_navigation_only',revision)
    for d in documents:
        if d.get('source_url'):url_documents[_url_identity(d['source_url'])].append(d['document_key'])
    for d in documents:
        p=json.loads(d.get('normalized_payload_json') or '{}');links=p.get('links',[]) if isinstance(p.get('links'),list) else []
        if p.get('parent_document_key'):links=links+[{'document_key':p['parent_document_key'],'parent':True,'locator':'$.parent_document_key'}]
        if p.get('supersedes_document_key'):links=links+[{'document_key':p['supersedes_document_key'],'supersedes':True,'locator':'$.supersedes_document_key'}]
        for link in links:
            if not isinstance(link,dict):continue
            targets=[link['document_key']] if link.get('document_key') else url_documents.get(_url_identity(link.get('url','')),[])
            source_meta=metadata[d['document_key']]
            targets=[target for target in targets if target in metadata and metadata[target].get('visibility','public')==source_meta.get('visibility','public') and (source_meta.get('visibility','public')=='public' or (metadata[target].get('department') or metadata[target].get('major') or '')==(source_meta.get('department') or source_meta.get('major') or ''))]
            if len(targets)!=1:continue
            for uk,row in source_units[d['document_key']]:
                label=link.get('label','')
                if not link.get('parent') and not link.get('supersedes') and (not label or label not in row[8]):continue
                for target in source_subjects.get(targets[0],[]):
                    predicate='ATTACHMENT_DETAIL_FOR' if link.get('parent') else 'EXPLICITLY_SUPERSEDES' if link.get('supersedes') else 'PLAN_ATTACHMENT_EVIDENCE' if link.get('kind')=='attachment' and re.search('계획|시행',label) else 'EXPLICIT_RULE_REFERENCE' if re.search('규정|학칙',label) else 'EXPLICIT_INFORMATION_REFERENCE'
                    evidence_text=link.get('document_key') if link.get('parent') or link.get('supersedes') else row[8]
                    rk=canonical_hash([uk,target,predicate]);references[rk]=(rk,uk,target,predicate,row[2],row[3],link.get('locator') or row[4],evidence_text,'explicit_reference_navigation',revision)
    links={};families=defaultdict(list)
    for key,row in subjects.items():families[row[1]].append((key,json.loads(row[4])))
    for concept,members in families.items():
        if len(members)>100:continue
        for key,q in members:
            for target,tq in members:
                if key==target or q.get('visibility','public')!='public' or tq.get('visibility','public')!='public' or q.get('campus_scope')!=tq.get('campus_scope'):continue
                # Scope navigation is not applicability inheritance. Evidence
                # remains attached to the assertion on each scoped subject.
                lk=canonical_hash([key,target]);links[lk]=(lk,key,target,'SAME_TOPIC_SCOPE_VARIANT','reviewed_topic_navigation_only','navigation_only',canonical_json({'scope_must_be_rechecked':True}),revision)
    report={'subjects':len(subjects),'units':len(units),'assertions':len(assertions),'references':len(references),'subject_scope_links':len(links),'facets':dict(role_counts),
            'assertions_with_source_url':sum(bool(units[a[3]][3]) for a in assertions.values()),
            'unattributed_assertions':sum(not bool(units[a[3]][3]) for a in assertions.values()),
            'documents_with_substantive_facets':sum(bool(roles-{'contact'}) for roles in document_roles.values()),
            'core_facets':{role:count for role,count in role_counts.items() if role!='contact'},
            'documents_with_facets':len(document_roles),'concept_source_coverage':{key:len(value) for key,value in concept_documents.items()},
            'unclassified_subjects':sum(row[3]=='unclassified_subject' for row in subjects.values()),'unresolved_programme_documents':sorted(unresolved_programmes),
            'unclassified':unclassified,'unclassified_units':sum(row[12]=='unclassified' for row in units.values())}
    return {'subjects':subjects,'units':units,'assertions':assertions,'references':references,'links':links,'report':report}


def persist_information_projection(db,projection):
    for table in ('information_subject_links','information_references','information_assertions','information_units','information_subjects'):db.execute('DELETE FROM '+table)
    for key,table in (('subjects','information_subjects'),('units','information_units'),('assertions','information_assertions'),('references','information_references'),('links','information_subject_links')):
        values=list(projection[key].values())
        if values:db.executemany('INSERT INTO '+table+' VALUES('+','.join('?' for _ in values[0])+')',values)


def information_bundle(reader,query,*,limit=24):
    """Return unique evidence units with all roles, scope cells and unknowns."""
    if not 1<=limit<=80:raise ValueError('information unit limit must be 1..80')
    reader.resolve_query_scope(query)
    concepts=concepts_in(query);concept_keys={c['key'] for c in concepts};roles=requested_roles(query)
    years={str(y) for y in reader.scope.academic_years} or set(re.findall(r'(20\d{2})\s*(?:학년도|년도|년)',query))
    if reader.scope.academic_year is not None:years={str(reader.scope.academic_year)}
    semester=reader.scope.semester
    if semester is None:
        for match in re.finditer(r'([12])\s*학기',query):
            if not re.match(r'.{0,5}(?:입학|신입)',query[match.end():match.end()+10]):semester=match.group(1)
    foreign=bool(re.search(r'외국인.*신입|신입.*외국인',query))
    admission_year=next(iter(years)) if foreign and len(years)==1 else None
    subject_rows=reader.db.execute('SELECT s.*,group_concat(DISTINCT d.dataset) AS source_datasets FROM information_subjects s JOIN information_units u USING(subject_key) JOIN source_documents d USING(document_key) WHERE u.document_key IN (SELECT value FROM json_each(?)) GROUP BY s.subject_key ORDER BY s.subject_key',(reader.allowed_json,)).fetchall()
    # Query target scope is matched only against already authorized source
    # identities. It never replaces the trusted requester department used by
    # ConnectedReader's visibility gate.
    explicit_scopes={}
    for field in ('college_name','program_name','department_name','major','restaurant','corner'):
        named={json.loads(row['qualifiers_json']).get(field,'') for row in subject_rows}
        targets={name for name in named if isinstance(name,str) and len(compact(name))>=3 and compact(name) in compact(query)}
        if targets:explicit_scopes[field]=targets
    cohort_match=re.search(r'(20\d{2})\s*학번',query)
    requested_cohort=reader.scope.entry_year if reader.scope.entry_year is not None else cohort_match.group(1) if cohort_match else None
    subjects=[]
    for row in subject_rows:
        q=json.loads(row['qualifiers_json']);code=q.get('course_code','')
        if row['concept_key'].startswith(('unclassified_course:','unclassified_programme:')):continue
        matches=row['concept_key'] in concept_keys or (row['concept_key'].startswith('meal:') and 'meal' in concept_keys) or ((not concept_keys or row['kind'] in {'scholarship_program','facility'}) and compact(row['label']) and compact(row['label']) in compact(query)) or (bool(re.fullmatch(r'[A-Z]{2,6}\d{3,5}',code)) and bool(re.search(r'(?<![A-Z0-9])'+re.escape(code)+r'(?![A-Z0-9])',query.upper())))
        if not matches:continue
        if any(q.get(field) not in (None,'') and q.get(field) not in targets for field,targets in explicit_scopes.items()):continue
        if reader.scope.college and q.get('college_name') not in (None,'',reader.scope.college):continue
        if reader.scope.program and q.get('program_name') not in (None,'',reader.scope.program):continue
        if years and q.get('academic_year') not in (None,'') and str(q['academic_year']) not in years:continue
        if semester and q.get('semester') not in (None,'',str(semester),semester):continue
        if requested_cohort is not None and q.get('entry_year') not in (None,'',requested_cohort,str(requested_cohort)):continue
        if q.get('program_scope_text') and re.search(r'AI소프트웨어|물리.?반도체|열린전공|바이오시스템',query) and not any(token in q['program_scope_text'] for token in re.findall(r'AI소프트웨어\w*|물리.?반도체\w*|열린전공\w*|바이오시스템\w*',query)):continue
        subjects.append(dict(row))
    subject_truncated=len(subjects)>40
    anchor_year=reader.scope.as_of.year if reader.scope.as_of else 0
    def subject_rank(row):
        q=json.loads(row['qualifiers_json'])
        dataset_priority=min((rank for rank,name in enumerate(('guidance','attachments','notices','schedule','rules','courses','staff','meals')) if name in row['source_datasets'].split(',')),default=8)
        year=int(q.get('academic_year') or q.get('entry_year') or anchor_year)
        return (dataset_priority,abs(year-anchor_year),bool(q.get('variant')),row['subject_key'])
    subjects=sorted(subjects,key=subject_rank)[:40];keys=[s['subject_key'] for s in subjects]
    if set(roles)&{'course_reference','requirement','credits'} and keys:
        targets=reader.db.execute("SELECT DISTINCT s.* FROM information_references r JOIN information_units u USING(unit_key) JOIN information_subjects s ON s.subject_key=r.target_subject_key WHERE u.subject_key IN (SELECT value FROM json_each(?)) AND u.document_key IN (SELECT value FROM json_each(?)) AND r.predicate IN ('REFERENCES_COURSE','EXPLICIT_COURSE_REQUIREMENT') AND s.subject_key IN (SELECT subject_key FROM information_units WHERE document_key IN (SELECT value FROM json_each(?))) LIMIT 10",(canonical_json(keys),reader.allowed_json,reader.allowed_json)).fetchall()
        for target in targets:
            if target['subject_key'] not in keys:subjects.append(dict(target));keys.append(target['subject_key'])
    if keys:
        components=reader.db.execute("SELECT DISTINCT s.* FROM information_references r JOIN information_units u USING(unit_key) JOIN information_subjects s ON s.subject_key=r.target_subject_key WHERE u.subject_key IN (SELECT value FROM json_each(?)) AND u.document_key IN (SELECT value FROM json_each(?)) AND r.predicate='EXPLICIT_TOPIC_COMPONENT' AND s.subject_key IN (SELECT subject_key FROM information_units WHERE document_key IN (SELECT value FROM json_each(?))) LIMIT 10",(canonical_json(keys),reader.allowed_json,reader.allowed_json)).fetchall()
        for target in components:
            if target['subject_key'] not in keys:subjects.append(dict(target));keys.append(target['subject_key'])
    rows=reader.db.execute('SELECT u.*,a.predicate,a.derivation,a.review_status FROM information_units u JOIN information_assertions a USING(unit_key) WHERE u.subject_key IN (SELECT value FROM json_each(?)) AND u.document_key IN (SELECT value FROM json_each(?)) ORDER BY u.document_key,u.span_start LIMIT 2001',(canonical_json(keys),reader.allowed_json)).fetchall() if keys else []
    inverse={v:k for k,v in PREDICATES.items()};unique={};reviews=[]
    for row in rows:
        q=json.loads(row['qualifiers_json']);role=inverse[row['predicate']]
        if q.get('audience') not in (None,'','unknown','common',reader.scope.audience,'undergraduate' if reader.scope.audience in (None,'','common','unknown') else reader.scope.audience):continue
        if row['graph_revision']!=reader.revision:raise ValueError('stale information assertion')
        if requested_cohort is not None and q.get('entry_year') not in (None,'',str(requested_cohort),requested_cohort):continue
        if years and q.get('academic_year') not in (None,'') and str(q['academic_year']) not in years:continue
        if semester and q.get('semester') not in (None,'',str(semester),semester):continue
        event_years=set(q.get('explicit_event_years',[]))
        calendar_window=bool(set(q.get('temporal_subroles',[]))&{'application_window','result_announcement'}) or row['derivation']=='explicit_structured_event_window'
        if calendar_window and event_years and years and not event_years.intersection(years):continue
        if calendar_window and not years and reader.scope.as_of and event_years and str(reader.scope.as_of.year-(reader.scope.as_of.month<3)) not in event_years:continue
        notes=q.get('conditional_term_notes',[])
        if years and notes:
            admissions=set()
            for note in notes:
                terms=re.findall(r'(20\d{2})\s*학년도\s*[12]\s*학기',note['source_text'])
                if len(terms)>=2:admissions.add(terms[0])
            if admissions and not admissions.intersection(years):continue
        if foreign and admission_year and notes:
            applicable=[n for n in notes if re.search(admission_year+r'\s*학년도\s*2\s*학기',n['source_text'])]
            if not applicable:continue
        if foreign and 'application_window' in q.get('temporal_subroles',[]) and not notes:continue
        variant=q.get('variant','');wanted_variant=next((v for v in ('질병휴학','군휴학','병사휴학','육아휴학','창업휴학') if v in compact(query)),None)
        if wanted_variant and variant and wanted_variant not in compact(variant):continue
        narrow_tokens=re.findall(r'AI소프트웨어융합학부|물리.?반도체과학부|경영대학',query)
        context_text=row['source_text']+'\n'+'\n'.join(c['source_text'] for c in q.get('source_applicability_context',[]))+'\n'+' '.join(str(q.get(field,'')) for field in ('college_name','program_name','program_scope_text'))
        if narrow_tokens and not any(token in context_text for token in narrow_tokens):continue
        if foreign and '외국인' not in row['source_text'] and not notes:continue
        item=unique.setdefault(row['unit_key'],{**dict(row),'qualifiers':q,'facets':[],'path':[]})
        if role not in item['facets']:item['facets'].append(role)
        item['path'].append({'subject_key':row['subject_key'],'predicate':row['predicate'],'unit_key':row['unit_key'],'document_key':row['document_key'],'source_url':row['source_url'],'locator':row['locator'],'source_text':row['source_text'],'derivation':row['derivation'],'review_status':row['review_status'],'revision':reader.revision})
    candidates=[]
    for item in unique.values():
        if item['review_status']=='needs_table_layout_review':reviews.append(item);continue
        if set(item['facets'])&set(roles):candidates.append(item)
    available={role for item in candidates for role in item['facets']}
    # Choose coverage before rank. Unique units can satisfy several requested
    # facets at once; periods also reserve cells for each year and date subrole.
    subject_concepts={subject['subject_key']:subject['concept_key'] for subject in subjects}
    def cells(item):
        found={('facet',r) for r in item['facets'] if r in roles}
        found.update(('topic_facet',subject_concepts[item['subject_key']],r) for r in item['facets'] if r in roles)
        q=item['qualifiers']
        if 'period' in item['facets'] and 'period' in roles:
            for year in q.get('explicit_event_years',[]):found.add(('year_period',year,str(q.get('semester',''))))
            if q.get('printed_time_precision')=='clock_time':found.add(('clock_source',item['document_key']))
            for sub in q.get('temporal_subroles',[]):
                if sub in {'result_announcement','application_window','deferred_application_term','decision_timing'}:found.add(('date_role',sub))
                if sub=='result_announcement':found.add(('result_source',item['document_key']))
        if 'exception' in roles:
            for note in q.get('conditional_term_notes',[]):found.add(('conditional_term',item['document_key'],note['academic_year'],note['semester']))
        if 'procedure' in roles and 'procedure' in item['facets'] and re.search(r'방법|메뉴|어떻게|절차|경로',query):found.add(('procedure_source',item['document_key'],q.get('procedure_subrole','source_procedure')))
        if 'consequence' in roles:
            for sub in q.get('consequence_subroles',[]):found.add(('consequence_role',sub,item['document_key'] if reader.documents[item['document_key']]['dataset'] in {'notices','attachments'} else 'guide'))
        non_exception=set(item['facets'])&set(roles)-{'exception'}
        if non_exception:found.add(('source_kind',reader.documents[item['document_key']]['dataset']))
        if 'eligibility' in roles and 'eligibility' in item['facets'] and reader.documents[item['document_key']]['dataset'] in {'attachments','notices'}:
            target_tokens=re.findall(r'AI소프트웨어융합학부|물리.?반도체과학부|외국인',query)
            if not target_tokens or any(token in item['source_text'] for token in target_tokens):
                branch=next((h['text'] for h in reversed(q.get('source_heading_spans',[])) if h['level']==20),'')
                if branch:found.add(('eligibility_branch',item['document_key'],branch))
        return found
    selected=[];covered=set();remaining=list(candidates)
    special=re.findall(r'외국인|AI소프트웨어융합학부|물리.?반도체과학부|경영대학|5지망',query)
    def score(item):
        new=cells(item)-covered
        role_gain=sum(c[0] in {'facet','topic_facet'} for c in new)*100
        temporal_gain=sum(c[0] in {'year_period','date_role','result_source','consequence_role','eligibility_branch','clock_source','procedure_source','conditional_term'} for c in new)*50
        source_gain=8 if any(c[0]=='source_kind' for c in new) else 0
        condition_gain=sum(token in item['source_text']+' '+str(item['qualifiers'].get('program_scope_text','')) for token in special)*20
        # Source recency is ranking only; it never creates supersession facts.
        cohort=int(item['qualifiers'].get('entry_year') or 0) if reader.scope.entry_year is not None else 0
        instance=2 if str(item['qualifiers'].get('academic_year','')) in years else 0
        temporal_rank=0
        if item['qualifiers'].get('event_start') and reader.scope.as_of:
            start=item['qualifiers']['event_start'];end=item['qualifiers'].get('event_end') or start;anchor=reader.scope.as_of.isoformat()
            temporal_rank=2 if start<=anchor<=end else 1 if start>anchor else -1
        return (role_gain+temporal_gain+source_gain+condition_gain,temporal_rank,instance,cohort,len(item['source_text']),item['unit_key'])
    while remaining and len(selected)<limit:
        productive=[item for item in remaining if cells(item)-covered]
        if not productive:break
        item=max(productive,key=score);remaining.remove(item)
        item['facet']=next((role for role in roles if role in item['facets']),item['facets'][0])
        selected.append(item);covered.update(cells(item))
    selected_roles={r for item in selected for r in item['facets']}
    exposed={s['subject_key'] for s in subjects}
    references=[dict(r) for r in reader.db.execute('SELECT * FROM information_references WHERE unit_key IN (SELECT value FROM json_each(?)) AND target_subject_key IN (SELECT value FROM json_each(?)) LIMIT 81',(canonical_json([u['unit_key'] for u in selected]),canonical_json(sorted(exposed))))] if selected else []
    component_paths=[dict(r) for r in reader.db.execute("SELECT r.* FROM information_references r JOIN information_units u USING(unit_key) WHERE r.predicate='EXPLICIT_TOPIC_COMPONENT' AND u.subject_key IN (SELECT value FROM json_each(?)) AND r.target_subject_key IN (SELECT value FROM json_each(?)) AND u.document_key IN (SELECT value FROM json_each(?)) LIMIT 80",(canonical_json(keys),canonical_json(keys),reader.allowed_json))] if keys else []
    scope_paths=[dict(r) for r in reader.db.execute('SELECT * FROM information_subject_links WHERE subject_key IN (SELECT value FROM json_each(?)) AND target_subject_key IN (SELECT value FROM json_each(?)) LIMIT 81',(canonical_json(sorted(exposed)),canonical_json(sorted(exposed))))] if exposed else []
    result_statements=[{'document_key':u['document_key'],'source_text':u['source_text'],'locator':u['locator'],'source_scope':u['qualifiers'].get('program_scope_text','')} for u in selected if 'result_announcement' in u['qualifiers'].get('temporal_subroles',[])]
    year_coverage={year:{'period':[u['unit_key'] for u in selected if 'period' in u['facets'] and year in u['qualifiers'].get('explicit_event_years',[])]} for year in sorted(years)}
    topic_coverage={concept:{'available_facets':sorted({role for item in candidates if subject_concepts[item['subject_key']]==concept for role in item['facets']}),'selected_facets':sorted({role for item in selected if subject_concepts[item['subject_key']]==concept for role in item['facets']})} for concept in set(subject_concepts.values())}
    return {'topic_coverage':topic_coverage,'target_resolution':reader.target_resolution,'subjects':subjects,'requested_facets':roles,'available_facets':sorted(available),'missing_facets':[r for r in roles if r not in available],
            'not_selected_facets':[r for r in roles if r in available and r not in selected_roles],
            'scope_coverage':year_coverage,'missing_scope_facets':{y:['period'] for y,c in year_coverage.items() if 'period' in roles and not c['period']},
            'missing_calendar_window':'period' in roles and not any('period' in u['facets'] and u['qualifiers'].get('period_precision')=='explicit_calendar_fields' for u in selected),
            'source_differences':[{'facet':'result_announcement','status':'different_source_statements_not_adjudicated','statements':result_statements}] if len({v['source_text'] for v in result_statements})>1 else [],
            'conditional_term_notes':[note for u in selected for note in u['qualifiers'].get('conditional_term_notes',[])],
            'units':selected,'review_candidates':reviews[:20],'references':references[:80],'scope_navigation':scope_paths[:80],'component_navigation':component_paths,
            'truncated':(['subject_window'] if subject_truncated else [])+(['information_window'] if len(rows)>2000 or (len(selected)>=limit and any(cells(item)-covered for item in remaining)) or len(references)>80 or len(scope_paths)>80 else []),'revision':reader.revision}


def information_hits(reader,bundle,*,limit=8):
    """Materialize original source spans, diversified by requested facets.

    No generated graph statement becomes answer text. Labels/headings and
    quoted spans are the source evidence; typed edges remain inspectable only.
    """
    from src.services.connected_knowledge import document_metadata
    grouped=defaultdict(list)
    for unit in bundle['units']:
        grouped[unit['document_key']].append(unit)
    ranked=sorted(grouped,key=lambda key:(-len({role for u in grouped[key] for role in u.get('facets',[u['facet']])}),key))
    # Reserve a source for each resolved information topic before filling the
    # remaining slots; one verbose process cannot starve a compound question.
    topic_by_subject={s['subject_key']:s['concept_key'] for s in bundle['subjects']}
    topic_sources=defaultdict(list)
    for key in ranked:
        for topic in {topic_by_subject[u['subject_key']] for u in grouped[key]}:topic_sources[topic].append(key)
    diversified=[]
    for topic in sorted(topic_sources):
        key=topic_sources[topic][0]
        if key not in diversified:diversified.append(key)
    ranked=diversified+[key for key in ranked if key not in diversified]
    hits=[]
    for key in ranked[:limit]:
        units=grouped[key];unique={u['unit_key']:u for u in units}
        text='\n\n'.join((u['section_heading']+'\n' if u['section_heading'] else '')+u['source_text'] for u in unique.values())
        contexts={c['locator']:c for u in unique.values() for c in u['qualifiers'].get('source_applicability_context',[]) if c.get('review_status')=='deterministic_explicit'}
        if contexts:text=applicability_context_text(contexts.values())+'\n\n'+text
        meta=document_metadata(reader.documents[key]);meta.update({k:v for k,v in units[0]['qualifiers'].items() if k in ('academic_year','semester','entry_year','college_name','program_scope_text')})
        degrees={u['qualifiers'].get('audience','unknown') for u in units}
        if len(degrees)==1:meta['audience']=next(iter(degrees))
        if units[0]['qualifiers'].get('official_snapshot'):meta['official_snapshot']=units[0]['qualifiers']['official_snapshot']
        hits.append({**meta,'chunk_id':'information:'+canonical_hash(sorted(unique))[:32],
                     'chunk_text':text[:12000],'position':0,'source_locator':' | '.join(u['locator'] for u in unique.values()),
                     'hybrid_score':1/(60+len(hits)+1),'vector_score':0.,'sparse_score':0.,
                     'information_match':1,'information_facets':sorted({role for u in units for role in u.get('facets',[u['facet']])}),
                     'information_unit_keys':list(unique),'information_evidence_units':[{'unit_key':u['unit_key'],'source_text':u['source_text'],'locator':u['locator'],'facets':u['facets'],'qualifiers':u['qualifiers']} for u in unique.values()],'connected_match':int(len(grouped)>1),
                     'information_scopes':[{'unit_key':u['unit_key'],'locator':u['locator'],'qualifiers':u['qualifiers']} for u in unique.values()],
                     'connected_revision':reader.revision,'connected_path':[p for u in unique.values() for p in u['path']],
                     'connected_lexical_score':1.,'connected_candidate':1})
    return hits


def validate_information_projection(projection,documents,sections_for,document_revision,revision):
    """Publication gate for source spans, subject membership and reference FKs."""
    errors=[];by_key={d['document_key']:d for d in documents}
    for key,row in projection['units'].items():
        _,subject,key_doc,url,locator,heading,start,end,text,qualifiers,doc_revision,graph_revision,_=row
        if subject not in projection['subjects']:errors.append('unit subject missing:'+key)
        d=by_key.get(key_doc)
        if not d:errors.append('unit source missing:'+key);continue
        if doc_revision!=document_revision(d) or graph_revision!=revision:errors.append('unit revision mismatch:'+key)
        if url!=(d.get('source_url') or ''):errors.append('unit URL mismatch:'+key)
        base=locator.rsplit('#chars=',1)[0]
        sections={s['locator']:s['text'] for s in sections_for(d)}
        p=json.loads(d.get('normalized_payload_json') or '{}')
        for context in json.loads(qualifiers).get('source_applicability_context',[]):
            context_base=context['locator'].rsplit('#chars=',1)[0]
            original=sections.get(context_base,'')
            cs,ce=context.get('span_start',-1),context.get('span_end',-1)
            if context.get('document_key')!=key_doc or context.get('source_url')!=(d.get('source_url') or '') or context.get('review_status')!='deterministic_explicit':
                errors.append('invalid applicability context provenance:'+key)
            if not (0<=cs<=ce<=len(original)) or original[cs:ce]!=context.get('source_text'):
                errors.append('applicability context span mismatch:'+key)
            unit_scope=json.loads(qualifiers);context_scope=context.get('scope',{})
            if unit_scope.get('semester') and context_scope.get('semester') and str(unit_scope['semester'])!=str(context_scope['semester']):errors.append('applicability context semester mismatch:'+key)
            if requires_table_layout_review(d,{'locator':context_base},context.get('source_text','')):
                errors.append('unreviewed table in applicability context:'+key)
        if base in sections:
            original=sections[base]
            if not (0<=start<=end<=len(original)) or original[start:end]!=text:errors.append('unit span mismatch:'+key)
            for h in json.loads(qualifiers).get('source_heading_spans',[]):
                if original[h['start']:h['end']]!=h['text']:errors.append('unit heading span mismatch:'+key)
        elif base=='SourceDocument.published_at':
            if text!=str(d.get('published_at')):errors.append('publication field mismatch:'+key)
        elif base.startswith('$.links['):
            index=int(re.search(r'\[(\d+)\]',base).group(1))
            if canonical_json(p['links'][index])!=text:errors.append('link field mismatch:'+key)
        elif base.startswith('$.'):
            field=base[2:]
            if field in p:
                if text!=str(p[field]):errors.append('canonical field mismatch:'+key)
            else:
                try:
                    fields=json.loads(text)
                    if not isinstance(fields,dict) or any(k not in p or p[k]!=v for k,v in fields.items()):errors.append('structured field mismatch:'+key)
                except ValueError:errors.append('unresolvable canonical locator:'+key)
        else:errors.append('unresolvable source locator:'+key)
    for key,row in projection['assertions'].items():
        unit=projection['units'].get(row[3])
        if not unit or row[1]!=unit[1]:errors.append('assertion/unit subject mismatch:'+key)
        if row[1] not in projection['subjects']:errors.append('assertion subject missing:'+key)
    for key,row in projection['references'].items():
        if row[1] not in projection['units'] or row[2] not in projection['subjects']:errors.append('reference target missing:'+key)
    for key,row in projection['links'].items():
        if row[1] not in projection['subjects'] or row[2] not in projection['subjects']:errors.append('subject scope reference missing:'+key)
    return errors
