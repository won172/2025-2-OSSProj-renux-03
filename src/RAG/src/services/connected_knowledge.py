"""Evidence-bound canonical document graph and read-only connected retrieval.

This projection supplements the original structured ontology. It is rebuilt
atomically from *all* canonical datasets, never from a partial crawl snapshot.
Graph paths navigate to original source text; path statements are not answer
facts. No model or external index is needed. Runtime opens SQLite mode=ro.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, replace
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
from urllib.parse import urlsplit
import time
import threading

from src.pipelines.canonical import canonical_hash, canonical_json
from src.services.campus_scope import classify_campus_scope
from src.services.ontology import (build_deterministic_projection, load_department_aliases,
                                   DETERMINISTIC_DATASETS, validate_projection)

SCHEMA_VERSION = 4
ALIAS_PATH = Path(__file__).resolve().parents[2] / "data/dongguk_department_aliases.csv"
ACTIVE = {"active", "updated"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS connected_manifest(id INTEGER PRIMARY KEY CHECK(id=1), revision TEXT,
 schema_version INTEGER, alias_hash TEXT, report_json TEXT);
CREATE TABLE IF NOT EXISTS connected_nodes(node_key TEXT PRIMARY KEY, kind TEXT, label TEXT,
 document_key TEXT, properties_json TEXT);
CREATE TABLE IF NOT EXISTS connected_edges(edge_key TEXT PRIMARY KEY, subject TEXT, predicate TEXT,
 object TEXT, document_key TEXT, source_url TEXT, locator TEXT, evidence_text TEXT,
 derivation TEXT, review_status TEXT, document_revision TEXT, graph_revision TEXT);
CREATE INDEX IF NOT EXISTS ix_connected_subject ON connected_edges(subject);
CREATE INDEX IF NOT EXISTS ix_connected_object ON connected_edges(object);
CREATE INDEX IF NOT EXISTS ix_connected_evidence ON connected_edges(document_key);
CREATE TABLE IF NOT EXISTS connected_chunks(chunk_id TEXT PRIMARY KEY, document_key TEXT,
 position INTEGER, locator TEXT, chunk_text TEXT, metadata_json TEXT, document_revision TEXT);
CREATE INDEX IF NOT EXISTS ix_connected_doc ON connected_chunks(document_key);
CREATE VIRTUAL TABLE IF NOT EXISTS connected_fts USING fts5(chunk_id UNINDEXED,document_key UNINDEXED, text, tokenize='trigram');
CREATE TABLE IF NOT EXISTS connected_epoch(id INTEGER PRIMARY KEY CHECK(id=1),canonical_epoch INTEGER,graph_epoch INTEGER);
INSERT OR IGNORE INTO connected_epoch VALUES(1,0,0);
"""
_cache_lock = threading.Lock()
_validated_cache = {}


def _install_epoch_triggers(db):
    for table in ('source_documents','connected_nodes','connected_edges','connected_chunks','connected_manifest','connected_fts',
                  'information_subjects','information_units','information_assertions','information_references','information_subject_links'):
        if table == 'connected_fts': continue  # virtual tables cannot own triggers; chunks are authoritative
        field = 'canonical_epoch' if table == 'source_documents' else 'graph_epoch'
        for action in ('INSERT','UPDATE','DELETE'):
            db.execute(f'CREATE TRIGGER IF NOT EXISTS ck_epoch_{table}_{action} AFTER {action} ON {table} BEGIN UPDATE connected_epoch SET {field}={field}+1 WHERE id=1; END')


def _signature(db, manifest, aliases_path):
    epoch = tuple(db.execute('SELECT canonical_epoch,graph_epoch FROM connected_epoch WHERE id=1').fetchone())
    file = db.execute('PRAGMA database_list').fetchone()[2]
    path=Path(file)
    identity=(str(path),path.stat().st_ino if path.exists() else id(db),db.execute('PRAGMA schema_version').fetchone()[0])
    # Relevant table triggers are the generation boundary. Query-log inserts
    # into the same DB/WAL do not change canonical or derived information.
    return (identity,epoch,manifest['revision'],manifest['schema_version'],alias_hash(aliases_path))


class _ReadOnlyConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()


def read_only(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5, factory=_ReadOnlyConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    started = time.monotonic()
    connection.set_progress_handler(lambda: int(time.monotonic()-started > 5), 10_000)
    return connection


def payload(document):
    if '_payload' in document: return document['_payload']
    try:
        value = json.loads(document["normalized_payload_json"] or "{}")
        result=value if isinstance(value, dict) else {}
        document['_payload']=result
        return result
    except (TypeError, ValueError):
        return {}


def load_documents(db):
    count, size = db.execute("SELECT count(*),sum(length(normalized_payload_json)) FROM source_documents").fetchone()
    if count > 100_000 or (size or 0) > 256_000_000:
        raise ValueError("connected corpus budget exceeded; partition before rollout")
    return [dict(row) for row in db.execute("SELECT * FROM source_documents ORDER BY document_key")]


def document_revision(document):
    if '_revision' in document: return document['_revision']
    # Recompute instead of trusting a missing/legacy hash. Visibility/status
    # participate, so a withdrawn document invalidates the old projection.
    revision=canonical_hash({k: document.get(k) for k in (
        "document_key", "dataset", "source_type", "source_url", "title", "status", "content_hash",
        "published_at", "collected_at", "raw_payload_json", "normalized_payload_json")})
    document['_revision']=revision
    return revision


def corpus_revision(documents):
    return canonical_hash([(d["document_key"], document_revision(d)) for d in documents])


def alias_hash(path=ALIAS_PATH):
    from src.services.information_graph import CONCEPT_PATH
    from src.services.official_provenance import REGISTRY,provenance_generation
    return hashlib.sha256((path.read_bytes() if path.exists() else b'')+CONCEPT_PATH.read_bytes()+REGISTRY.read_bytes()+provenance_generation().encode()+Path(__file__).with_name('information_graph.py').read_bytes()+Path(__file__).with_name('source_authority.py').read_bytes()+Path(__file__).with_name('source_audience.py').read_bytes()+Path(__file__).read_bytes()).hexdigest()


def projection_hash(db):
    """Certify derived contents too: missing/modified edges must fail closed."""
    digest = hashlib.sha256()
    for table, key in (("connected_nodes", "node_key"), ("connected_edges", "edge_key"),
                       ("connected_chunks", "chunk_id"),('connected_fts','chunk_id'),
                       ('information_subjects','subject_key'),('information_units','unit_key'),
                       ('information_assertions','assertion_key'),('information_references','reference_key'),('information_subject_links','link_key')):
        digest.update(table.encode())
        for row in db.execute(f"SELECT * FROM {table} ORDER BY {key}"):
            digest.update(canonical_json(list(row)).encode())
    return digest.hexdigest()


def doc_node(key):
    return "document:" + key


def has_source_url(document):
    try:
        parsed = urlsplit(document.get("source_url") or "")
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def text_sections(document):
    p = payload(document)
    sections = p.get("sections")
    if isinstance(sections, list):
        return [s for s in sections if isinstance(s, dict) and isinstance(s.get("text"), str)]
    text = p.get("content") or p.get("text") or p.get("menu") or p.get("메뉴") or ""
    if not text:
        text = "\n".join(f"{k}: {v}" for k, v in p.items() if isinstance(v, (str, int, float)) and k not in {
            "source_url", "url", "content_hash", "visibility", "document_key"})
    return [{"locator": "$.content" if "content" in p else "$.normalized_payload", "text": str(text)}]


def document_metadata(document):
    if '_metadata' in document: return dict(document['_metadata'])
    p = payload(document)
    result = {k: p[k] for k in (
        "campus_scope", "department", "department_name", "major", "visibility", "audience", "entry_year",
        "academic_year", "semester", "effective_from", "effective_to", "start_date", "end_date",
        "schedule_start", "schedule_end", "date", "meal_date", "deadline", "apply_deadline", "source_owner", "parent_document_key") if k in p}
    for field in ('is_latest','canonical_key','filename','college_name','category','topics','board_name','curriculum_year',
                  'availability_status','offered_semesters','effective_date','low_value','has_substantive_body'):
        if field in p: result[field]=p[field]
    result.update({"dataset": document["dataset"], "source_type": document["source_type"],
                   "document_key": document["document_key"], "doc_id": document["document_key"],
                   "title": document.get("title") or p.get("title", ""),
                   "url": document.get("source_url") or "", "source_url": document.get("source_url") or "",
                   "published_at": document.get("published_at") or "", "status": document["status"]})
    result["campus_scope"] = classify_campus_scope({**p, **result}).value
    result.setdefault("visibility", "public")
    from src.utils.audience import derive_audience
    if result['dataset'] in {'guidance','attachments','notices','rules','schedule'}:
        result.setdefault('audience','unknown')
    else:
        result.setdefault("audience", derive_audience(' '.join(str(p.get(k,'')) for k in ('title','department','category','content'))))
    if result["dataset"] == "meals":
        result["meal_date"] = result.get("meal_date") or result.get("date", "")
        upstream = p.get('upstream_published_at') or ''
        if not upstream and str(result['published_at'])[:10] != str(result['meal_date'])[:10]:
            upstream = result['published_at']
        result['published_at'] = upstream
        known_candidates=((upstream,'upstream_publication'),(p.get('source_fetched_at'),'source_response_receipt'),(p.get('known_at'),'explicit_capture_metadata'),(document.get('collected_at'),'canonical_collected_at'))
        result['evidence_known_at'],result['evidence_known_at_role']=next(((value,role) for value,role in known_candidates if value),('','unknown'))
        result['publication_field_status'] = 'upstream_explicit' if upstream else 'unknown_legacy_field_is_serving_date'
    if result["dataset"] == "schedule":
        result["schedule_start"] = result.get("schedule_start") or result.get("start_date") or p.get("start", "")
        result["schedule_end"] = result.get("schedule_end") or result.get("end_date") or p.get("end", "")
    if result['dataset']=='schedule':
        result['responsible_office']=p.get('department','')
        result['department_role']='responsible_office'
        result['major']=p.get('applicable_department','')
        result['student_department']=p.get('applicable_department','')
        result['calendar_container_year']=p.get('academic_year') or p.get('학년도','')
        explicit_year=re.search(r'(20\d{2})\s*학년도',result['title'])
        result['academic_year']=explicit_year.group(1) if explicit_year else result['calendar_container_year']
        explicit_semester=re.search(r'([12])\s*학기',result['title'])
        if explicit_semester:result['semester']=explicit_semester.group(1)
        result['audience']=p.get('audience') or derive_audience(result['title'])
        result['published_at']=p.get('upstream_published_at','')
        result['publication_field_status']='upstream_explicit' if result['published_at'] else 'unknown_legacy_field_is_event_start'
    if result['dataset'] in {'guidance','attachments','notices','rules'}:
        from src.services.source_audience import source_scope_from_payload
        degree=source_scope_from_payload(document,p)
        result['audience']=degree['audience'];result['audience_evidence']=degree
    result.setdefault("major", result.get("department_name", result.get("department", "")))
    if result['dataset']=='notices' and not result.get('apply_deadline'):
        from src.pipelines.notice_event_dates import extract_notice_event_dates
        event=extract_notice_event_dates(result['title'],p.get('content') or p.get('text') or '',result['published_at'])
        if event: result['apply_deadline']=event.end_date
    document['_metadata']=result
    return dict(result)


def _chunks(document):
    meta, position = document_metadata(document), 0
    for section in text_sections(document):
        text = section["text"]
        # Character cuts are locator-preserving, deterministic and bounded.
        for start in range(0, len(text), 900):
            body = text[start:start+1000]
            if not body.strip():
                continue
            locator = f"{section.get('locator', '$.content')}#chars={start}:{start+len(body)}"
            chunk_id = "connected:" + canonical_hash([document["document_key"], locator])[:32]
            yield (chunk_id, document["document_key"], position, locator, body,
                   canonical_json(meta), document_revision(document))
            position += 1


def rebuild_connected_projection(db, *, aliases_path=ALIAS_PATH):
    """Caller must supply a staging SQLite connection; one atomic derived replace.

    Original SourceDocument/Notice/Chunk and original ontology tables are never
    changed. On any failure the previous connected graph remains intact.
    """
    db.row_factory = sqlite3.Row
    documents = load_documents(db)
    revision = corpus_revision(documents)
    active = [d for d in documents if d["status"] in ACTIVE]
    metadata = {d["document_key"]: document_metadata(d) for d in active}
    nodes, edges, chunks, unresolved = {}, {}, [], []
    by_key = {d["document_key"]: d for d in active}
    by_url = defaultdict(list)
    for d in active:
        if d.get("source_url"):
            by_url[d["source_url"]].append(d["document_key"])
        nodes[doc_node(d["document_key"])] = ("SourceDocument", d.get("title") or d["document_key"], d["document_key"], metadata[d["document_key"]])
    # Reuse the ordinary current-version rules within campus/permission
    # partitions. A private/same-name cross-campus record cannot supersede a
    # public source through version annotation.
    import pandas as pd
    from src.services.notice_versioning import annotate_notice_versions
    from src.services.rule_versioning import annotate_rule_versions
    partitions=defaultdict(list)
    for d in active:
        m=metadata[d['document_key']]
        if d['dataset'] in {'notices','rules'}:
            partitions[(d['dataset'],m['campus_scope'],m['visibility'],m.get('department') if m['visibility']!='public' else '',m.get('entry_year') if d['source_type']=='entry_year_guide_pdf' else '')].append(d)
    for partition,rows in partitions.items():
        frame=pd.DataFrame([metadata[d['document_key']] for d in rows])
        annotated=(annotate_notice_versions if partition[0]=='notices' else annotate_rule_versions)(frame)
        for _,row in annotated.iterrows():
            key=row['document_key']
            for field in ('is_latest','canonical_key'):
                if field in row: metadata[key][field]=bool(row[field]) if field=='is_latest' else row[field]
            by_key[key]['_metadata']=metadata[key]
    for d in active: chunks.extend(_chunks(d))

    def node(key, kind, label, props=None):
        nodes.setdefault(key, (kind, label, "", props or {}))

    def edge(subject, predicate, object_, document, locator, text, derivation="explicit_metadata"):
        key = canonical_hash([subject, predicate, object_, document["document_key"], locator])
        edges[key] = (key, subject, predicate, object_, document["document_key"], document.get("source_url") or "",
                      locator, str(text), derivation, "deterministic", document_revision(document), revision)

    # Preserve existing structured semantics, independently per campus. Private
    # records never seed aliases/identity resolution for any public record.
    public = [d for d in active if metadata[d["document_key"]]["visibility"] == "public"]
    contact_records=defaultdict(list)
    for staff in public:
        if staff['dataset']!='staff': continue
        sp=payload(staff)
        path=str(sp.get('부서경로') or sp.get('department_path') or sp.get('organization_path') or sp.get('department') or sp.get('조직(트리)') or '')
        phone=re.sub(r'\D','',str(sp.get('전화번호') or sp.get('phone') or ''))
        contact_records[(metadata[staff['document_key']]['campus_scope'],path.split('>')[-1].strip(),phone)].append(staff['document_key'])
    aliases = load_department_aliases(aliases_path)
    organizations = defaultdict(lambda: defaultdict(set))
    for campus in sorted({metadata[d["document_key"]]["campus_scope"] for d in public}):
        scoped = [d for d in public if metadata[d["document_key"]]["campus_scope"] == campus]
        projection = build_deterministic_projection(
            [SimpleNamespace(**d) for d in scoped if d["dataset"] in DETERMINISTIC_DATASETS],
            department_aliases=aliases)
        errors = validate_projection(projection)
        if errors:
            raise ValueError("invalid domain projection: " + "; ".join(errors[:5]))
        prefix = "domain:" + campus + ":"
        for e in projection.entities.values():
            node(prefix+e.entity_key, e.entity_type.value, e.canonical_name, dict(e.properties))
            if e.entity_type.value in {"Department", "College", "OrganizationUnit"}:
                organizations[campus][e.canonical_name].add(prefix+e.entity_key)
        for a in projection.aliases.values():
            if prefix+a.entity_key in nodes:
                organizations[campus][a.alias].add(prefix+a.entity_key)
        for evidence in projection.evidence.values():
            relation = projection.relations[evidence.relation_key]
            d = by_key[evidence.document_key]
            if not has_source_url(d):
                continue  # retained legacy domain identity, unresolved factual provenance
            edge(prefix+relation.subject_key, relation.predicate.value, prefix+relation.object_key, d,
                 evidence.evidence_locator, evidence.evidence_text, "existing_deterministic_ontology")
            # Document -> domain entity preserves source-bound entities and
            # connects the actual source documents instead of just graph labels.
            for endpoint in (relation.subject_key, relation.object_key):
                edge(doc_node(d["document_key"]), "DESCRIBES", prefix+endpoint, d,
                     evidence.evidence_locator, evidence.evidence_text, "existing_deterministic_ontology")

    for d in active:
        key = d["document_key"]
        m, p, subject = metadata[key], payload(d), doc_node(key)
        campus = m["campus_scope"]
        partition = "public" if m["visibility"] == "public" else "isolated:" + key
        # Private/department nodes exist but never alter public identities.
        dataset_node = f"dataset:{partition}:{d['dataset']}"
        node(dataset_node, "Dataset", d["dataset"])
        edge(subject, "IN_DATASET", dataset_node, d, "$.dataset", d["dataset"], "canonical_membership")
        url = d.get("source_url") or ""
        parsed = urlsplit(url)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            site = f"website:{partition}:{parsed.scheme}://{parsed.netloc}"
            node(site, "Website", f"{parsed.scheme}://{parsed.netloc}")
            edge(subject, "ON_WEBSITE", site, d, "$.source_url", url, "navigation_only")
        else:
            unresolved.append({"document_key": key, "kind": "missing_source_url"})
        if campus != "unknown":
            campus_node = f"campus:{partition}:{campus}"
            node(campus_node, "Campus", campus)
            edge(subject, "HAS_CAMPUS_METADATA", campus_node, d, "$.campus_scope", campus, "metadata_scope_only")
        else:
            unresolved.append({"document_key": key, "kind": "unknown_campus"})
        for field in ("academic_year", "semester", "entry_year", "effective_from", "effective_to", "start_date", "end_date", "date"):
            if p.get(field):
                target = f"period:{partition}:{field}:{p[field]}"
                node(target, "PeriodMetadata", str(p[field]))
                edge(subject, "HAS_PERIOD_METADATA", target, d, "$."+field, p[field], "metadata_scope_only")
        for field in ("department_name", "department", "source_owner"):
            value = p.get(field)
            if not isinstance(value, str) or not value.strip():
                continue
            candidates = organizations[campus].get(value.strip(), set())
            if len(candidates) == 1:
                edge(subject, "ORGANIZATION_METADATA", next(iter(candidates)), d, "$."+field, value, "metadata_membership_only")
            else:
                # Unknown owners are distinct source-bound labels, not merged
                # by name or asserted as university organizational authority.
                target = "owner:" + canonical_hash([partition, campus, urlsplit(url).netloc, field, value])[:32]
                node(target, "UnresolvedOrganizationLabel", value)
                edge(subject, "HAS_OWNER_LABEL", target, d, "$."+field, value, "unresolved_metadata_only")
                unresolved.append({"document_key": key, "kind": "ambiguous_organization" if candidates else "unresolved_organization", "field": field, "value": value})
        for index, contact in enumerate(p.get('contacts',[]) if isinstance(p.get('contacts'),list) else []):
            office=contact.get('담당부서',{}).get('value','')
            phone=contact.get('연락처',{}).get('value','')
            locator=contact.get('담당부서',{}).get('locator',f'$.contacts[{index}]')
            if not office: continue
            candidates=organizations[campus].get(office,set())
            target=next(iter(candidates)) if len(candidates)==1 else 'responsible-office:'+canonical_hash([key,office])[:32]
            if len(candidates)!=1:
                node(target,'ResponsibleOffice',office,{'resolution':'ambiguous' if candidates else 'source_explicit','phone':phone})
            edge(subject,'RESPONSIBLE_OFFICE',target,d,locator,f'담당부서: {office}; 연락처: {phone}','explicit_page_contact')
            # A matching official phone plus office label is a navigation
            # bridge to an existing contact record, never a person-name merge
            # or a student's applicable-major declaration.
            normalized_phone=re.sub(r'\D','',phone)
            matches=contact_records.get((campus,office,normalized_phone),[]) if len(normalized_phone)>=8 else []
            if len(matches)<=10:
                for staff_key in matches:
                    edge(target,'CONTACT_REFERENCE',doc_node(staff_key),d,contact.get('연락처',{}).get('locator',locator),
                         f'공식 페이지 담당부서/전화와 기존 정본 연락처 일치: {office}; {phone}','explicit_contact_navigation')
            if not matches:
                unresolved.append({'document_key':key,'kind':'unresolved_contact_record','office':office})
        links = list(p.get("links", [])) if isinstance(p.get("links"), list) else []
        parent = p.get("parent_document_key")
        if parent:
            links.append({"document_key": parent, "locator": "$.parent_document_key", "parent": True})
        for index, link in enumerate(links):
            if not isinstance(link, dict):
                continue
            target_keys = [link["document_key"]] if link.get("document_key") else by_url.get(link.get("url", ""), [])
            target_keys=[target for target in target_keys if target in metadata and metadata[target]['visibility']==m['visibility'] and (m['visibility']=='public' or metadata[target].get('department')==m.get('department')) and metadata[target]['campus_scope']==campus]
            locator = link.get("locator") or f"$.links[{index}]"
            if len(target_keys) != 1 or target_keys[0] not in by_key:
                unresolved.append({"document_key": key, "kind": "unresolved_url" if not target_keys else "ambiguous_url", "url": link.get("url", "")})
                continue
            target_key = target_keys[0]
            target_meta = metadata[target_key]
            if (m["visibility"] != target_meta["visibility"] or
                (m["visibility"] != "public" and m.get("department") != target_meta.get("department")) or
                campus != target_meta["campus_scope"]):
                unresolved.append({"document_key": key, "kind": "cross_scope_link", "locator": locator})
                continue
            edge(subject, "ATTACHMENT_OF" if link.get("parent") else "LINKS_TO", doc_node(target_key), d,
                 locator, link.get("url") or target_key, "navigation_only")
    counts = Counter(d["dataset"] for d in active)
    from src.services.information_graph import SCHEMA as INFORMATION_SCHEMA,build_information_projection,persist_information_projection,validate_information_projection
    information=build_information_projection(active,metadata,text_sections,document_revision,revision)
    errors=validate_information_projection(information,active,text_sections,document_revision,revision)
    if errors:raise ValueError('invalid information projection: '+';'.join(errors[:5]))
    review_spans=defaultdict(list)
    for unit_key,row in information['units'].items():
        if json.loads(row[9]).get('layout_status'):
            review_spans[(row[2],row[4].rsplit('#chars=',1)[0])].append((row[6],row[7],unit_key))
    guarded_chunks={}
    for chunk in chunks:
        base,span=chunk[3].rsplit('#chars=',1);start,end=map(int,span.split(':'))
        overlaps=sorted((left,right,key) for left,right,key in review_spans.get((chunk[1],base),[]) if start<right and end>left)
        if not overlaps:
            guarded_chunks.setdefault(chunk[0],chunk)
            continue
        meta=json.loads(chunk[5]);meta['unreviewed_source_spans']=[key for _,_,key in overlaps]
        guarded_chunks.setdefault(chunk[0],(*chunk[:5],canonical_json(meta),chunk[6]))
        # Retain auditable review chunks, and index only exact safe intervals
        # around them as ordinary evidence. A neighboring table cannot erase
        # the HWP's valid five-preference/time paragraphs.
        intervals=[];cursor=start
        for left,right,_ in overlaps:
            if cursor<left:intervals.append((cursor,min(left,end)))
            cursor=max(cursor,right)
        if cursor<end:intervals.append((cursor,end))
        for left,right in intervals:
            text=chunk[4][left-start:right-start]
            if not text.strip():continue
            locator=base+f'#chars={left}:{right}'
            key='connected:'+canonical_hash([chunk[1],locator])[:32]
            safe_meta=json.loads(chunk[5]);safe_meta['source_review_boundary_split']=True
            guarded_chunks.setdefault(key,(key,chunk[1],chunk[2],locator,text,canonical_json(safe_meta),chunk[6]))
    chunks=list(guarded_chunks.values())
    semantic_keys={e[4] for e in edges.values() if e[8] in {'existing_deterministic_ontology','explicit_page_contact'}}
    linked_subjects = {e[1] for e in edges.values() if e[2] not in {
        "IN_DATASET", "ON_WEBSITE", "HAS_CAMPUS_METADATA", "HAS_PERIOD_METADATA", "HAS_OWNER_LABEL"}}
    report = {"revision": revision, "schema_version": SCHEMA_VERSION, "documents": len(active),
              "datasets": dict(counts), "nodes": len(nodes), "edges": len(edges), "chunks": len(chunks),
              "unresolved": unresolved, "unresolved_counts": dict(Counter(r["kind"] for r in unresolved)),
              "private_documents": sum(m["visibility"] != "public" for m in metadata.values()),
              "source_types": dict(Counter(d["source_type"] for d in active)),
              'information':information['report'],
              'audience_coverage':dict(Counter(m.get('audience','unknown') for m in metadata.values())),
              'audience_evidence_status':dict(Counter(m.get('audience_evidence',{}).get('status','legacy_dataset_scope') for m in metadata.values())),
              "semantic_documents":len(semantic_keys),
              "semantic_unlinked_documents":[d['document_key'] for d in active if d['document_key'] not in semantic_keys],
              "coverage_by_dataset":{dataset:{'canonical_documents':count,'document_nodes':count,
                  'semantic_documents':sum(d['dataset']==dataset and d['document_key'] in semantic_keys for d in active),
                  'missing_source_url':sum(d['dataset']==dataset and not has_source_url(d) for d in active),
                  'unknown_campus':sum(d['dataset']==dataset and metadata[d['document_key']]['campus_scope']=='unknown' for d in active)} for dataset,count in counts.items()},
              "unlinked_documents": [d["document_key"] for d in active if doc_node(d["document_key"]) not in linked_subjects]}
    db.executescript(SCHEMA)
    db.executescript(INFORMATION_SCHEMA)
    _install_epoch_triggers(db)
    with db:
        db.execute("DELETE FROM connected_edges")
        db.execute("DELETE FROM connected_nodes")
        db.execute("DELETE FROM connected_chunks")
        db.execute('DELETE FROM connected_fts')
        db.executemany("INSERT INTO connected_nodes VALUES(?,?,?,?,?)",
                       [(k, *v[:3], canonical_json(v[3])) for k, v in sorted(nodes.items())])
        db.executemany("INSERT INTO connected_edges VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", sorted(edges.values()))
        db.executemany("INSERT INTO connected_chunks VALUES(?,?,?,?,?,?,?)", chunks)
        db.executemany('INSERT INTO connected_fts VALUES(?,?,?)',[(r[0],r[1],json.loads(r[5]).get('title','')+' '+r[4]) for r in chunks])
        persist_information_projection(db,information)
        report["projection_hash"] = projection_hash(db)
        report['canonical_epoch']=db.execute('SELECT canonical_epoch FROM connected_epoch WHERE id=1').fetchone()[0]
        db.execute("INSERT OR REPLACE INTO connected_manifest VALUES(1,?,?,?,?)",
                   (revision, SCHEMA_VERSION, alias_hash(aliases_path), canonical_json(report)))
    return report


@dataclass(frozen=True)
class Scope:
    campus: str | None = None
    department: str | None = None
    audience: str = "common"
    entry_year: int | None = None
    academic_year: int | None = None
    semester: str | None = None
    as_of: date | None = None
    college: str | None = None
    program: str | None = None
    target_department: str | None = None
    target_college: str | None = None
    academic_years: tuple[int,...] = ()
    meal_start: date | None = None
    meal_end: date | None = None


def eligible(document, scope):
    from src.services.source_authority import requires_authority_review
    if requires_authority_review(document.get('source_url'),payload(document)):
        return False
    from src.services.official_provenance import guide_snapshot
    if document["status"] not in ACTIVE or not (has_source_url(document) or guide_snapshot(document)):
        return False
    m = document_metadata(document)
    if m.get('audience_evidence',{}).get('status')=='scope_conflict_requires_review':return False
    target_college=scope.target_college or scope.college
    if target_college and m.get('college_name') not in (None,'',target_college):return False
    if scope.academic_years and m.get('academic_year') not in (None,'') and str(m['academic_year']) not in {str(y) for y in scope.academic_years}:return False
    if document['dataset']=='meals':
        p=payload(document);menu=p.get('menu_text') or p.get('menu') or p.get('content');closed=p.get('is_closed') in (True,1,'1','true','True')
        if not menu and not closed:return False
    if document['dataset'] in {'meals','schedule'} and scope.as_of is None:
        return False
    if m.get('is_latest') in {False,0,'false','False','0'} and scope.academic_year is None and not scope.academic_years:
        return False
    visibility = m["visibility"]
    if visibility != "public" and not (visibility == "department" and scope.department and m.get("department") == scope.department):
        return False
    if m["campus_scope"] == "wise" or (scope.campus and m["campus_scope"] not in {scope.campus, "shared"}):
        return False
    # Explicit department/audience/year declarations are hard boundaries.
    department = m.get("student_department") if document["dataset"]=="schedule" else m.get("department") or m.get("department_name") or m.get("major")
    target_department=scope.target_department or (None if scope.target_college else scope.department)
    if target_department and department and department not in {target_department, "공통", "전체"}:
        return False
    if m["audience"] not in {"common", "unknown", "", scope.audience, "undergraduate" if scope.audience in {None,"","common"} else scope.audience}:
        return False
    for field in ("entry_year", "academic_year", "semester"):
        wanted, actual = getattr(scope, field), m.get(field)
        if wanted is not None and actual not in {None, "", str(wanted), wanted}:
            return False
    if scope.as_of:
        anchor = scope.as_of.isoformat()
        for field in ("effective_from", "effective_to"):
            value = m.get(field)
            if value:
                try:
                    value = date.fromisoformat(str(value)).isoformat()
                except ValueError:
                    return False
                if field == "effective_from" and value > anchor or field == "effective_to" and value < anchor:
                    return False
        # Meals are exact-date evidence, never timeless department neighbors.
        if document['dataset'] == 'meals':
            serving=str(m.get('meal_date') or m.get('date') or '')[:10]
            start=(scope.meal_start or scope.as_of).isoformat()
            end=(scope.meal_end or scope.meal_start or scope.as_of).isoformat()
            if not start <= serving <= end:
                return False
            if serving > anchor:
                known=str(m.get('evidence_known_at') or '')[:10]
                if not known or known > anchor:
                    return False
        if m.get("published_at") and str(m["published_at"])[:10] > anchor:
            return False
    return True


def terms(query):
    return set(re.findall(r"[A-Za-z0-9가-힣]{2,}", query.lower())) - {
        "알려줘", "알려주세요", "어디", "무엇", "관련", "안내", "방법", "방법과", "방법은",
        "문의", "부서", "담당", "담당부서", "연락처", "연락", "어떻게", "절차", "신청", "신청은", "오늘",'동국대학교'}


def relevance(query_terms, text):
    lowered = text.lower()
    return sum(t in lowered for t in query_terms) / max(len(query_terms), 1)


class ConnectedReader:
    def __init__(self, db, *, scope=Scope(), aliases_path=ALIAS_PATH):
        self.db, self.scope = db, scope
        manifest = db.execute("SELECT * FROM connected_manifest WHERE id=1").fetchone()
        if not manifest or manifest["schema_version"] != SCHEMA_VERSION:
            raise ValueError("missing/unsupported connected graph")
        signature = _signature(db,manifest,aliases_path)
        with _cache_lock:
            cached = _validated_cache.get(signature)
        if cached is None:
            documents = {d['document_key']:d for d in load_documents(db)}
            revision = corpus_revision(list(documents.values()))
            if manifest['revision'] != revision or manifest['alias_hash'] != alias_hash(aliases_path):
                raise ValueError('stale connected graph')
            report=json.loads(manifest['report_json'])
            if report.get('projection_hash') != projection_hash(db):
                raise ValueError('invalid connected projection checksum')
            cached=(documents,revision)
            with _cache_lock:
                if len(_validated_cache)>=2: _validated_cache.clear()
                _validated_cache[signature]=cached
        self.documents,self.revision=cached
        self._request_scope=scope
        self._resolved_query=None
        self.target_resolution={'status':'request_scope'}
        self._refresh_allowed()

    def _allowed_for_scope(self, scope):
        # Open a consistent read transaction in callers, so document status and
        # derived edges are never read from different committed generations.
        allowed = {k for k, d in self.documents.items() if eligible(d, scope)}
        for key in list(allowed):
            seen, current = set(), key
            while True:
                parent=payload(self.documents[current]).get('parent_document_key')
                if not parent: break
                if parent in seen or len(seen)>=8 or parent not in allowed:
                    allowed.discard(key)
                    break
                seen.add(parent)
                current=parent
        return allowed

    def _refresh_allowed(self):
        self.allowed=self._allowed_for_scope(self.scope)
        self.allowed_json=canonical_json(sorted(self.allowed))

    def resolve_query_scope(self, query):
        """Resolve public applicability without changing requester authority."""
        if self._resolved_query==query:return
        self._resolved_query=query
        query_scope=self._request_scope
        from src.services.source_audience import labelled_degree
        requested_degree=labelled_degree(query)
        if query_scope.audience in {None,'','common','unknown'} and requested_degree in {'undergraduate','graduate'}:query_scope=replace(query_scope,audience=requested_degree)
        years=tuple(sorted({int(y) for y in re.findall(r'(20\d{2})\s*(?:학년도|년도|년)',query)}))
        if query_scope.academic_year is None and not query_scope.academic_years and years:
            query_scope=replace(query_scope,academic_year=years[0] if len(years)==1 else None,academic_years=years if len(years)>1 else ())
        public_scope=replace(query_scope,department=None,target_department=None,target_college=None)
        prior_scope=self.scope
        reusable=self.target_resolution.get('status')!='ambiguous'
        public_keys=self.allowed if reusable and public_scope==prior_scope else self._allowed_for_scope(public_scope)
        departments=set();colleges=set();material=re.sub(r'\s+','',query)
        for key in public_keys:
            document=self.documents[key]
            meta=document_metadata(document)
            if meta.get('visibility')!='public':continue
            fields=('student_department',) if document['dataset']=='schedule' else ('department_name','major','department') if document['dataset']!='staff' else ()
            for field in fields:
                name=meta.get(field)
                if isinstance(name,str) and len(re.sub(r'\s+','',name))>=3 and re.sub(r'\s+','',name) in material:departments.add(name)
            college=meta.get('college_name')
            if college and re.sub(r'\s+','',college) in material:colleges.add(college)
        target=self._request_scope.target_department or (next(iter(departments)) if len(departments)==1 else None)
        college=self._request_scope.target_college or (next(iter(colleges)) if len(colleges)==1 else None)
        self.scope=replace(query_scope,target_department=target,target_college=college)
        self.target_resolution={'status':'resolved_public_target' if target or college else 'ambiguous' if len(departments)>1 or len(colleges)>1 else 'request_scope','department':target,'college':college,'candidates':sorted(departments) if len(departments)>1 else []}
        if reusable and self.scope==prior_scope:
            pass  # Same immutable read snapshot and applicability/authority.
        elif self.scope==public_scope:
            self.allowed=public_keys;self.allowed_json=canonical_json(sorted(public_keys))
        else:self._refresh_allowed()
        if self.target_resolution['status']=='ambiguous':self.allowed=set();self.allowed_json='[]'

    def inspect(self, seeds, *, max_hops=2, max_nodes=40, max_edges=80):
        if not (0 <= max_hops <= 4 and 1 <= max_nodes <= 100 and 1 <= max_edges <= 200):
            raise ValueError("graph inspection limits exceeded")
        queue, visited, paths, truncation = deque(), set(), [], set()
        for key in list(seeds)[:10]:
            if key in self.allowed:
                queue.append((doc_node(key), 0, []))
        # Website/dataset/date/campus membership is inspectable but terminal;
        # they never expand into every document on a large university hub.
        terminal = {"Website", "Dataset", "Campus", "PeriodMetadata", "UnresolvedOrganizationLabel"}
        while queue:
            key, depth, path = queue.popleft()
            if key in visited:
                continue
            if len(visited) >= max_nodes:
                truncation.add("nodes")
                break
            visited.add(key)
            if depth >= max_hops:
                continue
            rows = self.db.execute("SELECT * FROM connected_edges WHERE (subject=? OR object=?) AND document_key IN (SELECT value FROM json_each(?)) ORDER BY edge_key LIMIT ?",
                                   (key, key, self.allowed_json, max_edges+1)).fetchall()
            if len(rows) > max_edges:
                truncation.add("edges")
            for row in rows[:max_edges]:
                e = dict(row)
                if e["document_key"] not in self.allowed:
                    continue
                d = self.documents[e["document_key"]]
                if e["graph_revision"] != self.revision or e["document_revision"] != document_revision(d):
                    raise ValueError("invalid graph evidence revision")
                if e["review_status"] != "deterministic" or not e["locator"]:
                    continue
                target = e["object"] if key == e["subject"] else e["subject"]
                target_row = self.db.execute("SELECT kind,document_key FROM connected_nodes WHERE node_key=?", (target,)).fetchone()
                if not target_row or target_row["document_key"] and target_row["document_key"] not in self.allowed:
                    continue
                next_path = path + [e]
                paths.append({"node_key": target, "document_key": target_row["document_key"], "path": next_path})
                if len(paths) >= max_edges:
                    truncation.add("edges")
                    queue.clear()
                    break
                if target_row["kind"] not in terminal and target not in visited:
                    queue.append((target, depth+1, next_path))
        return {"revision": self.revision, "paths": paths, "visited_nodes": len(visited), "truncated": sorted(truncation)}

    def search(self, query, *, seeds=(), limit=8, connected=True):
        self.resolve_query_scope(query)
        if not 1 <= limit <= 20:
            raise ValueError("search limit exceeded")
        if connected:
            from src.services.information_graph import information_bundle,information_hits,QUERY_ROLES
            bundle=information_bundle(self,query)
            explicit_roles=any(re.search(pattern,query,re.I) for pattern in QUERY_ROLES.values())
            resolved=any(s['kind']!='unclassified_subject' for s in bundle['subjects'])
            if bundle['units'] or (resolved and explicit_roles):
                return {'status':'ok','hits':information_hits(self,bundle,limit=limit),
                        'information_bundle':bundle,'truncated':bundle['truncated'],'revision':self.revision}
        query_terms = terms(query)
        if not query_terms:
            return {"status": "ok", "hits": [], "truncated": [], "revision": self.revision}
        scored = []
        # Trigram LIKE is indexed even for Korean substring tokens. A bounded
        # candidate window prevents a comprehensive corpus from being scanned
        # and loaded on every query. Canonical epoch invalidates cached state.
        patterns=[t for t in sorted(query_terms) if len(t)>=3][:12]
        if patterns:
            sql='SELECT DISTINCT c.* FROM connected_fts f JOIN connected_chunks c ON c.chunk_id=f.chunk_id WHERE ('+ ' OR '.join('f.text LIKE ?' for _ in patterns)+') AND c.document_key IN (SELECT value FROM json_each(?)) ORDER BY c.chunk_id LIMIT 1001'
            candidates=self.db.execute(sql,tuple('%'+t+'%' for t in patterns)+(self.allowed_json,)).fetchall()
        else:
            candidates=self.db.execute('SELECT * FROM connected_chunks WHERE ('+ ' OR '.join('chunk_text LIKE ?' for _ in query_terms)+') AND document_key IN (SELECT value FROM json_each(?)) ORDER BY chunk_id LIMIT 1001',tuple('%'+t+'%' for t in sorted(query_terms))+(self.allowed_json,)).fetchall()
        scan_truncated=len(candidates)>1000
        review_omitted=False
        for row in candidates[:1000]:
            key = row["document_key"]
            if key not in self.allowed:
                continue
            metadata = json.loads(row["metadata_json"])
            if metadata.get('unreviewed_source_spans'):
                review_omitted=True
                continue
            score = relevance(query_terms, metadata.get("title", "") + " " + row["chunk_text"])
            if score >= 0.5:
                scored.append((score, dict(row), metadata))
        scored.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
        # Baseline top 3 lexical source documents + runtime baseline seeds.
        seed_keys = list(dict.fromkeys([k for k in seeds if k in self.allowed] + [r[1]["document_key"] for r in scored[:3]]))[:8]
        # Unclassified information remains searchable, but no shared office,
        # website or topic token is used to boost an unclassified document.
        traversal = {"paths": [], "truncated": []}
        paths_by_doc = {}
        for p in traversal["paths"]:
            documents_on_path={e['document_key'] for e in p['path']}
            if p['document_key']: documents_on_path.add(p['document_key'])
            # Self membership is not connected evidence. A useful path must
            # reach/support at least two different canonical source documents.
            if len(documents_on_path)<2: continue
            for key in documents_on_path:
                paths_by_doc.setdefault(key,p['path'])
        ranked = []
        for score, row, metadata in scored:
            path = paths_by_doc.get(row["document_key"], [])
            # Shared organization alone is insufficient: substantive query
            # terms outside department labels must still match source content.
            substantive = query_terms - terms(self.scope.department or "")
            if path and substantive and relevance(substantive, metadata.get("title", "") + " " + row["chunk_text"]) > 0:
                score += .12
            else:
                path = []
            ranked.append((score, row, metadata, path))
        ranked.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
        hits, seen = [], set()
        for score, row, metadata, path in ranked:
            if row["document_key"] in seen:
                continue
            seen.add(row["document_key"])
            base,span=row['locator'].rsplit('#chars=',1);left,right=map(int,span.split(':'))
            unit_rows=self.db.execute("SELECT * FROM information_units WHERE document_key=? AND substr(locator,1,instr(locator,'#chars=')-1)=? AND span_start<? AND span_end>? ORDER BY span_start,unit_key LIMIT 81",(row['document_key'],base,right,left)).fetchall()
            source_units={};audience_omitted=False
            for unit in unit_rows:
                qualifiers=json.loads(unit['qualifiers_json'])
                if qualifiers.get('layout_status'):continue
                if qualifiers.get('audience') not in (None,'','unknown','common',self.scope.audience,'undergraduate' if self.scope.audience in (None,'','common','unknown') else self.scope.audience):
                    audience_omitted=True
                    continue
                identity=(unit['locator'],unit['source_text'])
                source_units.setdefault(identity,{'unit_key':unit['unit_key'],'source_text':unit['source_text'],'locator':unit['locator'],'facets':[],'qualifiers':qualifiers})
            if audience_omitted and not source_units:continue
            metadata['information_evidence_units']=list(source_units.values()) or [{'unit_key':row['chunk_id'],'source_text':row['chunk_text'],'locator':row['locator'],'facets':[],'qualifiers':{}}]
            hits.append({**metadata, "chunk_id": row["chunk_id"], "chunk_text": row["chunk_text"],
                         "position": row["position"], "source_locator": row["locator"],
                         "hybrid_score": min(score, 1.0), "vector_score": 0.0, "sparse_score": score,
                         "connected_match": int(bool(path)), "connected_revision": self.revision,
                         "connected_path": path})
            if len(hits) >= limit:
                break
        return {"status": "ok", "hits": hits, "truncated": traversal["truncated"]+(['lexical_window'] if scan_truncated else [])+(['review_required_evidence_omitted'] if review_omitted else []), "revision": self.revision}


def connected_search(path, query, *, scope=Scope(), seeds=(), limit=8, connected=True):
    try:
        with read_only(path) as db:
            db.execute("BEGIN")
            return ConnectedReader(db, scope=scope).search(query, seeds=seeds, limit=limit, connected=connected)
    except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        return {"status": "fallback", "reason": f"{type(exc).__name__}: {exc}", "hits": [], "truncated": []}


def connected_provenance(path):
    """Cheap cache/evaluation identity; no document content or absolute paths."""
    try:
        with read_only(path) as db:
            db.execute('BEGIN')
            row=db.execute('SELECT * FROM connected_manifest WHERE id=1').fetchone()
            epoch=db.execute('SELECT canonical_epoch,graph_epoch FROM connected_epoch WHERE id=1').fetchone()
            report=json.loads(row['report_json'])
            return {'schema_version':row['schema_version'],'revision':row['revision'],'alias_hash':alias_hash(),
                    'projection_hash':report.get('projection_hash'),'canonical_epoch':epoch[0],'graph_epoch':epoch[1],
                    'state':'current' if row['schema_version']==SCHEMA_VERSION and row['alias_hash']==alias_hash() and report.get('canonical_epoch')==epoch[0] else 'stale'}
    except (sqlite3.Error,TypeError,ValueError,KeyError):
        return {'schema_version':SCHEMA_VERSION,'state':'unavailable','alias_hash':alias_hash()}
