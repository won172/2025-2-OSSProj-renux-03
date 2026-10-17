"""Content-addressed local official snapshots; never fabricate a remote URL."""
from pathlib import Path
from functools import lru_cache
import hashlib,json
REGISTRY=Path(__file__).resolve().parents[2]/'data/official_guide_provenance.json'

@lru_cache(maxsize=32)
def _file_hash(path,inode,size,mtime):
    sha=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):sha.update(block)
    return sha.hexdigest()


def _records():return json.loads(REGISTRY.read_text())['sources']


def guide_snapshot(document):
    if document.get('dataset')!='rules' or document.get('source_type')!='entry_year_guide_pdf':return None
    p=json.loads(document.get('normalized_payload_json') or '{}');filename=p.get('source_file') or p.get('filename')
    for record in _records():
        if filename!=record['source_file']:continue
        file=REGISTRY.parent/filename
        if not file.is_file() or file.is_symlink():return None
        stat=file.stat()
        if _file_hash(str(file),stat.st_ino,stat.st_size,stat.st_mtime_ns)!=record['sha256']:return None
        if p.get('source_sha256') and p['source_sha256']!=record['sha256']:return None
        return {'kind':'tracked_official_pdf_snapshot','source_file':filename,'sha256':record['sha256'],
                'page_start':p.get('page_start'),'page_end':p.get('page_end'),'remote_url_status':'present' if document.get('source_url') else 'unresolved'}
    return None


def provenance_generation():
    states=[]
    for record in _records():
        file=REGISTRY.parent/record['source_file']
        if file.is_file():
            stat=file.stat();actual=_file_hash(str(file),stat.st_ino,stat.st_size,stat.st_mtime_ns)
        else:actual='missing'
        states.append([record['source_file'],record['sha256'],actual])
    return hashlib.sha256(json.dumps(states,sort_keys=True).encode()).hexdigest()
