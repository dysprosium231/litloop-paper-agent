"""The camofox cloud client, driven by a fake HTTP pool (no network)."""

from __future__ import annotations

import base64
import json

import pytest

from litloop.browser import camofox_api


class _Response:
    def __init__(self, status: int, payload):
        self.status = status
        self.data = json.dumps(payload).encode()


class _Pool:
    """Records requests and replies with one prepared response."""

    def __init__(self, response: _Response):
        self.response = response
        self.calls: list[dict] = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response


@pytest.fixture
def pool(monkeypatch):
    def install(status: int = 200, payload=None):
        fake = _Pool(_Response(status, payload if payload is not None else {}))
        monkeypatch.setattr(camofox_api.urllib3, "PoolManager", lambda retries=None: fake)
        return fake

    return install


def test_the_access_key_wins_over_the_api_key():
    both = camofox_api._build_headers({"camofox_access_key": "acc", "camofox_api_key": "api"})
    assert both["Authorization"] == "Bearer acc"
    assert camofox_api._build_headers({"camofox_api_key": "api"})["Authorization"] == "Bearer api"
    assert "Authorization" not in camofox_api._build_headers({})


def test_a_request_carries_params_and_a_json_body(pool):
    fake = pool(payload={"ok": True})
    out = camofox_api._api_request(
        {"camofox_url": "http://box:9377/"}, "POST", "/tabs", body={"url": "https://a.b"},
        params={"userId": "litloop"},
    )
    assert out == {"ok": True}
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "http://box:9377/tabs?userId=litloop"
    assert json.loads(call["body"]) == {"url": "https://a.b"}
    assert call["headers"]["Content-Type"] == "application/json"


def test_an_error_status_becomes_a_runtime_error(pool):
    pool(404, {"error": "no such tab"})
    with pytest.raises(RuntimeError, match="Camofox 404: no such tab"):
        camofox_api._api_request({}, "GET", "/tabs/x")


def test_a_bare_list_is_wrapped_as_a_result(pool):
    pool(200, [1, 2])
    assert camofox_api._api_request({}, "GET", "/x") == {"result": [1, 2]}


def test_availability_needs_a_url_and_a_healthy_server(pool):
    assert camofox_api.is_available({}) is False
    pool(payload={"status": "ok"})
    assert camofox_api.is_available({"camofox_url": "http://box:9377"}) is True
    pool(500, {"error": "boom"})
    assert camofox_api.is_available({"camofox_url": "http://box:9377"}) is False


def test_netscape_cookies_keep_values_with_tabs_and_flag_httponly():
    text = "\n".join(
        [
            "# Netscape HTTP Cookie File",
            "example.edu\tTRUE\t/\tFALSE\t1900000000\tsid\tabc\tdef",
            "#HttpOnly_.example.edu\tTRUE\t/\tTRUE\t0\ttoken\thidden",
            "broken line",
            "short\tTRUE\t/\tFALSE\tnot-a-number\tx\ty",
        ]
    )
    cookies = camofox_api._parse_netscape_cookies(text)
    assert [c["name"] for c in cookies] == ["sid", "token", "x"]
    assert cookies[0]["value"] == "abc\tdef"
    assert cookies[1]["httpOnly"] is True and cookies[1]["secure"] is True
    assert cookies[2]["expires"] == 0


def test_importing_cookies_can_filter_by_domain(pool, tmp_path):
    jar = tmp_path / "cookies.txt"
    jar.write_text(
        "example.edu\tTRUE\t/\tFALSE\t0\ta\t1\n"
        "other.org\tTRUE\t/\tFALSE\t0\tb\t2\n",
        encoding="utf-8",
    )
    fake = pool(payload={"ok": True})
    assert camofox_api.import_cookies(jar, {}, domain_suffix="example.edu") == 1
    body = json.loads(fake.calls[0]["body"])
    assert [c["name"] for c in body["cookies"]] == ["a"]


def test_importing_a_missing_file_or_no_matching_cookies(pool, tmp_path):
    with pytest.raises(FileNotFoundError):
        camofox_api.import_cookies(tmp_path / "nope.txt", {})
    jar = tmp_path / "cookies.txt"
    jar.write_text("other.org\tTRUE\t/\tFALSE\t0\tb\t2\n", encoding="utf-8")
    fake = pool()
    assert camofox_api.import_cookies(jar, {}, domain_suffix="example.edu") == 0
    assert fake.calls == []


def test_tab_helpers_swallow_failures(pool):
    pool(payload={"tabId": "t1"})
    assert camofox_api.create_tab("https://a.b", {}) == "t1"
    pool(500, {"error": "nope"})
    assert camofox_api.create_tab("https://a.b", {}) is None
    assert camofox_api.navigate_tab("t1", "https://a.b", {}) is False
    assert camofox_api.get_captured_responses("t1", {}) == []


def test_only_a_real_pdf_from_the_network_is_accepted(pool):
    big = b"%PDF-" + b"x" * 6000
    pool(payload={"responses": [{"dataBase64": base64.b64encode(big).decode()}]})
    out = camofox_api.fetch_url("t1", "https://a.b/x.pdf", {})
    assert out["status"] == "ok" and out["bytes"] == len(big)

    pool(payload={"responses": [{"dataBase64": base64.b64encode(b"%PDF-small").decode()}]})
    assert camofox_api.fetch_url("t1", "https://a.b/x.pdf", {}) is None


def test_the_pdf_url_heuristics():
    assert camofox_api._is_pdf_url("https://a.b/paper.PDF")
    assert camofox_api._is_pdf_url("https://a.b/doi/pdf/10.1/x")
    assert camofox_api._is_pdf_url("https://a.b/content/pdf?x=1")
    assert camofox_api._is_pdf_url("https://a.b/pdfdirect?x=1")
    assert not camofox_api._is_pdf_url("https://a.b/article/123")
