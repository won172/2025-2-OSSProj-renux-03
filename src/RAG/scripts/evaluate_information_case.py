#!/usr/bin/env python3
"""Read-only source-oracle replay on an explicit disposable canonical corpus.

Agent-authored assertions against actual G/N/H/E25/E26 source captures. This
measures evidence retrieval and provenance, not human answer quality or hybrid
production retrieval. Never fetches HTTP or invokes models.
"""
from pathlib import Path
from datetime import date
import argparse,json,sys,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.pipelines.official_public import StagingWorkspace
from src.services.connected_knowledge import ConnectedReader,Scope,read_only
from src.services.information_graph import information_bundle

QUERIES=[
 '2026학년도 2학기 전공결정 신청 대상과 기간, 신청 방법과 예외를 알려줘',
 '2026년 2학기에 입학한 외국인 신입생도 이번 10월에 전공결정을 신청해?',
 'AI소프트웨어융합학부는 몇 지망까지 써야 하고 신청 메뉴는 뭐야?',
 '전공결정 공지에 붙은 HWP를 작성해서 제출해야 해?',
 '2025년과 2026년 2학기 전공결정 신청기간을 비교해줘',
 '2026학년도 2학기 전공결정 대상 자격 신청 기간 방법 결과 발표 예외 선발기준 미신청 불이익 첨부 제출서류 승인',
 '전공결정 필수 교과목 학수번호와 제출 서류',
]


def evaluate(reader):
    cases=[]
    for query in QUERIES:
        started=time.perf_counter();bundle=information_bundle(reader,query)
        cases.append({'query':query,'seconds':time.perf_counter()-started,'bundle':bundle})
    checks=[]
    def check(name,value):checks.append({'check':name,'passed':bool(value)})
    def text(i,review=False):return '\n'.join(u['source_text'] for u in cases[i]['bundle']['review_candidates' if review else 'units'])
    for i,case in enumerate(cases):
        units=case['bundle']['units']
        check(f'{i}:coalesced_source_units',len(units)==len({u['unit_key'] for u in units}))
        check(f'{i}:only_major_selection',all(s['concept_key']=='major_selection' for s in case['bundle']['subjects']))
        check(f'{i}:review_tables_not_known',all(u['review_status']!='needs_table_layout_review' for u in units))
        check(f'{i}:dependent_context_review_gate',all(c.get('review_status')=='deterministic_explicit' and '인원배정' not in c['source_text'] for u in units for c in u['qualifiers'].get('source_applicability_context',[])))
        check(f'{i}:dependent_context_semester_gate',all(not u['qualifiers'].get('semester') or not c.get('scope',{}).get('semester') or str(u['qualifiers']['semester'])==str(c['scope']['semester']) for u in units for c in u['qualifiers'].get('source_applicability_context',[])))
        check(f'{i}:dependent_context_branch_identity',all(c.get('branch_id') and c.get('branch_relation') for u in units for c in u['qualifiers'].get('source_applicability_context',[])))
        check(f'{i}:no_first_semester_window',not any(u['qualifiers'].get('semester_scope_origin')=='local_heading' and str(u['qualifiers'].get('semester'))=='1' for u in units))
        for unit in units:
            check(f'{i}:subject_agreement:{unit["unit_key"]}',all(p['subject_key']==unit['subject_key'] and p['unit_key']==unit['unit_key'] for p in unit['path']))
    b0=cases[0]['bundle'];b1=cases[1]['bundle'];b2=cases[2]['bundle'];b3=cases[3]['bundle'];b4=cases[4]['bundle']
    check('compound_existing_and_new_sources',{'rules','guidance','notices','attachments'}<={reader.documents[u['document_key']]['dataset'] for u in b0['units']})
    check('compound_all_requested_facets_selected',not b0['missing_facets'] and not b0['not_selected_facets'])
    check('A1_explicit_exclusion','학과별 또는 전공확정 후 입학생 제외' in text(5))
    check('A2_lower_priority_not_ineligible','후순위' in text(5) and '1학년 2학기말' in text(5))
    check('A2_conditional_approval',any('approval_condition' in u['facets'] and '승인' in u['source_text'] for u in cases[5]['bundle']['units']))
    check('A3_eligibility_branches',all(value in text(0) for value in ('영어영문학부','2027학년도 봄 졸업대상자','조기')))
    check('A4_physics_exception','물리·반도체과학부' in text(0) and '졸업대상자가 아닌 일반 재학생도 신청 가능' in text(0))
    check('A5_foreign_admission_deferral','2027학년도 1학기' in text(1) and b1['missing_calendar_window'] and '09:00' not in text(1))
    check('A5_three_source_corroboration',{'rules','notices','attachments'}<={reader.documents[u['document_key']]['dataset'] for u in b1['units']})
    check('A6_exact_printed_application_clock',all(value in text(0) for value in ('2026. 10. 19.','09:00','23:59')))
    check('A6_timezone_unknown',all(u['qualifiers'].get('timezone')=='not_printed' for u in b0['units'] if u['qualifiers'].get('printed_time_precision')=='clock_time'))
    check('A7_five_preferences_default_channel',all(value in text(2) for value in ('5지망','학생정보[신청함]','전공미결정생전공신청')))
    check('A8_business_specific_route',any('광역화전공신청' in u['source_text'] and '경영대학' in u['qualifiers'].get('program_scope_text','') for u in b0['units']))
    check('A8_special_H_branches',all(value in text(0) for value in ('열린전공학부','바이오시스템대학','광역화전공신청')))
    check('A9_source_difference_preserved','2026. 11. 06.' in text(0) and '추후 안내 예정' in text(0) and bool(b0['source_differences']))
    check('A10_unreviewed_table_retained','탐색' in text(5,True) and any(u['qualifiers'].get('layout_status')=='flattened_table_requires_column_review' for u in cases[5]['bundle']['review_candidates']))
    check('A11_plan_not_submission_form','documents' in b3['missing_facets'] and any('plan_attachment' in u['facets'] for u in b3['units']) and bool(b3['references']))
    check('A12_possible_consequence','강제 배정 가능' in text(5))
    check('A12_deferral_table_review','유예' in text(5,True))
    check('historical_and_current_windows',all(value in text(4) for value in ('2025. 10. 20.','2026. 10. 19.','09:00','23:59')) and not b4['missing_scope_facets'])
    check('unknown_prerequisite_codes_forms',{'course_reference','documents'}<=set(cases[6]['bundle']['missing_facets']))
    return {'scope':'same staged corpus source-oracle retrieval; agent-authored, no models/network/production/human answer labels','revision':reader.revision,'checks':checks,'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),'cases':cases}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--staging-dir',required=True,type=Path);p.add_argument('--as-of',required=True,type=date.fromisoformat);args=p.parse_args()
    workspace=StagingWorkspace(args.staging_dir);workspace.validate()
    with read_only(workspace.database) as db:
        db.execute('BEGIN');report=evaluate(ConnectedReader(db,scope=Scope(as_of=args.as_of)))
    output=workspace.directory/'information-oracle-checks.json';output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'report':str(output),'passed':report['passed'],'failed':[c['check'] for c in report['checks'] if not c['passed']]},ensure_ascii=False,indent=2))
    return int(bool(report['failed']))

if __name__=='__main__':raise SystemExit(main())
