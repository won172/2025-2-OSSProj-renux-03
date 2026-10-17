"""Wire-level URL and source-field state regressions, without network or models."""
from io import BytesIO
import json
from types import SimpleNamespace
import ssl
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from src.collectors import official_public as public
from src.services.information_graph import information_unit_body
from tests.test_connected_official_knowledge import workspace, add_documents, build
from tests.test_connected_information_integration import runtime, endpoint
from tests.test_information_graph import bundle


HOST = 'https://www.dongguk.edu'
UNICODE_PATH = '/resources/files/2026 우아대.pdf'
MIXED = '신청 대상:\n제출 서류:\n신청 방법: nDRIMS에서 신청한다.\n단, 승인 전에는 신청을 완료한 것으로 보지 않는다.'
UNFILLED = '[원문 미입력 필드: 신청 대상, 제출 서류 — 값이 기재되지 않아 해당 정보의 근거가 아님]'


def policy(*urls, descendants=False):
    return public.Policy({'schema_version': 1, 'approved_sources': [
        {'url': url, 'owner': '동국대학교', 'verification_url': public.DIRECTORY,
         'allow_descendants': descendants} for url in urls]})


@pytest.fixture
def wire(monkeypatch):
    """Keep real HTTPConnection.request/getresponse and pinned connect/TLS calls.

    Only the TCP socket and TLS wrapper are fakes; http.client must validate,
    encode and serialize the outgoing request itself.
    """
    state = SimpleNamespace(responses=[], sockets=[], addresses=[], sni=[])

    class Socket:
        def __init__(self, response):
            self.response = response
            self.sent = bytearray()
            self.closed = False

        def sendall(self, data):
            self.sent.extend(data)

        def makefile(self, *args):
            return BytesIO(self.response)

        def close(self):
            self.closed = True

    def connect(address, timeout):
        state.addresses.append((address, timeout))
        sock = Socket(state.responses.pop(0))
        state.sockets.append(sock)
        return sock

    class TLS:
        verify_mode = ssl.CERT_REQUIRED
        check_hostname = True

        def wrap_socket(self, sock, *, server_hostname):
            state.sni.append(server_hostname)
            return sock

    monkeypatch.setattr(public.socket, 'create_connection', connect)
    monkeypatch.setattr(public.ssl, 'create_default_context', TLS)
    state.fetcher = lambda p, **kw: public.Fetcher(p, public.Budgets(**kw),
        resolver=lambda host: ['93.184.216.34'], sleep=lambda delay: None)
    return state


def response(body=b'official bytes', status='200 OK', headers=()):
    return (f'HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\n' +
            ''.join(f'{k}: {v}\r\n' for k, v in headers) + '\r\n').encode() + body


def test_unicode_target_is_encoded_by_actual_http_request(wire):
    original = HOST + UNICODE_PATH
    wire.responses.append(response())
    result, final, trail = wire.fetcher(policy(original)).fetch(original + '#원문')
    assert bytes(wire.sockets[0].sent).split(b'\r\n')[0] == b'GET /resources/files/2026%20%EC%9A%B0%EC%95%84%EB%8C%80.pdf HTTP/1.1'
    assert result.body == b'official bytes'
    assert final == original and trail == [original]
    assert wire.addresses[0][0] == ('93.184.216.34', 443)
    assert wire.sni == ['www.dongguk.edu'] and wire.sockets[0].closed


def test_mixed_field_body_explicitly_marks_only_unfilled_fields():
    body = information_unit_body({'source_text': MIXED})
    assert body.startswith(UNFILLED + '\n')
    assert body.endswith(MIXED)


@pytest.mark.parametrize('path', [
    '/resources/files/2026 Dongguk International Summer School Brochure.pdf',
    '/public/2026학년도 신입생을 위한 학사제도 및 학업이수가이드_20260921.pdf',
    '/resources/files/2025-1575 정보통신접근성 품질인증서-동국대학교.pdf',
    '/resources/site/mecha/resource/files/학과_소개_자료.pdf',
    '/resources/files/2026%20%EC%9A%B0%EC%95%84%EB%8C%80.pdf',
    '/resources/files/plain-ASCII_2026(2).pdf',
    '/cmmn/fileDown.do?filename=전공 결정.hwp&filepath=/official/files&filerealname=원본.hwp',
])
def test_observed_path_shapes_preserve_source_identity_and_query(wire, path):
    original = HOST + path
    canonical = public.canonical_url(original)
    wire.responses.append(response())
    result, final, trail = wire.fetcher(policy(original)).fetch(original + '#section')
    request = bytes(wire.sockets[0].sent).split(b'\r\n')[0].decode('ascii')
    target = request.split(' ')[1]
    assert ' ' not in target and '#' not in target
    assert unquote(urlsplit(target).path) == unquote(urlsplit(canonical).path)
    assert parse_qs(urlsplit(target).query) == parse_qs(urlsplit(canonical).query)
    assert '%2520' not in target and '%25EC' not in target
    assert final == canonical and trail == [canonical]
    assert public.digest(result.body) == public.digest(b'official bytes')
    assert b'Host: www.dongguk.edu\r\n' in wire.sockets[0].sent
    if 'plain-ASCII' in path:
        assert target == path


def test_authorized_redirect_reencodes_each_wire_target_without_identity_changes(wire):
    first = HOST + '/resources/files/start.pdf'
    last = 'https://mba.dongguk.edu/resources/files/모집 요강.pdf'
    # A Location header is an ISO-8859-1 HTTP field; the Korean path is escaped
    # on the wire, and its literal space still needs request-target encoding.
    location = 'https://mba.dongguk.edu/resources/files/%EB%AA%A8%EC%A7%91 %EC%9A%94%EA%B0%95.pdf'
    wire.responses.extend([response(b'', '302 Found', [('Location', location)]), response()])
    f = wire.fetcher(policy(first, last, 'https://mba.dongguk.edu/resources/files', descendants=True))
    _, final, trail = f.fetch(first)
    assert final == location and trail == [first, location]
    assert b'%91%20%EC' in wire.sockets[1].sent and b'%25EB' not in wire.sockets[1].sent
    assert wire.sni == ['www.dongguk.edu', 'mba.dongguk.edu']
    assert f.requests == 2 and all(s.closed for s in wire.sockets)


@pytest.mark.parametrize('path', [
    '/outside/file.pdf', '/resources/files/login', '/resources/files/delete?id=1',
    '/resources/files/../file.pdf', '/resources/files/a%2fb.pdf',
    '/resources/files/%2e%2e/file.pdf', '/resources/files/a%5cb.pdf',
    '/resources/files/a\\b.pdf', '/resources/files/a%0db.pdf',
    '/resources/files/a%0ab.pdf', '/resources/files/a\x01b.pdf',
    '/resources/files/a\x7fb.pdf',
])
def test_unsafe_targets_never_reach_socket(wire, path):
    with pytest.raises(public.CollectionError):
        wire.fetcher(policy(HOST + '/resources/files', descendants=True)).fetch(HOST + path)
    assert not wire.sockets


@pytest.mark.parametrize('destination', [
    'https://unapproved.example/resources/files/a.pdf',
    HOST + '/unapproved.pdf', HOST + '/resources/files/login',
    HOST + '/resources/files/a%2fb.pdf', 'http://www.dongguk.edu/resources/files/a.pdf',
])
def test_unsafe_redirect_never_opens_destination_socket(wire, destination):
    first = HOST + UNICODE_PATH
    wire.responses.append(response(b'', '302 Found', [('Location', destination)]))
    with pytest.raises(public.CollectionError):
        wire.fetcher(policy(HOST + '/resources/files', descendants=True)).fetch(first)
    assert len(wire.sockets) == 1 and wire.sockets[0].closed


def test_dns_is_rechecked_after_redirect_and_private_address_never_connects(wire):
    first = HOST + UNICODE_PATH
    destination = 'https://mba.dongguk.edu/resources/files/second.pdf'
    wire.responses.append(response(b'', '302 Found', [('Location', destination)]))
    f = wire.fetcher(policy(first, destination))
    hosts = []
    def resolve(host):
        hosts.append(host)
        return ['93.184.216.34'] if len(hosts) == 1 else ['127.0.0.1']
    f.resolver = resolve
    with pytest.raises(public.CollectionError, match='non-public'):
        f.fetch(first)
    assert hosts == ['www.dongguk.edu', 'mba.dongguk.edu'] and len(wire.sockets) == 1


def test_unapproved_origin_never_resolves_or_connects(wire):
    f = wire.fetcher(policy(HOST + UNICODE_PATH))
    f.resolver = lambda host: pytest.fail('unapproved origin reached DNS')
    with pytest.raises(public.CollectionError, match='allowlist'):
        f.fetch('https://unapproved.example/resources/files/file.pdf')
    assert not wire.sockets


def test_tls_verification_failure_closes_pinned_socket(wire, monkeypatch):
    class RejectTLS:
        verify_mode = ssl.CERT_REQUIRED
        check_hostname = True
        def wrap_socket(self, sock, *, server_hostname):
            assert server_hostname == 'www.dongguk.edu'
            raise ssl.SSLCertVerificationError('fixture certificate mismatch')
    monkeypatch.setattr(public.ssl, 'create_default_context', RejectTLS)
    wire.responses.append(response())
    with pytest.raises(ssl.SSLCertVerificationError):
        wire.fetcher(policy(HOST + UNICODE_PATH)).fetch(HOST + UNICODE_PATH)
    assert wire.sockets[0].closed and not wire.sockets[0].sent


def test_elapsed_budget_prevents_request_construction(wire):
    f = wire.fetcher(policy(HOST + UNICODE_PATH), seconds=1)
    f.clock = lambda: f.started + 2
    with pytest.raises(public.CollectionError, match='elapsed-time'):
        f.fetch(HOST + UNICODE_PATH)
    assert not wire.sockets


@pytest.mark.parametrize('limits', [{'response_bytes': 3}, {'total_bytes': 3}])
def test_wire_byte_budget_closes_connection(wire, limits):
    original = HOST + UNICODE_PATH
    wire.responses.append(response(b'1234'))
    with pytest.raises(public.CollectionError, match='budget'):
        wire.fetcher(policy(original), **limits).fetch(original)
    assert wire.sockets[0].closed


def test_request_budget_and_pacing_are_preserved(wire):
    first = HOST + UNICODE_PATH
    last = HOST + '/resources/files/next.pdf'
    wire.responses.append(response(b'', '302 Found', [('Location', last)]))
    f = wire.fetcher(policy(first, last), requests=1)
    with pytest.raises(public.CollectionError, match='budget'):
        f.fetch(first)
    assert len(wire.sockets) == f.requests == 1
    wire.responses.extend([response(), response()])
    f = wire.fetcher(policy(first), per_host_seconds=1)
    f.clock = lambda: 100
    f.started = 100
    delays = []
    f.sleep = delays.append
    f.fetch(first); f.fetch(first)
    assert delays == [0, 1]


def test_wire_collection_keeps_canonical_checkpoint_and_raw_history(wire, tmp_path):
    original = HOST + '/page/공식 안내'
    p = policy(original)
    raw = ('<html><title>휴학 안내</title><main><h2>신청 방법</h2><p>' +
           '공식 신청 절차의 조건을 모두 확인하고 포털에서 신청한다. ' * 8 + '</p></main></html>').encode()
    wire.responses.extend([response(raw, headers=[('Content-Type', 'text/html')])] * 2)
    f = wire.fetcher(p)
    c = public.Collector(tmp_path / 'checkpoint.sqlite', p, fetcher=f)
    try:
        c.run(); c.run(refresh=True)
        row = c.db.execute('SELECT * FROM receipts WHERE url=?', (original,)).fetchone()
        assert row['final_url'] == original and row['raw_sha256'] == public.digest(raw)
        assert json.loads(row['redirects_json']) == [original]
        assert public.Path(row['raw_path']).read_bytes() == raw
        history = c.db.execute('SELECT url,final_url,raw_sha256 FROM receipt_history').fetchall()
        assert len(history) == 2 and all(tuple(r) == (original, original, public.digest(raw)) for r in history)
        assert c.db.execute('SELECT count(*) FROM frontier WHERE url=?', (original,)).fetchone()[0] == 1
    finally:
        c.close()


@pytest.mark.parametrize('value', [
    '신청 대상: 재학생', '신청 대상:\n재학생', '제출 서류: 재학증명서',
    '신청 방법:\nnDRIMS에서 신청한다.', '신청 기간: 미정',
    '신청 기간:\n추후 안내 예정', '수수료: 0', '수수료:\n0',
])
def test_populated_zero_and_stated_unknown_are_not_unfilled(value):
    body = information_unit_body({'source_text': value})
    assert body.endswith(value) and '미입력 필드' not in body
    if '미정' in value or '추후 안내' in value:
        assert '[원문 미정/추후안내 필드: 신청 기간 — 학교가 미정/추후안내 상태로 명시함]' in body
    else:
        assert body == value


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['ask', 'stream'])
async def test_mixed_unit_keeps_conditions_and_missing_state_through_selector_and_final(workspace, runtime, mode):
    add_documents(workspace, [('guidance', 'mixed', {'title': '휴학 안내', 'text': MIXED})])
    build(workspace)
    query = '휴학 신청 대상과 제출 서류와 신청 방법을 알려줘'
    result = bundle(workspace, query)
    assert 'procedure' in result['available_facets']
    assert {'eligibility', 'documents'} <= set(result['missing_facets'])
    assert not {'eligibility', 'documents'} & set(result['available_facets'])
    assert any(u['source_text'] == MIXED for u in result['units'])
    answer = await endpoint(query, mode)
    assert answer['verification_status'] == 'passed' and len(answer['sources']) == 1
    assert runtime.selector and runtime.contexts and runtime.grounding
    selected = '\n'.join(row['text'] for rows in runtime.selector for row in rows)
    context = runtime.contexts[-1]['context']
    for text in (selected, context):
        assert UNFILLED in text and MIXED in text
        assert text.index(UNFILLED) < text.index(MIXED)
        assert '단, 승인 전에는 신청을 완료한 것으로 보지 않는다.' in text


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['ask', 'stream'])
@pytest.mark.parametrize('field,facet', [
    ('신청 대상:\n재학생', 'eligibility'),
    ('신청 기간:\n추후 안내 예정', 'period'),
    ('수수료:\n0', 'amount'),
])
async def test_mixed_filled_and_declared_unknown_values_survive_transports(workspace, runtime, mode, field, facet):
    source = '제출 서류:\n' + field + '\n신청 방법: nDRIMS에서 신청한다.\n단, 별도 승인이 필요하다.'
    add_documents(workspace, [('guidance', 'values', {'title': '휴학 안내', 'text': source})])
    build(workspace)
    query = '휴학 신청 대상 서류 기간 수수료 신청 방법'
    result = bundle(workspace, query)
    assert {facet, 'procedure'} <= set(result['available_facets'])
    assert 'documents' in result['missing_facets']
    answer = await endpoint(query, mode)
    assert answer['verification_status'] == 'passed'
    for text in ('\n'.join(row['text'] for rows in runtime.selector for row in rows), runtime.contexts[-1]['context']):
        assert '[원문 미입력 필드: 제출 서류 — 값이 기재되지 않아 해당 정보의 근거가 아님]' in text
        assert source in text
        if facet == 'period':
            # A mixed unit has both a known procedure and an unknown period;
            # the annotation describes that field, not the whole unit.
            assert '[원문 미정/추후안내 필드: 신청 기간 — 학교가 미정/추후안내 상태로 명시함]' in text
