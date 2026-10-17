#!/usr/bin/env python3
"""Disposable candidate CLI. Every command requires --staging-dir explicitly."""
from __future__ import annotations
import argparse
from datetime import date
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collectors.official_public import Collector, Budgets
from src.pipelines.official_public import StagingWorkspace, load_policy, import_collection
from src.services.connected_knowledge import (rebuild_connected_projection, connected_search, read_only, ConnectedReader, Scope)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-dir", required=True, type=Path)
    parser.add_argument("--checkpoint-name", default="collection.sqlite3",
                        help="optional named frontier; reviewed policy upgrades also preserve the same checkpoint history")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    snapshot=commands.add_parser('import-snapshot',help='non-overwriting import from an explicit public read-only canonical snapshot')
    snapshot.add_argument('--snapshot',required=True,type=Path)
    snapshot.add_argument('--snapshot-sha256',required=True)
    snapshot.add_argument('--acknowledge-public-snapshot',action='store_true',required=True)
    fixture = commands.add_parser("seed-fixture", help="load labelled synthetic competency corpus into empty staging DB only")
    fixture.add_argument("--fixture", required=True, type=Path)
    collect = commands.add_parser("collect")
    collect.add_argument("--manifest", type=Path, default=Path(__file__).resolve().parents[1]/"data/official_public_inventory.json")
    collect.add_argument("--retry-failed", action="store_true")
    collect.add_argument("--refresh", action="store_true", help="revisit completed frontier; failed/partial fetches never withdraw canonical rows")
    collect.add_argument("--backfill-attachments", action="store_true")
    for name, default, type_ in (("pages",100,int),("requests",150,int),("total-bytes",30000000,int),
                                 ("response-bytes",8000000,int),("depth",3,int),("seconds",300,float),
                                 ("per-host-seconds",1,float),("frontier",5000,int)):
        collect.add_argument("--"+name, default=default, type=type_)
    commands.add_parser("import")
    commands.add_parser("build")
    commands.add_parser("coverage")
    export = commands.add_parser("export")
    export.add_argument("--output", type=Path, required=True)
    for name in ("search", "replay", "inspect",'bundle'):
        p = commands.add_parser(name)
        p.add_argument("--query", default="")
        p.add_argument("--seed", action="append", default=[])
        p.add_argument("--campus", choices=["seoul", "bmc", "shared"])
        p.add_argument("--department", help="trusted requester department; does not grant another department's access")
        p.add_argument('--college')
        p.add_argument('--program')
        p.add_argument('--academic-years', type=int, nargs='+')
        p.add_argument("--audience", default="common")
        p.add_argument("--entry-year", type=int)
        p.add_argument("--academic-year", type=int)
        p.add_argument("--semester")
        p.add_argument('--meal-date', type=date.fromisoformat, help='serving date; as-of remains knowledge/reference date')
        p.add_argument("--as-of", type=date.fromisoformat, required=True)
        if name == "replay":
            p.add_argument("--fixtures", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        workspace = StagingWorkspace(args.staging_dir)
        if Path(args.checkpoint_name).name != args.checkpoint_name or not args.checkpoint_name.endswith(".sqlite3") or args.checkpoint_name == "canonical.sqlite3":
            raise ValueError("checkpoint-name must be a local .sqlite3 filename distinct from canonical.sqlite3")
        workspace.checkpoint = workspace.directory / args.checkpoint_name
        if args.command == "init":
            report = workspace.initialize()
        else:
            workspace.validate()
            if args.command == 'import-snapshot':
                from src.pipelines.official_public import import_public_snapshot
                report=import_public_snapshot(workspace,args.snapshot,args.snapshot_sha256)
            elif args.command == "seed-fixture":
                fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
                if fixture.get("kind") != "synthetic_connected_competency":
                    raise ValueError("only labelled synthetic competency fixtures are accepted")
                from src.pipelines.canonical import canonical_hash, canonical_json
                with sqlite3.connect(workspace.database) as db:
                    if db.execute("SELECT count(*) FROM source_documents").fetchone()[0]:
                        raise ValueError("seed-fixture requires an empty staging corpus")
                    for dataset, source_id, fields in fixture["documents"]:
                        fields = dict(fields)
                        status, source_type = fields.pop("_status", "active"), fields.pop("_type", dataset)
                        source_url = fields.pop("_url", "https://www.dongguk.edu/page/"+source_id)
                        db.execute("INSERT INTO source_documents(dataset,source_type,source_id,document_key,source_url,title,status,content_hash,normalized_payload_json) VALUES(?,?,?,?,?,?,?,?,?)",
                                   (dataset,source_type,source_id,dataset+":"+source_id,source_url,fields.get("title",source_id),status,canonical_hash(fields),canonical_json(fields)))
                report = {"status":"success","scope":"synthetic fixture only","documents":len(fixture["documents"])}
            elif args.command == "collect":
                budgets = Budgets(**{key: getattr(args, key) for key in ("pages", "requests", "total_bytes", "response_bytes", "depth", "seconds", "per_host_seconds", "frontier")})
                collector = Collector(workspace.checkpoint, load_policy(args.manifest), budgets)
                try:
                    if args.backfill_attachments:
                        from src.database import SourceDocument
                        from sqlalchemy import create_engine
                        from sqlalchemy.orm import Session
                        engine = create_engine("sqlite:///" + str(workspace.database))
                        try:
                            with Session(engine) as session:
                                collector.backfill_attachments(session.query(SourceDocument).all())
                        finally:
                            engine.dispose()
                    report = collector.run(retry_failed=args.retry_failed, refresh=args.refresh)
                finally:
                    collector.close()
            elif args.command == "import":
                report = import_collection(workspace)
            elif args.command == "build":
                with sqlite3.connect(workspace.database) as db:
                    report = rebuild_connected_projection(db)
            elif args.command in {"coverage", "export"}:
                with read_only(workspace.database) as db:
                    db.execute('BEGIN')
                    exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='connected_manifest'").fetchone()
                    manifest = db.execute("SELECT report_json FROM connected_manifest WHERE id=1").fetchone() if exists else None
                    report = json.loads(manifest[0]) if manifest else {"status": "missing_graph"}
                    graph_valid=False
                    if manifest:
                        try:
                            ConnectedReader(db)
                            graph_valid=True
                        except (ValueError,sqlite3.Error) as exc:
                            report={'status':'fallback','reason':str(exc),'previous_projection_coverage':report}
                    if args.command == "export":
                        if not graph_valid:raise ValueError('export requires a current validated graph')
                        # Export is staging-operator only; contains all staged scopes.
                        output = args.output.absolute()
                        if output.is_symlink() or output.resolve().parent != workspace.directory or output.suffix.lower()!='.json' or output==workspace.marker:
                            raise ValueError("export must target a non-control .json file directly inside staging directory")
                        report = {"coverage": report, "nodes": [dict(r) for r in db.execute("SELECT * FROM connected_nodes")],
                                  "edges": [dict(r) for r in db.execute("SELECT * FROM connected_edges")],
                                  "information": {name: [dict(r) for r in db.execute("SELECT * FROM "+name)] for name in ('information_subjects','information_units','information_assertions','information_references','information_subject_links')}}
                        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                        report = {"output": str(output), "revision": report["coverage"].get("revision")}
                if args.command == "coverage" and workspace.checkpoint.exists():
                    with sqlite3.connect(workspace.checkpoint.resolve().as_uri()+'?mode=ro',uri=True) as db:
                        db.row_factory = sqlite3.Row
                        report["collection_states"] = dict(db.execute("SELECT status,count(*) FROM frontier GROUP BY status"))
                        report["sources"] = [dict(r) for r in db.execute("SELECT url,owner,campus_scope,kind,status,attempts,error,parent_url,locator FROM frontier ORDER BY url")]
                    with read_only(workspace.database) as db:
                        for item in report["sources"]:
                            keys = [r[0] for r in db.execute("SELECT document_key FROM source_documents WHERE source_url=? AND status IN ('active','updated')",(item["url"],))]
                            item["canonical_documents"] = keys
                            item['information_units'] = sum(db.execute('SELECT count(*) FROM information_units WHERE document_key=?',(k,)).fetchone()[0] for k in keys) if graph_valid else 0
                            item['information_facets'] = sorted({r[0] for k in keys for r in db.execute('SELECT DISTINCT predicate FROM information_assertions a JOIN information_units u USING(unit_key) WHERE u.document_key=?',(k,))}) if graph_valid else []
                            item["graph_document_nodes"] = sum(db.execute("SELECT count(*) FROM connected_nodes WHERE document_key=?",(k,)).fetchone()[0] for k in keys) if graph_valid else 0
                            item["searchable_chunks"] = sum(db.execute("SELECT count(*) FROM connected_chunks WHERE document_key=?",(k,)).fetchone()[0] for k in keys) if graph_valid else 0
            else:
                scope = Scope(campus=args.campus, department=args.department, audience=args.audience,
                              entry_year=args.entry_year, academic_year=args.academic_year, semester=args.semester, as_of=args.as_of, college=args.college, program=args.program, academic_years=tuple(args.academic_years or ()),meal_start=args.meal_date,meal_end=args.meal_date)
                if args.command == 'bundle':
                    from src.services.information_graph import information_bundle
                    with read_only(workspace.database) as db:
                        db.execute('BEGIN')
                        report=information_bundle(ConnectedReader(db,scope=scope),args.query)
                elif args.command == "inspect":
                    with read_only(workspace.database) as db:
                        db.execute("BEGIN")
                        report = ConnectedReader(db, scope=scope).inspect(args.seed)
                elif args.command == "search":
                    report = connected_search(workspace.database, args.query, scope=scope, seeds=args.seed)
                else:
                    reports = []
                    for fixture in json.loads(args.fixtures.read_text()):
                        changes = dict(fixture.get("scope",{}))
                        if "as_of" in changes:
                            changes["as_of"] = date.fromisoformat(changes["as_of"])
                        case_scope = replace(scope,**changes)
                        baseline = connected_search(workspace.database, fixture["query"], scope=case_scope, connected=False)
                        candidate = connected_search(workspace.database, fixture["query"], scope=case_scope)
                        expected = set(fixture.get("expected_document_keys",[]))
                        detail = {"id":fixture.get("id"), "query": fixture["query"], "baseline": baseline, "candidate": candidate}
                        for name,result in (("baseline",baseline),("candidate",candidate)):
                            keys = [h["document_key"] for h in result["hits"]]
                            detail[name+"_recall_at_3"] = len(expected.intersection(keys[:3])) / len(expected) if expected else None
                            detail[name+"_no_answer_correct"] = not keys if not expected else None
                        reports.append(detail)
                    changed = sum([h["document_key"] for h in r["baseline"]["hits"]] != [h["document_key"] for h in r["candidate"]["hits"]] for r in reports)
                    report = {"scope": "same corpus lexical baseline vs connected candidate; structural fixture labels, retrieval only, no answer-quality claim", "changed_cases":changed,"cases": reports}
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 1 if report.get("status") in {"partial", "fallback", "missing_graph"} else 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
