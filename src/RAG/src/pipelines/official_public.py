"""Explicit disposable staging workspace and non-destructive canonical import.

No serving publication entry point is provided. Reviewed promotion must pass
existing lineage/publication/approval gates; a staging marker is not approval.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import uuid
import re
from dataclasses import asdict
from urllib.parse import urlsplit

from src.collectors.official_public import Policy, digest, canonical_url, EXTENSIONS
from src.collectors.official_extract import extract_html, extract_attachment_bounded
from src.pipelines.canonical import canonical_hash, canonical_json


class StagingWorkspace:
    def __init__(self, directory):
        raw = Path(directory).absolute()
        if raw != raw.resolve():
            raise ValueError("staging path may not contain symlinks")
        self.directory = raw
        self.database = raw / "canonical.sqlite3"
        self.checkpoint = raw / "collection.sqlite3"
        self.marker = raw / ".connected-staging.json"

    def initialize(self):
        if self.directory.exists() and any(self.directory.iterdir()):
            raise ValueError("init requires an empty/new disposable staging directory")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.marker.write_text(canonical_json({"schema_version": 1, "kind": "disposable_connected_staging",
                                              "directory": str(self.directory), "id": str(uuid.uuid4())}), encoding="utf-8")
        self.validate()
        # Create only in the explicit staging DB. No default init_db/migration.
        from sqlalchemy import create_engine
        from src.database import Base
        engine = create_engine("sqlite:///" + str(self.database))
        try:
            Base.metadata.create_all(engine)
        finally:
            engine.dispose()
        return {"staging_directory": str(self.directory), "database": str(self.database)}

    def validate(self):
        if not self.marker.is_file() or self.marker.is_symlink():
            raise ValueError("missing disposable staging marker; run init first")
        marker = json.loads(self.marker.read_text(encoding="utf-8"))
        if marker.get("kind") != "disposable_connected_staging" or marker.get("directory") != str(self.directory):
            raise ValueError("invalid/moved staging marker")
        for path in (self.database, self.checkpoint):
            if path.is_symlink() or path.resolve().parent != self.directory:
                raise ValueError("staging database symlink rejected")
        serving = Path(os.getenv("RAG_DATABASE_FILE") or Path(__file__).resolve().parents[2] / "rag_database.db").resolve()
        if self.database.resolve() == serving or self.checkpoint.resolve() == serving:
            raise ValueError("refusing serving/default database target")
        return self


def load_policy(path):
    path = Path(path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get('institution_manifest'):
        institutions = json.loads((path.parent/manifest['institution_manifest']).read_text(encoding='utf-8'))
        existing={entry['url'] for entry in manifest['approved_sources']}
        manifest['approved_sources'].extend(entry for entry in institutions['approved_sources'] if entry['url'] not in existing)
        manifest['verified_upgrades'] = institutions['verified_upgrades']
    if manifest.get("guidance_manifest"):
        guidance = json.loads((path.parent / manifest["guidance_manifest"]).read_text(encoding="utf-8"))
        for source in guidance["sources"]:
            manifest["approved_sources"].append({
                "url": source["source_url"], "owner": "동국대학교", "campus_scope": "unknown",
                "verification_url": source["discovery_url"], "expected_title": source["title"],
                "body_markers": source["body_markers"], "selector": "#content_focus.contents > .inner",
                "verified_on": guidance["verified_on"], "scope_note": source["scope_note"]})
    return Policy(manifest)


def _discovery_parent(workspace, source, database, policy, parent_url, child_url, report):
    """Verified navigation provenance, with no searchable homepage body.

    This identity uses the same key as a future fully extracted guidance page.
    It may authorize only the explicit file links reproduced from its receipt.
    An existing private/inactive parent is never replaced or reactivated.
    """
    row=source.execute('SELECT f.*,r.final_url,r.http_status,r.fetched_at,r.raw_sha256,r.raw_path,r.extraction_json,r.manifest_hash FROM frontier f JOIN receipts r USING(url) WHERE f.url=?',(parent_url,)).fetchone()
    if row is None or row['status']!='discovery_only' or row['http_status']!=200:
        raise ValueError('attachment parent lacks a current HTTP-200 discovery receipt')
    policy.entry(row['url']);entry=policy.entry(row['final_url'])
    from src.services.source_authority import requires_authority_review
    if requires_authority_review(row['final_url'],entry):raise ValueError('unreviewed attachment parent authority')
    if row['manifest_hash']!=policy.hash:
        historic=source.execute('SELECT manifest_json FROM policies WHERE hash=?',(row['manifest_hash'],)).fetchone()
        if not historic:raise ValueError('parent receipt manifest history missing')
        old=Policy(json.loads(historic[0]))
        if old.hash!=row['manifest_hash']:raise ValueError('parent receipt manifest mismatch')
        old.entry(row['url']);old.entry(row['final_url'])
    path=Path(row['raw_path']);raw_root=workspace.directory/'raw'
    if raw_root.is_symlink() or raw_root!=raw_root.resolve() or path.is_symlink() or path.resolve().parent!=raw_root:raise ValueError('parent raw receipt outside staging raw store')
    if path.stat().st_size>20_000_000:raise ValueError('parent raw receipt exceeds hard byte limit')
    raw=path.read_bytes()
    if digest(raw)!=row['raw_sha256']:raise ValueError('parent raw receipt hash mismatch')
    parsed=extract_html(raw,row['final_url'],selector=entry.get('selector'),expected_title=entry.get('expected_title',''),body_markers=entry.get('body_markers',[]),discovery_only=row['kind'] in {'directory','sitemap','board'})
    if parsed.status!='discovery_only' or canonical_hash(asdict(parsed))!=canonical_hash(json.loads(row['extraction_json'])):
        raise ValueError('parent discovery extraction does not reproduce')
    links=[]
    for link in parsed.discovery_links+parsed.links:
        url=canonical_url(link['url'])
        if link.get('kind')!='attachment' and Path(urlsplit(url).path).suffix.lower() not in EXTENSIONS and Path(link.get('label','')).suffix.lower() not in EXTENSIONS:continue
        try:policy.entry(url)
        except ValueError:continue
        links.append({**link,'url':url,'kind':'attachment'})
    if canonical_url(child_url) not in {link['url'] for link in links}:
        raise ValueError('attachment absent from verified parent download anchors')
    existing=database.execute('SELECT * FROM source_documents WHERE source_url IN (?,?)',(row['url'],row['final_url'])).fetchall()
    if len(existing)>1:raise ValueError('ambiguous canonical attachment parent')
    previous=existing[0] if existing else None
    if previous:
        old_payload=json.loads(previous['normalized_payload_json'] or '{}')
        if previous['status'] not in {'active','updated','provenance'} or old_payload.get('visibility','public')!='public':raise ValueError('parent inactive/non-public; provenance cannot broaden access')
        if not old_payload.get('provenance_only'):return previous['document_key']
        key=previous['document_key'];source_id=previous['source_id']
    else:
        source_id=canonical_hash(row['url']);key='guidance:'+source_id
        if database.execute('SELECT 1 FROM source_documents WHERE document_key=?',(key,)).fetchone():raise ValueError('parent identity collision')
    normalized={'title':parsed.title,'content':'','sections':[],'links':links,'provenance_only':True,'answerable':False,
                'visibility':'public','campus_scope':row['campus_scope'],'audience':'unknown','source_owner':row['owner'],
                'source_url':row['url'],'parser_version':parsed.parser,'raw_sha256':row['raw_sha256']}
    raw_payload={'raw_sha256':row['raw_sha256'],'final_url':row['final_url'],'manifest_hash':row['manifest_hash'],'http_status':200,
                 'parser':parsed.parser,'fetched_at':row['fetched_at'],'derivation':'verified_download_anchor_provenance_only'}
    if previous:_preserve_refresh_scope(previous,normalized,provenance_only=True)
    content_hash=canonical_hash(normalized,exclude_fields=('raw_sha256',))
    if previous:
        if previous['content_hash']!=content_hash:
            database.execute("UPDATE source_documents SET title=?,content_hash=?,normalized_payload_json=?,raw_payload_json=?,collected_at=?,last_parsed_at=? WHERE document_key=?",(parsed.title,content_hash,canonical_json(normalized),canonical_json(raw_payload),row['fetched_at'],row['fetched_at'],key))
    else:
        database.execute("INSERT INTO source_documents(dataset,source_type,source_id,source_url,document_key,title,status,content_hash,schema_version,raw_payload_json,normalized_payload_json,collected_at,last_parsed_at,miss_count) VALUES('guidance','official_discovery_parent',?,?,?,?,'provenance',?,1,?,?,?,?,0)",(source_id,row['url'],key,parsed.title,content_hash,canonical_json(raw_payload),canonical_json(normalized),row['fetched_at'],row['fetched_at']))
    if key not in report['provenance_parents']:report['provenance_parents'].append(key)
    return key


def _preserve_refresh_scope(previous, incoming, *, provenance_only=False):
    """Public collection cannot change a canonical document's access grant.

    This is shared by every dataset, including attachments with their own ACL
    or a curated parent binding. Restricted/non-published records require the
    separate scope-aware review flow; their prior body is left untouched.
    """
    retained=json.loads(previous['normalized_payload_json'] or '{}')
    provenance_refresh=(provenance_only and previous['status']=='provenance' and previous['source_type']=='official_discovery_parent'
                        and retained.get('provenance_only') is True and retained.get('answerable') is False
                        and incoming.get('provenance_only') is True and incoming.get('answerable') is False)
    if (previous['status'] not in {'active','updated'} or retained.get('provenance_only') is True or retained.get('answerable') is False) and not provenance_refresh:
        raise ValueError('inactive/non-answerable document requires explicit lifecycle review; no automatic reactivation')
    if retained.get('visibility','public')!='public':
        raise ValueError('restricted canonical refresh requires separately authorized scope review; prior body retained')
    if retained.get('parent_document_key') and retained['parent_document_key']!=incoming.get('parent_document_key'):
        raise ValueError('canonical parent binding changed; prior body retained for review')
    declared=dict(incoming)
    for field,pattern in (('academic_year',r'(20\d{2})\s*학년도'),('entry_year',r'(20\d{2})\s*학번'),('semester',r'([12])\s*학기')):
        match=re.search(pattern,incoming.get('title',''))
        if match:declared[field]=match[1]
    fields=('visibility','department','major','department_name','student_department','applicable_department','college_name','program_name','program_scope_text',
            'audience','campus_scope','entry_year','academic_year','semester','effective_from','effective_to','is_latest','curriculum_year','availability_status','offered_semesters',
            'parent_document_key','source_authority')
    for field in fields:
        if field not in retained:continue
        old,new=retained[field],declared.get(field)
        if field=='audience' and old in {'common','unknown',''} and new in {'undergraduate','graduate'}:continue
        if old not in (None,'','unknown') and new not in (None,'','unknown') and str(old)!=str(new):
            raise ValueError('changed explicit '+field+' requires scope review; prior canonical body retained')
        incoming[field]=old
        if field=='audience' and 'audience_evidence' in retained:incoming['audience_evidence']=retained['audience_evidence']


def import_collection(workspace: StagingWorkspace):
    workspace.validate()
    source = sqlite3.connect(workspace.checkpoint)
    source.row_factory = sqlite3.Row
    database = sqlite3.connect(workspace.database)
    database.row_factory = sqlite3.Row
    try:
        manifest_row = source.execute("SELECT value FROM metadata WHERE key='manifest_json'").fetchone()
        policy = Policy(json.loads(manifest_row[0]))
        rows = source.execute("SELECT f.*,r.final_url,r.fetched_at,r.raw_sha256,r.raw_path,r.extraction_json,r.manifest_hash "
                              "FROM frontier f JOIN receipts r USING(url) WHERE f.status='extracted' ORDER BY f.kind='attachment',f.url").fetchall()
        report = {"new": [], "updated": [], "unchanged": [], "failed": [], "provenance_parents":[], "skipped_states":
                  dict(source.execute("SELECT status,count(*) FROM frontier WHERE status!='extracted' GROUP BY status"))}
        with database:
            for row in rows:
                try:
                    policy.entry(row["url"])
                    entry = policy.entry(row["final_url"])
                    if row["manifest_hash"] != policy.hash:
                        historic=source.execute('SELECT manifest_json FROM policies WHERE hash=?',(row['manifest_hash'],)).fetchone()
                        if not historic or Policy(json.loads(historic[0])).hash!=row['manifest_hash']:
                            raise ValueError('receipt manifest mismatch')
                        historic_policy=Policy(json.loads(historic[0]));historic_policy.entry(row['url']);historic_policy.entry(row['final_url'])
                    from src.services.source_authority import requires_authority_review
                    if requires_authority_review(row['final_url'],entry):
                        raise ValueError('unreviewed user-content source authority')
                    raw_path = Path(row["raw_path"])
                    raw_root = workspace.directory / "raw"
                    if raw_root.is_symlink() or raw_root!=raw_root.resolve() or raw_path.is_symlink() or raw_path.resolve().parent != raw_root:
                        raise ValueError("raw receipt outside staging raw store")
                    if raw_path.stat().st_size > 20_000_000:
                        raise ValueError("raw receipt exceeds hard byte limit")
                    raw = raw_path.read_bytes()
                    if digest(raw) != row["raw_sha256"]:
                        raise ValueError("raw receipt hash mismatch")
                    extraction = json.loads(row["extraction_json"])
                    if extraction["status"] != "extracted" or not extraction.get("sections"):
                        raise ValueError("invalid extraction receipt")
                    if row["kind"] == "attachment":
                        parsed = extract_attachment_bounded(raw, extraction["title"])
                    else:
                        parsed = extract_html(raw, row["final_url"], selector=entry.get("selector"),
                                              expected_title=entry.get("expected_title", ""),
                                              body_markers=entry.get("body_markers", []))
                    if canonical_hash(asdict(parsed)) != canonical_hash(extraction):
                        raise ValueError("extraction receipt does not reproduce from raw response")
                    parent_key = row["parent_document_key"] or ""
                    if row["kind"] == "attachment" and not parent_key:
                        parents = database.execute("SELECT * FROM source_documents WHERE source_url=?",
                                                   (row["parent_url"],)).fetchall()
                        if len(parents)>1:
                            raise ValueError("attachment parent canonical identity unresolved")
                        if not parents or json.loads(parents[0]['normalized_payload_json'] or '{}').get('provenance_only'):
                            parent_key=_discovery_parent(workspace,source,database,policy,row['parent_url'],row['url'],report)
                        else:parent_key=parents[0]['document_key']
                    parent = None
                    if parent_key:
                        parent = database.execute("SELECT * FROM source_documents WHERE document_key=?", (parent_key,)).fetchone()
                        if not parent:
                            raise ValueError("parent inactive/missing")
                        parent_payload = json.loads(parent["normalized_payload_json"] or "{}")
                        provenance=parent['status']=='provenance' and parent['source_type']=='official_discovery_parent' and parent_payload.get('provenance_only') is True and parent_payload.get('answerable') is False
                        if parent['status'] not in {'active','updated'} and not provenance:raise ValueError('parent inactive/missing')
                        if parent_payload.get("visibility", "public") != "public":
                            raise ValueError("non-public parent requires separately authorized scope-aware enrichment")
                    match = re.fullmatch(r'/article/([^/]+)/detail/(\d+)',urlsplit(row['url']).path)
                    dataset = "attachments" if row["kind"] == "attachment" else "notices" if match else "guidance"
                    source_id = canonical_hash([parent_key, row["url"]]) if parent_key else canonical_hash(row["url"])
                    if match and dataset == 'notices':
                        host = urlsplit(row['url']).hostname
                        source_id = ('' if host == 'www.dongguk.edu' else host+':')+':'.join(match.groups())
                    key = dataset + ":" + source_id
                    normalized = {"title": extraction["title"], "content": "\n".join(s["text"] for s in extraction["sections"]),
                                  "sections": extraction["sections"], "links": extraction.get("links", []),
                                  "campus_scope": row["campus_scope"], "visibility": "public", "audience": "unknown",
                                  "source_owner": row["owner"], "source_url": row["url"],
                                  "discovery_url": row["parent_url"], "parent_document_key": parent_key,
                                  "parser_version": extraction["parser"], "raw_sha256": row["raw_sha256"]}
                    normalized['contacts'] = extraction.get('contacts',[])
                    normalized['attachments']=[{'name':link.get('label',''),'url':link['url'],'locator':link.get('locator','')} for link in extraction.get('links',[]) if link.get('kind')=='attachment']
                    normalized['images']=extraction.get('images',[])
                    normalized.update(extraction.get('metadata',{}))
                    from src.services.source_audience import source_degree_scope
                    degree=source_degree_scope(extraction['title'],extraction['sections'])
                    normalized['audience']=degree['audience'];normalized['audience_evidence']=degree
                    if parent:
                        for field in ("visibility", "department", "major", "department_name", "campus_scope", "audience", "entry_year", "academic_year",
                                      "semester", "effective_from", "effective_to"):
                            if field in parent_payload:
                                normalized[field] = parent_payload[field]
                    if not normalized["content"].strip():
                        raise ValueError("empty canonical extraction")
                    content_hash = canonical_hash(normalized, exclude_fields=("raw_sha256",))
                    previous = database.execute("SELECT * FROM source_documents WHERE document_key=?", (key,)).fetchone()
                    if previous:
                        _preserve_refresh_scope(previous,normalized)
                        content_hash = canonical_hash(normalized,exclude_fields=('raw_sha256',))
                    raw_payload = {"raw_sha256": row["raw_sha256"], "final_url": row["final_url"], "manifest_hash": row["manifest_hash"],
                                   "discovery_locator": row["locator"], "parser": extraction["parser"], "fetched_at": row["fetched_at"]}
                    if previous and previous["content_hash"] == content_hash:
                        report["unchanged"].append(key)
                        continue
                    if previous:
                        database.execute("UPDATE source_documents SET title=?,status='updated',content_hash=?,raw_payload_json=?,"
                                         "normalized_payload_json=?,collected_at=?,last_parsed_at=?,parse_error=NULL WHERE document_key=?",
                                         (extraction["title"], content_hash, canonical_json(raw_payload), canonical_json(normalized),
                                          row["fetched_at"], row["fetched_at"], key))
                        report["updated"].append(key)
                        if dataset=='notices':database.execute('UPDATE source_documents SET source_url=? WHERE document_key=?',(row['url'],key))
                    else:
                        database.execute("INSERT INTO source_documents(dataset,source_type,source_id,source_url,document_key,title,status,content_hash,"
                                         "schema_version,raw_payload_json,normalized_payload_json,collected_at,last_parsed_at,miss_count) "
                                         "VALUES(?,?,?,?,?,?,'active',?,1,?,?,?,?,0)",
                                         (dataset, "official_attachment" if parent_key else "official_cms_notice" if match else "official_public_page", source_id, row["url"], key,
                                          extraction["title"], content_hash, canonical_json(raw_payload), canonical_json(normalized), row["fetched_at"], row["fetched_at"]))
                        report["new"].append(key)
                    if normalized.get('published_at'):
                        database.execute('UPDATE source_documents SET published_at=? WHERE document_key=?',(normalized['published_at'],key))
                except (ValueError, KeyError, OSError) as exc:
                    report["failed"].append({"url": row["url"], "error": str(exc)})
        report["status"] = "partial" if report["failed"] or report["skipped_states"] else "success"
        # Absence/failed work never deletes, hides, or zeroes any existing source.
        return report
    finally:
        source.close()
        database.close()


def import_public_snapshot(workspace, snapshot, expected_sha256):
    """Explicit immutable public snapshot -> staging canonical rows, no overwrite.

    The caller attests public provenance and supplies the immutable file hash.
    This does not open a serving/default DB, copy indexes, or grant publication.
    """
    workspace.validate()
    from src.services.connected_knowledge import read_only
    snapshot=Path(snapshot).absolute()
    if snapshot != snapshot.resolve() or not snapshot.is_file() or snapshot.name=='rag_database.db':
        raise ValueError('snapshot must be an explicit immutable public copy, never rag_database.db')
    if snapshot.resolve() == workspace.database.resolve():
        raise ValueError('snapshot cannot be the staging target')
    import hashlib
    sha=hashlib.sha256()
    with snapshot.open('rb') as file:
        for block in iter(lambda:file.read(1024*1024),b''): sha.update(block)
    if sha.hexdigest()!=expected_sha256:
        raise ValueError('public snapshot SHA256 mismatch')
    report={'status':'success','snapshot_sha256':expected_sha256,'imported':0,'unchanged':0,'conflicts':[],'excluded':0}
    with read_only(snapshot) as source, sqlite3.connect(workspace.database) as destination:
        source.execute('BEGIN')
        target_columns={r[1] for r in destination.execute('PRAGMA table_info(source_documents)')}
        for record in source.execute('SELECT * FROM source_documents ORDER BY document_key'):
            row=dict(record)
            p=json.loads(row.get('normalized_payload_json') or '{}')
            if p.get('visibility','public')!='public' or row['status'] not in {'active','updated'}:
                report['excluded']+=1;continue
            if row['document_key']!=row['dataset']+':'+row['source_id']:
                raise ValueError('invalid canonical snapshot identity')
            previous=destination.execute('SELECT content_hash FROM source_documents WHERE document_key=?',(row['document_key'],)).fetchone()
            if previous:
                if previous[0]==row['content_hash']: report['unchanged']+=1
                else: report['conflicts'].append(row['document_key'])
                continue
            columns=sorted((set(row)&target_columns)-{'id'})
            destination.execute('INSERT INTO source_documents('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',[row[c] for c in columns])
            report['imported']+=1
    if report['conflicts']:report['status']='partial'
    with (workspace.directory/'snapshot-import-receipts.jsonl').open('a',encoding='utf-8') as file:
        file.write(canonical_json(report)+'\n')
    return report
