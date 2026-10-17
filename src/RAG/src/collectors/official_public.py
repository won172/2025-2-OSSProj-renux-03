"""Resumable, budgeted collection. Discovery is evidence, never automatic trust.

Only exact manifest-approved HTTPS origins/path prefixes can be fetched. Every
hop is DNS checked and the socket is pinned to that public address (TLS SNI and
certificate verification still use the hostname). No cookies/proxies/login/JS.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import re
import socket
import sqlite3
import ssl
import time
import os
import tempfile
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode, unquote, quote

from src.collectors.official_extract import extract_html, extract_attachment_bounded, CollectionError

DIRECTORY = "https://www.dongguk.edu/campus/list"
SITEMAP = "https://www.dongguk.edu/sitemap/detail"
DENIED = re.compile(r"(?:login|logout|signin|sso|oauth|password|delete|remove|write|modify|admin|member/join)", re.I)
EXTENSIONS = {".pdf", ".hwp", ".hwpx", ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt"}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical_url(value):
    p = urlsplit(value)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise CollectionError("not a credential-free HTTP(S) URL")
    if p.port not in {None, 80 if p.scheme == "http" else 443}:
        raise CollectionError("nonstandard port")
    if any(c in value for c in "\\\r\n\x00") or re.search(r"%0[ad]", value, re.I) or re.search(r"%5c|%2f|%2e",p.path,re.I):
        raise CollectionError("ambiguous URL encoding")
    host = p.hostname.encode("idna").decode("ascii").lower()
    if host.endswith(".") or any(part in {".", ".."} for part in p.path.split("/")):
        raise CollectionError("ambiguous hostname/path")
    query = parse_qsl(p.query, keep_blank_values=True)
    if re.fullmatch(r'/article/[^/]+/detail/\d+',p.path):
        query=[(key,value) for key,value in query if key!='pageIndex']
    if p.path=='/cmmn/fileDown.do':
        for key in ('filename','filepath','filerealname'):
            values=[v for k,v in query if k==key]
            if len(values)>1:raise CollectionError('ambiguous download query')
            for item in values:
                decoded=unquote(item)
                if key=='filepath' and any(part in {'.','..'} for part in decoded.split('/')):raise CollectionError('download path traversal rejected')
                if key!='filepath' and ('/' in decoded or '\\' in decoded):raise CollectionError('download filename must be a basename')
    if len(query) > 30 or len(value) > 2048:
        raise CollectionError("URL budget exceeded")
    return urlunsplit((p.scheme.lower(), host, p.path or "/", urlencode(sorted(query)), ""))


def origin(url):
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


@dataclass(frozen=True)
class Budgets:
    pages: int = 100
    requests: int = 150
    total_bytes: int = 30_000_000
    response_bytes: int = 8_000_000
    depth: int = 3
    seconds: float = 300
    per_host_seconds: float = 1
    frontier: int = 5000
    redirects: int = 3
    request_seconds: float = 20

    def __post_init__(self):
        if not (1 <= self.pages <= 1000 and 1 <= self.requests <= 2000 and
                1 <= self.response_bytes <= 20_000_000 and 1 <= self.total_bytes <= 200_000_000 and
                0 <= self.depth <= 6 and 0 < self.seconds <= 3600 and
                0.1 <= self.per_host_seconds <= 60 and 1 <= self.frontier <= 20_000 and
                0 <= self.redirects <= 5 and 0 < self.request_seconds <= 60):
            raise CollectionError("invalid/excessive collection budgets")


class Policy:
    def __init__(self, manifest):
        if manifest.get("schema_version") != 1:
            raise CollectionError("unsupported inventory manifest")
        self.manifest = manifest
        self.approved = {}
        for entry in manifest["approved_sources"]:
            url = canonical_url(entry["url"])
            if urlsplit(url).scheme != "https" or not entry.get("verification_url") or not entry.get("owner"):
                raise CollectionError("approved sources require HTTPS, owner and verification provenance")
            self.approved[url] = entry
        self.hash = digest(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode())

    def entry(self, url):
        url = canonical_url(url)
        if urlsplit(url).scheme != "https" or DENIED.search(url):
            raise CollectionError("HTTP/login/mutation route rejected")
        matches = []
        for base, entry in self.approved.items():
            if url == base or (entry.get("allow_descendants") and origin(url) == origin(base)
                               and urlsplit(url).path.startswith(urlsplit(base).path.rstrip("/") + "/")):
                matches.append((len(base), entry))
        if not matches:
            raise CollectionError("source requires explicit allowlist review")
        return max(matches, key=lambda item: item[0])[1]


def public_addresses(host):
    addresses = sorted({info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    if not addresses:
        raise CollectionError("empty DNS response")
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        if not address.is_global or (address.version == 6 and address.ipv4_mapped):
            raise CollectionError("non-public DNS destination rejected")
    return addresses


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes


class Fetcher:
    def __init__(self, policy, budgets, *, transport=None, resolver=public_addresses,
                 clock=time.monotonic, sleep=time.sleep):
        self.policy, self.budgets = policy, budgets
        self.transport, self.resolver, self.clock, self.sleep = transport, resolver, clock, sleep
        self.started, self.last = clock(), {}
        self.requests, self.bytes = 0, 0

    def check_time(self):
        if self.clock() - self.started >= self.budgets.seconds:
            raise CollectionError("run elapsed-time budget exceeded")

    def fetch(self, url):
        trail = []
        for hop in range(self.budgets.redirects + 1):
            url = canonical_url(url)
            self.policy.entry(url)
            self.check_time()
            if url in trail or self.requests >= self.budgets.requests:
                raise CollectionError("redirect/request budget exceeded")
            trail.append(url)
            host = urlsplit(url).hostname
            addresses = self.resolver(host)
            # Validate injected resolvers too; no transport receives a private destination.
            if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
                raise CollectionError("non-public destination")
            delay = max(0, self.budgets.per_host_seconds - (self.clock() - self.last.get(host, -1e10)))
            self.sleep(delay)
            self.check_time()
            self.last[host] = self.clock()
            self.requests += 1
            remaining = min(self.budgets.response_bytes, self.budgets.total_bytes - self.bytes)
            if remaining <= 0:
                raise CollectionError("run byte budget exceeded")
            if self.transport:
                response = self.transport(url, addresses[0], remaining)
            else:
                connection = _PinnedHTTPS(host, addresses[0], min(self.budgets.request_seconds,
                                            max(.1, self.budgets.seconds - (self.clock() - self.started))))
                try:
                    path = urlsplit(url)
                    # Encode only the wire path. Keep canonical/discovery identities,
                    # existing escapes and the already encoded query unchanged.
                    if any(ord(c) < 32 or ord(c) == 127 for c in path.path):
                        raise CollectionError("control character in request path")
                    target_path = quote(path.path, safe="/%:@!$&'()*+,;=-._~")
                    connection.request("GET", urlunsplit(("", "", target_path, path.query, "")),
                                       headers={"User-Agent": "DongttokOfficialPublicCollector/1.0 (bounded candidate)",
                                                "Accept-Encoding": "identity", "Connection": "close"})
                    result = connection.getresponse()
                    headers = {k.lower(): v for k, v in result.getheaders()}
                    if headers.get("content-encoding", "identity").lower() != "identity":
                        raise CollectionError("compressed transfer rejected")
                    length = headers.get("content-length")
                    if length and (not length.isdigit() or int(length) > remaining):
                        raise CollectionError("response Content-Length budget exceeded")
                    chunks, size, began = [], 0, self.clock()
                    while True:
                        self.check_time()
                        if self.clock() - began > self.budgets.request_seconds:
                            raise CollectionError("response elapsed-time budget exceeded")
                        chunk = result.read(min(16384, remaining - size + 1))
                        if not chunk:
                            break
                        size += len(chunk)
                        self.bytes += len(chunk)
                        if size > remaining:
                            raise CollectionError("response/run byte budget exceeded")
                        chunks.append(chunk)
                    if length and size != int(length):
                        raise CollectionError("truncated response")
                    response = Response(result.status, headers, b"".join(chunks))
                finally:
                    connection.close()
            if self.transport:
                self.bytes += len(response.body)
            self.check_time()
            if len(response.body) > remaining:
                raise CollectionError("response/run byte budget exceeded")
            if response.status in {301, 302, 303, 307, 308}:
                if hop == self.budgets.redirects or not response.headers.get("location"):
                    raise CollectionError("redirect budget/missing location")
                url = urljoin(url, response.headers["location"])
                continue  # policy + DNS checked before every next hop
            return response, url, trail
        raise CollectionError("redirect budget exceeded")


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS frontier(url TEXT PRIMARY KEY, parent_url TEXT, depth INTEGER,
 owner TEXT, campus_scope TEXT, kind TEXT, filename TEXT, parent_document_key TEXT,
 status TEXT, attempts INTEGER DEFAULT 0, error TEXT, discovered_at TEXT, locator TEXT);
CREATE TABLE IF NOT EXISTS receipts(url TEXT PRIMARY KEY, final_url TEXT, fetched_at TEXT,
 http_status INTEGER, raw_sha256 TEXT, raw_path TEXT, bytes INTEGER, extraction_json TEXT,
 redirects_json TEXT, manifest_hash TEXT);
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, report_json TEXT);
CREATE TABLE IF NOT EXISTS discoveries(url TEXT, parent_url TEXT, locator TEXT, label TEXT,
 context TEXT, discovered_at TEXT, UNIQUE(url,parent_url,locator));
CREATE TABLE IF NOT EXISTS institutions(entry_key TEXT PRIMARY KEY, institution TEXT,
 directory_group TEXT, listed_url TEXT, canonical_url TEXT, campus_scope TEXT,
 discovery_url TEXT, locator TEXT, raw_sha256 TEXT, review_state TEXT);
CREATE TABLE IF NOT EXISTS policies(hash TEXT PRIMARY KEY,manifest_json TEXT);
CREATE TABLE IF NOT EXISTS receipt_history(id INTEGER PRIMARY KEY,url TEXT,final_url TEXT,
 fetched_at TEXT,http_status INTEGER,raw_sha256 TEXT,raw_path TEXT,bytes INTEGER,
 extraction_json TEXT,redirects_json TEXT,manifest_hash TEXT);
"""


class Collector:
    def __init__(self, checkpoint: Path, policy: Policy, budgets=Budgets(), *, fetcher=None):
        self.checkpoint = Path(checkpoint)
        self.checkpoint.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.checkpoint)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.policy, self.budgets = policy, budgets
        self.fetcher = fetcher or Fetcher(policy, budgets)
        old = self.db.execute("SELECT value FROM metadata WHERE key='manifest_hash'").fetchone()
        previous=self.db.execute("SELECT value FROM metadata WHERE key='manifest_json'").fetchone()
        if old and previous:
            self.db.execute('INSERT OR IGNORE INTO policies VALUES(?,?)',(old[0],previous[0]))
        if old and old[0] != policy.hash:
            # Policy upgrades retain the frontier, receipts and run history.
            # Re-evaluate unrequested candidates under the new explicit policy;
            # extracted bodies are refreshed explicitly, never silently reused
            # under a different parser/allowlist receipt.
            for row in self.db.execute("SELECT url,depth FROM frontier WHERE status IN ('requires_review','depth_limited','pending','deferred')").fetchall():
                try:
                    policy.entry(row['url'])
                    state = 'pending' if row['depth'] <= budgets.depth else 'depth_limited'
                except CollectionError:
                    state = 'requires_review'
                self.db.execute("UPDATE frontier SET status=? WHERE url=?",(state,row['url']))
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES('previous_manifest_hash',?)",(old[0],))
        self.db.execute("INSERT OR REPLACE INTO metadata VALUES('manifest_hash', ?)", (policy.hash,))
        self.db.execute("INSERT OR REPLACE INTO metadata VALUES('manifest_json', ?)", (json.dumps(policy.manifest),))
        self.db.execute('INSERT OR IGNORE INTO policies VALUES(?,?)',(policy.hash,json.dumps(policy.manifest)))
        self.db.commit()

    def enqueue(self, url, *, parent="", depth=0, owner="unknown", campus="unknown", kind="page",
                filename="", parent_document_key="", locator=""):
        try:
            url = canonical_url(url)
        except (CollectionError, ValueError):
            return
        if self.db.execute('SELECT 1 FROM frontier WHERE url=?',(url,)).fetchone():return
        try:
            entry = self.policy.entry(url)
            status = "pending" if depth <= self.budgets.depth else "depth_limited"
            owner = entry.get("owner", owner)
            campus = entry.get("campus_scope", campus)
        except CollectionError:
            status = "requires_review"
        if status == 'pending' and self.db.execute("SELECT count(*) FROM frontier WHERE status IN ('pending','fetching')").fetchone()[0] >= self.budgets.frontier:
            # The cap bounds active work, not historical corpus size. Preserve
            # every enqueue's metadata, including canonical attachment parent.
            status = 'deferred'
        self.db.execute("INSERT OR IGNORE INTO frontier VALUES(?,?,?,?,?,?,?,?,?,0,'',?,?)",
                        (url, parent, depth, owner, campus, kind, filename, parent_document_key, status, now(), locator))
        self.db.commit()

    def _admit_deferred(self):
        # Older checkpoints, policy approvals or a smaller resumed budget can
        # begin with too many pending rows. Keep their full metadata deferred.
        fetching=self.db.execute("SELECT count(*) FROM frontier WHERE status='fetching'").fetchone()[0]
        pending_capacity=max(0,self.budgets.frontier-fetching)
        self.db.execute("UPDATE frontier SET status='deferred' WHERE url IN (SELECT url FROM frontier WHERE status='pending' ORDER BY depth,discovered_at,url LIMIT -1 OFFSET ?)",(pending_capacity,))
        active = self.db.execute("SELECT count(*) FROM frontier WHERE status IN ('pending','fetching')").fetchone()[0]
        capacity = max(0, self.budgets.frontier - active)
        rows = self.db.execute("SELECT url,depth FROM frontier WHERE status='deferred' ORDER BY depth,discovered_at,url LIMIT ?", (capacity,)).fetchall()
        for row in rows:
            try:
                self.policy.entry(row['url'])
                state = 'pending' if row['depth'] <= self.budgets.depth else 'depth_limited'
            except CollectionError:
                state = 'requires_review'
            self.db.execute('UPDATE frontier SET status=? WHERE url=?', (state,row['url']))
        self.db.commit()

    def seed(self):
        self.enqueue(DIRECTORY, kind="directory")
        self.enqueue(SITEMAP, kind="sitemap")
        for entry in self.policy.manifest["approved_sources"]:
            if entry.get("seed", True):
                self.enqueue(entry["url"], owner=entry["owner"], campus=entry.get("campus_scope", "unknown"),
                             kind=entry.get("kind", "page"))

    def backfill_attachments(self, documents):
        """Reusable hook for canonical notices; identities/scope are never rewritten."""
        for document in documents:
            payload = json.loads(document.normalized_payload_json or "{}")
            if document.status not in {"active", "updated"} or payload.get("visibility", "public") != "public":
                continue
            attachments = payload.get("attachments", [])
            if isinstance(attachments, str):
                try:
                    attachments = json.loads(attachments)
                except ValueError:
                    continue
            for index, a in enumerate(attachments if isinstance(attachments, list) else []):
                if isinstance(a, dict) and a.get("url"):
                    self.enqueue(urljoin(document.source_url or "", a["url"]), parent=document.source_url or "",
                                 parent_document_key=document.document_key, kind="attachment", filename=a.get("name", ""),
                                 campus=payload.get("campus_scope", "unknown"), locator=f"$.attachments[{index}]")

    def run(self, *, retry_failed=False, refresh=False):
        self.frontier_truncated = False
        self.seed()
        for item in self.db.execute('SELECT i.* FROM institutions i LEFT JOIN frontier f ON f.url=i.canonical_url WHERE f.url IS NULL').fetchall():
            self.enqueue(item['canonical_url'],parent=item['discovery_url'],depth=0,owner=item['institution'],campus=item['campus_scope'],kind='institution',locator=item['locator'])
        for item in self.db.execute('SELECT d.*,f.depth AS parent_depth FROM discoveries d LEFT JOIN frontier q ON q.url=d.url LEFT JOIN frontier f ON f.url=d.parent_url WHERE q.url IS NULL ORDER BY d.discovered_at LIMIT 20000').fetchall():
            attachment=Path(urlsplit(item['url']).path).suffix.lower() in EXTENSIONS or '/cmmn/fileDown.do' in item['url']
            self.enqueue(item['url'],parent=item['parent_url'],depth=(item['parent_depth'] or 0)+1,owner=item['label'],
                         kind='attachment' if attachment else 'page',filename=item['label'] if attachment else '',locator=item['locator'])
        overflow=self.db.execute('SELECT count(*) FROM discoveries d LEFT JOIN frontier f ON f.url=d.url WHERE f.url IS NULL').fetchone()[0]
        self.db.execute("INSERT OR REPLACE INTO metadata VALUES('frontier_truncated',?)",('true' if overflow else 'false',))
        self.db.commit()
        self.db.execute("UPDATE frontier SET status='pending' WHERE status='fetching'")
        if refresh:
            self.db.execute("UPDATE frontier SET status='deferred' WHERE status IN ('extracted','discovery_only')")
        if retry_failed:
            self.db.execute("UPDATE frontier SET status='deferred' WHERE status IN ('failed','http_failed')")
        self.db.commit()
        run_id = self.db.execute("INSERT INTO runs(started_at) VALUES(?)", (now(),)).lastrowid
        self.db.commit()
        processed = 0
        while processed < self.budgets.pages:
            self._admit_deferred()
            row = self.db.execute("SELECT * FROM frontier WHERE status='pending' ORDER BY depth, discovered_at, url LIMIT 1").fetchone()
            if row is None:
                break
            try:
                self.fetcher.check_time()
            except CollectionError:
                break
            processed += 1
            url = row["url"]
            self.db.execute("UPDATE frontier SET status='fetching',attempts=attempts+1 WHERE url=?", (url,))
            self.db.commit()
            try:
                response, final, redirects = self.fetcher.fetch(url)
                raw_hash = digest(response.body)
                raw_path = self.checkpoint.parent / "raw" / raw_hash
                raw_path.parent.mkdir(exist_ok=True)
                if not raw_path.exists() or digest(raw_path.read_bytes()) != raw_hash:
                    with tempfile.NamedTemporaryFile(dir=raw_path.parent, delete=False) as file:
                        temporary = Path(file.name)
                        try:
                            file.write(response.body)
                            file.flush()
                            os.fsync(file.fileno())
                            os.replace(temporary, raw_path)
                        finally:
                            temporary.unlink(missing_ok=True)
                entry = self.policy.entry(final)
                if response.status != 200:
                    result = {"status": "http_failed", "error": f"HTTP {response.status}", "sections": [], "links": []}
                elif row["kind"] == "attachment":
                    filename = row["filename"] or urlsplit(final).path.rsplit("/", 1)[-1]
                    path_name = urlsplit(final).path.rsplit("/", 1)[-1]
                    if Path(filename).suffix.lower() not in EXTENSIONS and Path(path_name).suffix.lower() in EXTENSIONS:
                        filename = path_name
                    # Download endpoints may identify the format only via Content-Disposition.
                    if Path(filename).suffix.lower() not in EXTENSIONS:
                        m = re.search(r'filename="?([^";]+)', response.headers.get("content-disposition", ""))
                        if m:
                            filename = m.group(1)
                        m = re.search(r"filename\*=UTF-8''([^;]+)", response.headers.get("content-disposition", ""), re.I)
                        if m:
                            filename = unquote(m.group(1))
                    result = asdict(extract_attachment_bounded(response.body, filename))
                else:
                    mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if mime not in {"text/html", "application/xhtml+xml"}:
                        result = {"status": "unsupported", "error": f"unsupported page MIME: {mime}", "sections": [], "links": []}
                    else:
                        result = asdict(extract_html(response.body, final, selector=entry.get("selector"),
                                                   expected_title=entry.get("expected_title", ""),
                                                   body_markers=entry.get("body_markers", []),
                                                   discovery_only=row["kind"] in {"directory", "sitemap", "board"}))
                self.db.execute("INSERT OR REPLACE INTO receipts VALUES(?,?,?,?,?,?,?,?,?,?)",
                                (url, final, now(), response.status, raw_hash, str(raw_path.resolve()), len(response.body),
                                 json.dumps(result, ensure_ascii=False), json.dumps(redirects), self.policy.hash))
                self.db.execute('INSERT INTO receipt_history(url,final_url,fetched_at,http_status,raw_sha256,raw_path,bytes,extraction_json,redirects_json,manifest_hash) SELECT url,final_url,fetched_at,http_status,raw_sha256,raw_path,bytes,extraction_json,redirects_json,manifest_hash FROM receipts WHERE url=?',(url,))
                self.db.execute("UPDATE frontier SET status=?,error=? WHERE url=?",
                                (result["status"], result.get("error", ""), url))
                self.db.commit()
                for institution in result.get('institutions',[]):
                    listed = canonical_url(institution['listed_url'])
                    # HTTP discovery is retained verbatim; only a manifest-
                    # verified same-host HTTPS equivalent may be scheduled.
                    upgrade = self.policy.manifest.get('verified_upgrades',{}).get(listed,listed)
                    try:
                        self.policy.entry(upgrade)
                        review = 'approved_manifest'
                    except CollectionError:
                        review = 'requires_review'
                    self.db.execute("INSERT OR REPLACE INTO institutions VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (digest((url+institution['locator']).encode()),institution['institution'],institution['directory_group'],
                         institution['listed_url'],upgrade,institution['campus_scope'],url,institution['locator'],raw_hash,review))
                    self.enqueue(upgrade,parent=url,depth=0,owner=institution['institution'],
                                 campus=institution['campus_scope'],kind='institution',locator=institution['locator'])
                self.db.commit()
                if result["status"] in {"extracted", "discovery_only", "unsupported_dynamic", "unsupported_layout"}:
                    for link in result.get("discovery_links", result.get("links", [])) + result.get('links',[]):
                        target = link["url"]
                        try:
                            discovered_url = canonical_url(target)
                        except (CollectionError, ValueError):
                            continue
                        self.db.execute("INSERT OR IGNORE INTO discoveries VALUES(?,?,?,?,?,?)",
                                        (discovered_url, url, link["locator"], link["label"], link.get("context", ""), now()))
                        self.db.commit()
                        filename = urlsplit(target).path.rsplit("/", 1)[-1]
                        suffix = Path(filename).suffix.lower()
                        is_attachment = link.get('kind') == 'attachment' or suffix in EXTENSIONS or Path(link["label"]).suffix.lower() in EXTENSIONS
                        directory_owner = link["label"]
                        if directory_owner in {"홈페이지", "바로가기", "홈페이지 바로가기", "Homepage", "Website"}:
                            directory_owner = link.get("context") or "unknown"
                        self.enqueue(target, parent=url, depth=row["depth"]+(0 if link.get('kind')=='pagination' else 1),
                                     owner=directory_owner if row["kind"] == "directory" else row["owner"],
                                     campus="unknown" if row["kind"] == "directory" else row["campus_scope"],
                                     kind="attachment" if is_attachment else "page",
                                     filename=link["label"] if is_attachment else "", locator=link["locator"])
            except Exception as exc:
                self.db.execute("UPDATE frontier SET status='failed',error=? WHERE url=?", (f"{type(exc).__name__}: {str(exc)[:250]}", url))
                self.db.commit()
                if self.fetcher.requests >= self.budgets.requests or self.fetcher.bytes >= self.budgets.total_bytes:
                    break
        self._admit_deferred()
        self.frontier_truncated = bool(self.db.execute("SELECT 1 FROM frontier WHERE status='deferred'").fetchone()) or bool(overflow)
        self.db.execute("INSERT OR REPLACE INTO metadata VALUES('frontier_truncated',?)", ('true' if self.frontier_truncated else 'false',))
        self.db.commit()
        report = self.report()
        report.update({"processed_this_run": processed, "requests": self.fetcher.requests,
                       "bytes": self.fetcher.bytes, "budgets": asdict(self.budgets), "frontier_truncated": self.frontier_truncated or bool(self.db.execute("SELECT 1 FROM metadata WHERE key='frontier_truncated' AND value='true'").fetchone())})
        self.db.execute("UPDATE runs SET finished_at=?,report_json=? WHERE id=?", (now(), json.dumps(report), run_id))
        self.db.commit()
        return report

    def report(self):
        states = dict(self.db.execute("SELECT status,count(*) FROM frontier GROUP BY status"))
        sources = []
        for r in self.db.execute("SELECT f.*,r.http_status,r.raw_sha256,r.bytes FROM frontier f LEFT JOIN receipts r USING(url) ORDER BY f.url"):
            item = dict(r)
            item["discovery_provenance"] = [dict(d) for d in self.db.execute("SELECT parent_url,locator,label,context FROM discoveries WHERE url=?", (r["url"],))]
            item["campus_verification"] = "unknown" if r["campus_scope"] == "unknown" else "manifest_verified"
            item["access_status"] = "not_requested" if r["http_status"] is None else f"http_{r['http_status']}"
            item["parser_support"] = r["status"] if r["status"] in {"extracted", "unsupported", "unsupported_dynamic", "ocr_required", "failed"} else "not_extracted"
            sources.append(item)
        return {"status": "partial" if any(k not in {"extracted", "discovery_only"} for k in states) else "bounded_complete",
                "completeness": "frontier only; homepage visits do not prove institution coverage", "states": states,
                "sources": sources, "institution_inventory": [dict(r) for r in self.db.execute('SELECT * FROM institutions ORDER BY entry_key')],
                "manifest_hash": self.policy.hash}

    def close(self):
        self.db.close()
