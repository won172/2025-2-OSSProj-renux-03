"""Bounded, model-free official body/attachment extraction.

Table rendering adapted from the unmerged 70c6a20 guidance candidate. All
locators refer to original sections/pages, never inferred factual claims.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import PurePosixPath
import re
import struct
import zipfile
import zlib
import json
import subprocess
import sys
from xml.etree import ElementTree as ET
from urllib.parse import urljoin, urlsplit, urlencode
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

MAX_TABLE_CELLS = 10_000
MAX_DOM_DEPTH = 100
MAX_CHARS = 1_000_000
PARSER_VERSION = "official-public-v5"

class CollectionError(ValueError):
    pass

def normalized(text):
    return " ".join(str(text).split())

@dataclass
class Extraction:
    status: str
    title: str = ""
    sections: list[dict] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    parser: str = PARSER_VERSION
    error: str = ""
    discovery_links: list[dict] = field(default_factory=list)
    institutions: list[dict] = field(default_factory=list)
    contacts: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    images: list[dict] = field(default_factory=list)

    @property
    def text(self):
        return "\n".join(s["text"] for s in self.sections)

def validate_dom_depth(content: Tag) -> None:
    """Check the complete body iteratively before any recursive rendering."""
    stack = [(content, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_DOM_DEPTH:
            raise CollectionError(f"DOM depth budget exceeded (max {MAX_DOM_DEPTH})")
        stack.extend((child, depth + 1) for child in node.children if isinstance(child, Tag))


def render_table(table: Tag) -> str:
    """Expand merged cells into a labelled grid; keep multi-level headers in order."""
    if len(table.find_parents("table")) >= 3:
        raise CollectionError("table nesting budget exceeded")
    grid = {}
    rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
    captions = table.find_all("caption", recursive=False)
    lines = ["[표]" + (" " + normalized(captions[0].get_text(" ", strip=True)) if captions else "")]
    for row_index, row in enumerate(rows):
        column = 0
        for cell in row.find_all(["th", "td"], recursive=False):
            while (row_index, column) in grid:
                column += 1
            try:
                rowspan = int(cell.get("rowspan", 1))
                colspan = int(cell.get("colspan", 1))
            except (ValueError, TypeError) as exc:
                raise CollectionError("invalid table span") from exc
            if not 1 <= rowspan <= len(rows) - row_index or not 1 <= colspan <= 100:
                raise CollectionError("table span budget/structure invalid")
            text = " / ".join(line for line in render_node(cell).splitlines() if line.strip())
            for r in range(row_index, row_index + rowspan):
                for c in range(column, column + colspan):
                    if (r, c) in grid or len(grid) >= MAX_TABLE_CELLS:
                        raise CollectionError("overlapping table cells or table budget exceeded")
                    grid[r, c] = text
            column += colspan
    if not grid:
        raise CollectionError("empty table")
    width = max(c for _, c in grid) + 1
    lines.append(" | ".join(f"열{c + 1}" for c in range(width)))
    for r in range(len(rows)):
        lines.append(" | ".join(grid.get((r, c), "") for c in range(width)))
    lines.append("[/표]")
    return "\n" + "\n".join(lines) + "\n"


def render_node(node) -> str:
    if isinstance(node, Comment):
        return ""
    if isinstance(node, NavigableString):
        return re.sub(r"\s+", " ", str(node))
    if not isinstance(node, Tag):
        return ""
    if node.name == "table":
        return render_table(node)
    if node.name == "br":
        return "\n"
    text = "".join(render_node(child) for child in node.children)
    if node.name in {"h2", "h3", "h4", "h5", "h6"}:
        return f"\n{'#' * int(node.name[1])} {normalized(text)}\n"
    if node.name in {"p", "div", "ul", "ol", "li", "dl", "dt", "dd", "section"}:
        return "\n" + text + "\n"
    return " " + text + " "



def _quality(result: Extraction, minimum=40):
    text = result.text
    if len(text) > MAX_CHARS:
        return Extraction("failed", error="extracted character budget exceeded")
    if len(re.sub(r"[^0-9A-Za-z가-힣]", "", text)) < minimum:
        result.status = "ocr_required" if result.parser == "pdf" else "failed"
        result.error = "insufficient textual evidence"
    return result


def extract_links(container, url, prefix):
    links = []
    for index, a in enumerate(container.select("a[href]")[:5000]):
        href = a.get("href", "").strip()
        locator = f"{prefix}/a[href][{index}]"
        download = re.fullmatch(r"(?:javascript:)?\s*downGO\('([^']+)','([^']+)','([^']+)'\);?", href)
        if download:
            name, path, stored = download.groups()
            # Values are data, never JavaScript execution. Per-hop policy is
            # still enforced when this exact official download URL is fetched.
            target = urljoin(url, "/cmmn/fileDown.do") + "?" + urlencode({"filename": name, "filepath": path, "filerealname": stored})
            links.append({"url": target, "label": name, "locator": locator,
                          "kind": "attachment", "context": "official downGO file reference"})
        elif href and not href.startswith("#") and urlsplit(href).scheme in {"", "http", "https"}:
            links.append({"url": urljoin(url, href), "label": normalized(a.get_text(" ", strip=True)),
                          "locator": locator, "context": normalized(a.parent.get_text(" ", strip=True))[:300],
                          'kind':'pagination' if a.find_parent(class_='pagenation') else 'attachment' if '/cmmn/fileDown.do' in urljoin(url,href) else 'link'})
    return links


def parse_directory(soup, url):
    entries = []
    for group_index, item in enumerate(soup.select("#campus_list .item")):
        heading = item.select_one("h2.cont_tit")
        for index, a in enumerate(item.select("ul.link_group a[href]")):
            entries.append({"institution": normalized(a.get_text(" ", strip=True)),
                            "directory_group": normalized(heading.get_text(" ", strip=True)) if heading else "unknown",
                            "listed_url": urljoin(url, a["href"]), "campus_scope": "unknown",
                            "discovered_from": url,
                            "locator": f"#campus_list .item[{group_index}]/ul.link_group/a[{index}]"})
    return entries


def extract_html(raw: bytes, url: str, *, selector: str | None = None,
                 expected_title: str = "", body_markers=(), discovery_only=False) -> Extraction:
    soup = BeautifulSoup(raw, "html.parser")
    title = normalized(soup.title.get_text(" ", strip=True)) if soup.title else ""
    if not title or re.search(r"로그인|접근.?거부|access denied|not found|오류.*발생", title, re.I):
        return Extraction("failed", error="missing title/login/error response")
    if expected_title and expected_title not in title:
        return Extraction("failed", error="verified title mismatch")
    from src.services.source_authority import requires_authority_review
    if requires_authority_review(url):
        # Preserve the response receipt/raw body, not personal question text or
        # links in an active official-information extraction/frontier.
        return Extraction('requires_authority_review',title=title,
                          metadata={'source_authority':'unreviewed_user_content'},
                          error='official hosting does not establish authority of user questions')
    discovery_links = extract_links(soup, url, "document/discovery")
    if discovery_only:
        institutions = parse_directory(soup, url) if urlsplit(url).path == "/campus/list" else []
        return Extraction("discovery_only", title=title, discovery_links=discovery_links, institutions=institutions)
    if re.fullmatch(r'/article/[^/]+/list',urlsplit(url).path):
        return Extraction('discovery_only',title=title,discovery_links=discovery_links)
    containers = soup.select(selector) if selector else soup.select(
        ".board_view div.view_cont, #content_focus.contents > .inner, #sub_container #contents, .board_view .view_con, .board_view .view_content, "
        ".sub_content .contents, .sub_contents .contents, .contents .cont_group, .sub_cont, "
        "article, main, [role=main]")
    # Prefer the most specific official container over broad nested main/article.
    if selector and len(containers) != 1:
        return Extraction("unsupported_layout", title=title, discovery_links=discovery_links, error="configured body container missing/ambiguous")
    if not containers:
        homepage = urlsplit(url).path.rstrip("/") in {"", "/main", "/index.do", "/index", "/main.do"}
        return Extraction("discovery_only" if homepage and discovery_links else "unsupported_layout", title=title,
                          discovery_links=discovery_links, error="navigation observed; substantive body requires subpage/adapter")
    body = soup.select_one('.board_view .view_cont') or containers[0]
    images=[{'url':urljoin(url,img.get('src','')),'alt':img.get('alt',''),'locator':f'body/img[{index}]','status':'ocr_required'} for index,img in enumerate(body.select('img[src]'))]
    contacts = []
    for index, contact in enumerate(soup.select("div.page_contact")):
        fields = {}
        for li_index, li in enumerate(contact.select("ul > li")):
            label, value = li.select_one("span"), li.select_one("p")
            if label and value:
                fields[normalized(label.get_text()).strip()] = {"value": normalized(value.get_text(" ", strip=True)),
                    "locator": f"div.page_contact[{index}]/ul/li[{li_index}]/p"}
        if fields:
            contacts.append(fields)
    # Only anchors in the substantive body (plus the explicit notice file
    # region) enter canonical links; global navigation never becomes evidence.
    links = []
    board = soup.select_one("div.board_view")
    metadata = {}
    if board:
        title_tag = board.select_one("div.tit > p")
        if title_tag:
            title = normalized(title_tag.get_text(" ", strip=True))
        files = board.select_one("div.view_files")
        if files:
            links.extend(extract_links(files, url, "div.board_view/div.view_files"))
        info = board.select_one("div.tit > div.info")
        if info:
            match = re.search(r"등록일\s*(20\d{2})[.\-/](\d{2})[.\-/](\d{2})", info.get_text(" ", strip=True))
            if match:
                metadata["published_at"] = "-".join(match.groups())
    if body.select('input[type=password]'):
        return Extraction("failed", error="login form")
    for element in body.select("script, style, noscript, iframe, svg, button, nav, footer, header, "
                               "form, .depth4, .section_tit, .page_contact, [role=navigation]"):
        element.decompose()
    links.extend(extract_links(body,url,'body'))
    try:
        validate_dom_depth(body)
        text = "\n".join(normalized(line) for line in render_node(body).splitlines() if normalized(line))
    except (CollectionError, RecursionError) as exc:
        return Extraction("failed", error=str(exc))
    if any(marker not in text for marker in body_markers):
        return Extraction("failed", title=title, error="verified body marker missing")
    if any(marker in text for marker in ("로그인이 필요합니다", "권한이 없습니다", "요청하신 페이지를 찾을 수", "접근 권한이 필요")):
        return Extraction("failed", title=title, error="access/error body rejected")
    # Retain each section's heading and ordinal, including tables in-place.
    sections, current, heading = [], [], title
    for line in text.splitlines():
        if line.startswith("##") and current:
            sections.append({"locator": f"body/section/{len(sections)+1}", "heading": heading, "text": "\n".join(current)})
            current = []
            heading = line.lstrip("# ")
        current.append(line)
    if current:
        sections.append({"locator": f"body/section/{len(sections)+1}", "heading": heading, "text": "\n".join(current)})
    result = _quality(Extraction("extracted", title, sections, links), minimum=60)
    if result.status != 'extracted':
        if images:result.status='ocr_required';result.images=images;result.discovery_links=discovery_links
        return result
    for contact in contacts:
        sections.append({"locator": "div.page_contact", "heading": "담당부서/연락처",
                         "text": "; ".join(f"{k}: {v['value']}" for k,v in contact.items())})
    result.sections=sections
    result.contacts, result.discovery_links, result.metadata = contacts, discovery_links, metadata
    result.images=images
    if images:result.metadata['image_evidence_status']='ocr_required_partial'
    return result


def _zip_parts(raw):
    archive = zipfile.ZipFile(BytesIO(raw))
    infos = archive.infolist()
    if len(infos) > 2000 or sum(i.file_size for i in infos) > 40_000_000:
        raise CollectionError("ZIP expanded-size/member budget exceeded")
    if any(i.flag_bits & 1 or i.file_size > 10_000_000 or
           (i.file_size > 100_000 and i.file_size / max(i.compress_size, 1) > 200) for i in infos):
        raise CollectionError("ZIP encrypted/oversized member")
    return archive


def extract_attachment(raw: bytes, filename: str) -> Extraction:
    ext = PurePosixPath(filename.lower()).suffix
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(raw))
            if reader.is_encrypted:
                return Extraction("unsupported", parser="pdf", error="encrypted PDF")
            if len(reader.pages) > 300:
                return Extraction("unsupported", parser="pdf", error="PDF page budget exceeded")
            sections = [{"locator": f"page/{n}", "text": page.extract_text() or ""}
                        for n, page in enumerate(reader.pages, 1)]
            return _quality(Extraction("extracted", filename, sections, parser="pdf"))
        if ext in {".docx", ".pptx", ".xlsx", ".hwpx"}:
            archive = _zip_parts(raw)
            with archive:
                if ext == ".xlsx":
                    from openpyxl import load_workbook
                    workbook = load_workbook(BytesIO(raw), read_only=True, data_only=False, keep_links=False)
                    sections = []
                    try:
                        cells = 0
                        for sheet in workbook:
                            rows = []
                            for n, row in enumerate(sheet.iter_rows(values_only=True), 1):
                                cells += len(row)
                                if cells > 100_000:
                                    raise CollectionError("spreadsheet cell budget exceeded")
                                # Formula source is retained as text, never evaluated.
                                rows.append(f"row/{n}: " + " | ".join(str(v) if v is not None else "" for v in row))
                            sections.append({"locator": f"sheet/{sheet.title}", "text": "\n".join(rows)})
                    finally:
                        workbook.close()
                else:
                    patterns = {".docx": r"word/document\.xml", ".pptx": r"ppt/slides/slide[0-9]+\.xml",
                                ".hwpx": r"Contents/section[0-9]+\.xml"}
                    parts = sorted(n for n in archive.namelist() if re.fullmatch(patterns[ext], n))
                    sections = []
                    for part in parts:
                        xml = archive.read(part)
                        if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                            raise CollectionError("XML entities rejected")
                        root = ET.fromstring(xml)
                        paragraphs = [" ".join(e.itertext()).strip() for e in root.iter()
                                      if e.tag.rsplit("}", 1)[-1] in {"p", "tr"}]
                        # Paragraphs in table rows are kept with row locators in XML part.
                        sections.append({"locator": part, "text": "\n".join(p for p in paragraphs if p)})
            return _quality(Extraction("extracted", filename, sections, parser=ext[1:]))
        if ext == ".hwp":
            import olefile
            with olefile.OleFileIO(BytesIO(raw)) as ole:
                header = ole.openstream("FileHeader").read()
                if not header.startswith(b"HWP Document File") or len(header) < 40:
                    raise CollectionError("invalid HWP header")
                flags = struct.unpack_from("<I", header, 36)[0]
                if flags & 6:
                    return Extraction("unsupported", parser="hwp", error="encrypted/distribution HWP")
                sections = []
                for name in sorted(ole.listdir()):
                    if len(name) != 2 or name[0] != "BodyText" or not name[1].startswith("Section"):
                        continue
                    data = ole.openstream(name).read()
                    if flags & 1:
                        decoder = zlib.decompressobj(-15)
                        data = decoder.decompress(data, 10_000_001)
                        if len(data) > 10_000_000 or decoder.unconsumed_tail or not decoder.eof:
                            raise CollectionError("HWP decompression budget/stream invalid")
                    offset, lines = 0, []
                    while offset + 4 <= len(data):
                        record = struct.unpack_from("<I", data, offset)[0]
                        offset += 4
                        tag, size = record & 0x3ff, record >> 20
                        if size == 0xfff:
                            size = struct.unpack_from("<I", data, offset)[0]
                            offset += 4
                        if offset + size > len(data):
                            raise CollectionError("truncated HWP record")
                        if tag == 67:
                            text = data[offset:offset+size].decode("utf-16le", errors="strict")
                            # Extended controls contain binary parameters; reject them instead of inventing text.
                            text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f].{0,7}", " ", text)
                            lines.append(text)
                        offset += size
                    sections.append({"locator": "/".join(name), "text": "\n".join(lines)})
            return _quality(Extraction("extracted", filename, sections, parser="hwp"))
        return Extraction("unsupported", parser=ext or "unknown", error="unsupported attachment format (legacy Office/images require adapter/OCR)")
    except ImportError as exc:
        return Extraction("unsupported", parser=ext, error=f"parser unavailable: {exc.name}")
    except Exception as exc:
        return Extraction("failed", parser=ext, error=f"{type(exc).__name__}: {str(exc)[:200]}")


def extract_attachment_bounded(raw: bytes, filename: str) -> Extraction:
    """Isolate hostile PDF/Office decompression and parser hangs from the job."""
    if len(raw) > 20_000_000:
        return Extraction("failed", error="attachment byte budget exceeded")
    try:
        result = subprocess.run([sys.executable, __file__, "--worker", filename], input=raw,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20, check=False)
        if result.returncode or len(result.stdout) > 8_000_000:
            return Extraction("failed", error="parser worker failed/resource limit exceeded")
        return Extraction(**json.loads(result.stdout))
    except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
        return Extraction("failed", error=f"parser worker: {type(exc).__name__}")


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--worker":
    from dataclasses import asdict
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
        resource.setrlimit(resource.RLIMIT_AS, (768_000_000, 768_000_000))
    except (ImportError, ValueError, OSError):
        pass  # Wall-clock, raw/expanded-byte/page/cell limits remain portable.
    raw = sys.stdin.buffer.read(20_000_001)
    output = extract_attachment(raw, sys.argv[2]) if len(raw) <= 20_000_000 else Extraction("failed", error="byte budget")
    print(json.dumps(asdict(output), ensure_ascii=False))
