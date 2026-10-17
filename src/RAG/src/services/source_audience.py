"""Conservative degree applicability from explicit source identities/labels.

This is content scope, not requester authorization or personal eligibility.
Incidental body words, contacts, offices and host names are not audience proof.
"""
import re


def labelled_degree(text):
    value=re.sub(r'\s+','',str(text or '')).lower()
    if not value or re.search(r'제외|면제|불가|문의처|담당부서|대학원실',value):return 'unknown'
    if re.search(r'학부(?:및|와|·|/|,|ㆍ)대학원|학부생(?:및|과|·|/)대학원생|대학\(원\)생',value):return 'common'
    graduate=bool(re.search(r'^(?:일반|전문|특수)?대학원$|대학원(?:생|휴학|복학|수강|졸업|등록|학적|학위|입학|과정|석사|박사|학사|학칙|교육|규정|요람)|(?:석사|박사|석박사)(?:과정|재학생|학생|학위)',value))
    undergraduate=bool(re.search(r'^학부$|학부생|학부재학생|학사과정|학부모집단위입학생',value))
    return 'common' if graduate and undergraduate else 'graduate' if graduate else 'undergraduate' if undergraduate else 'unknown'


def source_degree_scope(title,sections=()):
    title=str(title or '');segments=re.split(r'\s+-\s+|\s*>\s*',title)
    title_evidence=[]
    for index,segment in enumerate(segments):
        compact=re.sub(r'\s+','',segment)
        # The top-level mixed menu is navigation, followed by a reviewed
        # literal undergraduate/graduate hierarchy branch on the main site.
        if compact in {'대학/대학원','대학ㆍ대학원','대학·대학원'}:continue
        brand_only=bool(re.fullmatch(r'동국대학교(?:[가-힣]+(?:대학원|대학|학부|학과))?',compact))
        audience='undergraduate' if compact=='대학' and index and re.sub(r'\s+','',segments[index-1]) in {'대학/대학원','대학ㆍ대학원','대학·대학원'} else labelled_degree(segment) if index or len(segments)==1 or not brand_only else 'unknown'
        if audience!='unknown':title_evidence.append({'audience':audience,'source_field':'title','locator':f'title/segment/{index}','source_text':segment,'derivation':'explicit_content_identity_label'})
    title_values={e['audience'] for e in title_evidence}
    title_scope=next(iter(title_values)) if len(title_values)==1 else 'unknown'
    evidence=list(title_evidence);section_values=set()
    for section in sections:
        heading=section.get('heading','')
        # Ignore the extractor's default whole-document breadcrumb heading.
        if heading==title:continue
        audience=labelled_degree(heading)
        if audience!='unknown':
            section_values.add(audience);evidence.append({'audience':audience,'source_field':'section_heading','locator':section.get('locator','')+'/heading','source_text':heading,'derivation':'explicit_section_degree_label'})
    known=title_values|section_values
    mixed=len(known)>1
    audience='unknown' if mixed else title_scope
    if audience=='unknown' and not mixed and section_values and len(section_values)==1:
        # A lone scoped subsection cannot broadcast its audience to unrelated
        # sibling sections. The per-unit adapter retains the scoped subsection.
        audience='unknown'
    return {'audience':audience,'title_audience':title_scope,'status':'mixed_scope_requires_unit_evidence' if mixed else 'explicit_identity' if audience!='unknown' else 'unknown','evidence':evidence}


def source_scope_from_payload(document,payload):
    title=document.get('title') or payload.get('title','')
    result=source_degree_scope(title,payload.get('sections',[]) if isinstance(payload.get('sections'),list) else [])
    declared=payload.get('audience')
    if declared in {'undergraduate','graduate'} and result['audience'] in {'undergraduate','graduate'} and declared!=result['audience']:
        return {**result,'audience':declared,'status':'scope_conflict_requires_review'}
    if declared in {'undergraduate','graduate'} and result['audience']=='unknown':
        result={**result,'audience':declared,'status':'explicit_canonical_scope','evidence':result['evidence']+[{'source_field':'$.audience','source_text':declared,'derivation':'canonical_scope_metadata'}]}
    if declared=='common' and payload.get('audience_evidence',{}).get('status')=='explicit_identity' and result['status']=='unknown':
        result={**result,'audience':'common','status':'explicit_canonical_common','evidence':payload['audience_evidence'].get('evidence',[])}
    return result


def labelled_condition_degree(text,heading=''):
    """A degree in an explicit target/eligibility field, never a word count."""
    evidence=[];offset=0;lines=str(text).splitlines(keepends=True)
    label=re.compile(r'^(?:#+\s*)?(?:[가-힣0-9]+[.)]\s*)?(?:신청\s*대상(?:자)?|지원\s*대상(?:자)?|대상자?|지원\s*자격|신청\s*자격)\s*(?:[:：|]\s*(.*))?$')
    for index,line in enumerate(lines):
        match=label.match(line.strip())
        if match:
            value=match.group(1) or (lines[index+1].strip() if index+1<len(lines) else '')
            degree=labelled_degree(value)
            if degree!='unknown':evidence.append({'audience':degree,'source_text':value,'source_field':'explicit_eligibility_label','offset':offset})
        offset+=len(line)
    leaf=heading.split(' > ')[-1].strip()
    if label.match(leaf) and not evidence:
        degree=labelled_degree(text)
        if degree!='unknown':evidence.append({'audience':degree,'source_text':str(text),'source_field':'labelled_eligibility_section','offset':0})
    degrees={item['audience'] for item in evidence}
    return {'audience':next(iter(degrees)) if len(degrees)==1 else 'unknown','evidence':evidence,'status':'explicit_eligibility_label' if len(degrees)==1 else 'unknown_or_mixed'}
