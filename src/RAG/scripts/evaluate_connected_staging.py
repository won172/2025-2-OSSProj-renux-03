"""Replay root-supplied public cached HTTP and canonical snapshot evidence.

No network, no serving writes, no LLM. This is NOT a hybrid/answer-quality
benchmark: it measures collection/import/graph contracts and lexical-vs-graph
retrieval on the identical staged corpus. All output stays in explicit staging.
"""
from pathlib import Path
import argparse
from datetime import date
import json
import sqlite3
import sys
import time
import hashlib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.pipelines.official_public import StagingWorkspace,import_public_snapshot,load_policy,import_collection
from src.collectors.official_public import Collector,Fetcher,Budgets,Policy,Response
from src.services.connected_knowledge import rebuild_connected_projection,connected_search,Scope


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staging-dir',required=True,type=Path)
    parser.add_argument('--snapshot',required=True,type=Path)
    parser.add_argument('--snapshot-sha256',required=True)
    parser.add_argument('--response-checkpoint',required=True,type=Path)
    parser.add_argument('--as-of',required=True,type=date.fromisoformat)
    parser.add_argument('--source-fixtures', type=Path, help='optional agent-labelled source-match cases, not answer-quality labels')
    parser.add_argument('--scale-copies',type=int,default=1,choices=[1,2],help='2 is an explicitly synthetic performance workload, never real coverage')
    args=parser.parse_args()
    workspace=StagingWorkspace(args.staging_dir)
    if not workspace.marker.exists():workspace.initialize()
    imported=import_public_snapshot(workspace,args.snapshot,args.snapshot_sha256)
    if args.scale_copies==2:
        with sqlite3.connect(workspace.database) as db:
            db.row_factory=sqlite3.Row
            originals=[dict(r) for r in db.execute("SELECT * FROM source_documents WHERE source_id NOT LIKE '%:scale-copy-2'")]
            for row in originals:
                row['source_id']+=':scale-copy-2';row['document_key']=row['dataset']+':'+row['source_id'];row.pop('id')
                columns=sorted(row)
                db.execute('INSERT OR IGNORE INTO source_documents('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',[row[c] for c in columns])
    connection=sqlite3.connect(args.response_checkpoint.resolve().as_uri()+'?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    responses={r['url']:dict(r) for r in connection.execute('SELECT * FROM receipts')}
    connection.close()
    manifest=load_policy(Path(__file__).resolve().parents[1]/'data/official_public_inventory.json').manifest
    manifest=dict(manifest)
    manifest['approved_sources']=[e for e in manifest['approved_sources'] if e['url'] in responses or e['url']=='https://www.dongguk.edu/']
    for entry in manifest['approved_sources']:
        if entry['url']=='https://www.dongguk.edu/':entry['seed']=False
    policy=Policy(manifest)
    budgets=Budgets(pages=len(responses),requests=max(1,len(responses)),seconds=300)
    def transport(url,address,remaining):
        row=responses[url]
        raw=Path(row['raw_path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row['raw_sha256']:raise ValueError('supplied cached response hash mismatch')
        return Response(row['http_status'],{'content-type':'text/html'},raw)
    collector=Collector(workspace.checkpoint,policy,budgets,fetcher=Fetcher(policy,budgets,transport=transport,resolver=lambda _:['93.184.216.34'],sleep=lambda _:None))
    try:collection=collector.run(refresh=True)
    finally:collector.close()
    collected=import_collection(workspace)
    began=time.perf_counter()
    with sqlite3.connect(workspace.database) as db:
        graph=rebuild_connected_projection(db)
        information_sources=[dict(zip(('document_key','dataset','units','assertions','facets'),r)) for r in db.execute("SELECT d.document_key,d.dataset,count(DISTINCT u.unit_key),count(DISTINCT a.assertion_key),group_concat(DISTINCT a.predicate) FROM source_documents d LEFT JOIN information_units u USING(document_key) LEFT JOIN information_assertions a USING(unit_key) GROUP BY d.document_key,d.dataset")]
    build_seconds=time.perf_counter()-began
    queries=['휴학 신청 방법과 문의 부서 연락처','등록금 납부 방법과 연락처','컴퓨터공학과 졸업 기준','상록원 오늘 점심 메뉴']
    cases=[]
    for query in queries:
        for mode in (False,True,True):
            began=time.perf_counter();result=connected_search(workspace.database,query,scope=Scope(as_of=args.as_of),connected=mode)
            cases.append({'query':query,'connected':mode,'seconds':time.perf_counter()-began,'status':result['status'],'reason':result.get('reason'),
                          'keys':[h['document_key'] for h in result['hits']],'urls':[h['url'] for h in result['hits']],
                          'truncated':result['truncated']})
    source_matches=[]
    if args.source_fixtures:
        for fixture in json.loads(args.source_fixtures.read_text())['cases']:
            detail=dict(fixture)
            for name,mode in (('lexical',False),('connected',True)):
                result=connected_search(workspace.database,fixture['query'],scope=Scope(as_of=args.as_of),connected=mode)
                urls=[h['url'] for h in result['hits']]
                detail[name]={'expected_rank':urls.index(fixture['expected_url'])+1 if fixture['expected_url'] in urls else None,'urls':urls,'status':result['status']}
            source_matches.append(detail)
    report={'scope':'root supplied tracked public snapshot + cached official HTTP; no new live requests; lexical-vs-information retrieval only, not production hybrid or answer quality',
            'snapshot_import':imported,'collection_states':collection['states'],'institution_entries':len(collection['institution_inventory']),
            'synthetic_scale_copies':args.scale_copies,'collected_import':collected,'graph':graph,'build_seconds':build_seconds,
            'information_source_coverage':information_sources,'source_match_cases':source_matches,'queries':cases}
    output=workspace.directory/'evaluation.json';output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'report':str(output),'documents':graph['documents'],'build_seconds':build_seconds,
                      'information':{k:v for k,v in graph.get('information',{}).items() if k not in {'unclassified','concept_source_coverage'}},
                      'source_match_ranks':[{k:v for k,v in r.items() if k in ('id','lexical','connected')} for r in source_matches],
                      'query_seconds':[round(c['seconds'],4) for c in cases]},ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
