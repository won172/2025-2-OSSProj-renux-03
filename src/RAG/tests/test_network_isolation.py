"""The suite-wide network guard must fail before an external request can start."""
from __future__ import annotations

import asyncio
from io import BytesIO
import socket
from urllib import request as urllib_request
from urllib.response import addinfourl

import httpx
import pytest
import requests


def test_socket_guard_rejects_dns_and_connection_paths():
    with pytest.raises(pytest.fail.Exception, match="outbound network access"):
        socket.getaddrinfo("example.test", 443)
    with pytest.raises(pytest.fail.Exception, match="outbound network access"):
        socket.create_connection(("203.0.113.1", 443))
    with socket.socket() as client:
        with pytest.raises(pytest.fail.Exception, match="outbound network access"):
            client.connect(("203.0.113.1", 443))
        with pytest.raises(pytest.fail.Exception, match="outbound network access"):
            client.connect_ex(("203.0.113.1", 443))


def test_http_guards_reject_external_url_before_transport():
    with pytest.raises(pytest.fail.Exception, match="outbound network access"):
        requests.get("https://example.test/")
    with pytest.raises(pytest.fail.Exception, match="outbound network access"):
        httpx.get("https://example.test/")
    with pytest.raises(pytest.fail.Exception, match="outbound network access"):
        asyncio.run(_request_external_url())
    with pytest.raises(pytest.fail.Exception, match="outbound network access"):
        urllib_request.urlopen("https://example.test/")
    with pytest.raises(pytest.fail.Exception, match="outbound network access"):
        urllib_request.urlopen(urllib_request.Request("https://example.test/"))


async def _request_external_url():
    async with httpx.AsyncClient() as client:
        await client.get("https://example.test/")


def test_loopback_resolution_remains_available():
    assert socket.getaddrinfo("127.0.0.1", 0)
    assert socket.getaddrinfo("::1", 0)


def test_urllib_loopback_destination_is_allowed(monkeypatch):
    response = addinfourl(BytesIO(b"local"), {}, "http://127.0.0.1/test", code=200)
    response.msg = "OK"

    class LocalHandler(urllib_request.BaseHandler):
        handler_order = 100

        def http_open(self, request):
            assert request.full_url == "http://127.0.0.1/test"
            return response

    opener = urllib_request.build_opener(urllib_request.ProxyHandler({}), LocalHandler())
    monkeypatch.setattr(urllib_request, "_opener", opener)
    assert urllib_request.urlopen("http://127.0.0.1/test") is response


def test_http_guards_check_destination_even_with_loopback_proxy(monkeypatch):
    # urlopen caches an opener with the proxy settings; restore it with the env.
    monkeypatch.setattr(urllib_request, "_opener", None)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:8765")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8765")
    url = "https://example.test/"

    with pytest.raises(pytest.fail.Exception, match="example.test"):
        requests.get(url)
    with pytest.raises(pytest.fail.Exception, match="example.test"):
        httpx.get(url)
    with pytest.raises(pytest.fail.Exception, match="example.test"):
        urllib_request.urlopen(url)
