"""Tests for publisher access verification and classification helpers."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
import requests

from litloop import publisher_access as pa


@pytest.fixture
def tmp_path():
    """Isolated temporary directory avoiding root temp permission issues."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        text: str = "",
        headers: dict[str, str] | None = None,
        url: str = "https://example.org",
        history: list[Any] | None = None,
    ):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.url = url
        self.history = history or []
        self.closed = False

    def close(self):
        self.closed = True


class _FaultyTextResponse:
    def __init__(self, headers: dict[str, str] | None = None):
        self.headers = headers or {}
        self.status_code = 200
        self.url = "https://example.org"
        self.history: list[Any] = []

    @property
    def text(self) -> str:
        raise RuntimeError("cannot decode body")


class _FakeSession:
    def __init__(self, responder=None):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._responder = responder

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if callable(self._responder):
            return self._responder(url, **kwargs)
        return _FakeResponse(url=url)


def test_catalog_and_policy_loaders(tmp_path):
    """Catalog and institutional policy loaders return expected dictionary structures."""
    catalog = pa.load_publisher_access_catalog()
    assert "publishers" in catalog
    assert "elsevier" in catalog["publishers"]

    policy = pa.load_institutional_identity_policy()
    assert isinstance(policy, dict)

    custom_catalog_path = tmp_path / "custom_catalog.json"
    custom_catalog_path.write_text('{"custom": 1}', encoding="utf-8")
    assert pa.load_publisher_access_catalog(custom_catalog_path) == {"custom": 1}

    custom_policy_path = tmp_path / "custom_policy.json"
    custom_policy_path.write_text('{"policy": 2}', encoding="utf-8")
    assert pa.load_institutional_identity_policy(custom_policy_path) == {"policy": 2}


def test_header_retrieval_branches():
    """_header handles exact match, title case fallback, missing headers, and malformed objects."""
    res = _FakeResponse(headers={"content-type": "application/pdf", "Content-Length": "100"})
    assert pa._header(res, "content-type") == "application/pdf"
    assert pa._header(res, "content-length") == "100"
    assert pa._header(res, "x-missing") == ""
    assert pa._header(None, "content-type") == ""
    assert pa._header(_FakeResponse(headers=None), "content-type") == ""

    class _BadHeadersResponse:
        headers = 123

    assert pa._header(_BadHeadersResponse(), "content-type") == ""


def test_response_text_branches():
    """_response_text returns text for textual types, limits length, and suppresses non-text."""
    html_res = _FakeResponse(headers={"Content-Type": "text/html; charset=utf-8"}, text="<html>body</html>")
    assert pa._response_text(html_res) == "<html>body</html>"

    xml_res = _FakeResponse(headers={"Content-Type": "application/xml"}, text="<xml />")
    assert pa._response_text(xml_res) == "<xml />"

    plain_res = _FakeResponse(headers={"Content-Type": "text/plain"}, text="plain text")
    assert pa._response_text(plain_res) == "plain text"

    no_type_res = _FakeResponse(headers={}, text="default text")
    assert pa._response_text(no_type_res) == "default text"

    pdf_res = _FakeResponse(headers={"Content-Type": "application/pdf"}, text="%PDF-1.4...")
    assert pa._response_text(pdf_res) == ""

    long_res = _FakeResponse(headers={"Content-Type": "text/html"}, text="abcdef" * 100)
    assert pa._response_text(long_res, limit=10) == "abcdefabcd"

    faulty_res = _FaultyTextResponse(headers={"Content-Type": "text/html"})
    assert pa._response_text(faulty_res) == ""


@pytest.mark.parametrize(
    "status, content_type, url, text, expected",
    [
        # 1. 200..299 with pdf content-type
        (200, "application/pdf", "https://example.org/doc.pdf", "", "pdf_accessible"),
        (206, "application/x-pdf", "https://example.org/doc.pdf", "", "pdf_accessible"),
        # 2. 401/402/403 with challenge marker in URL
        (403, "text/html", "https://example.org/challenge/login", "", "challenge_or_bot_check"),
        # 3. 401/402/403 with challenge marker in text
        (401, "text/html", "https://example.org/item", "Attention Required! Cloudflare challenge", "challenge_or_bot_check"),
        (402, "text/html", "https://example.org/item", "perfdrive verification needed", "challenge_or_bot_check"),
        # 4. 401/402/403 without challenge marker (identity required)
        (401, "text/html", "https://example.org/item", "Unauthorized user", "identity_required"),
        (402, "text/html", "https://example.org/item", "Payment Required", "identity_required"),
        (403, "text/html", "https://example.org/item", "Access Forbidden", "identity_required"),
        # 5. 404 not found
        (404, "text/html", "https://example.org/notfound", "Not Found", "not_found"),
        # 6. >= 500 server error
        (500, "text/html", "https://example.org/err", "Internal Server Error", "server_error"),
        (503, "text/html", "https://example.org/down", "Service Unavailable", "server_error"),
        # 7. Other status with challenge markers
        (200, "text/html", "https://example.org/item?robot=true", "checking your browser", "challenge_or_bot_check"),
        (200, "text/html", "https://example.org/item", "Please solve this captcha", "challenge_or_bot_check"),
        # 8. Other status with login markers
        (200, "text/html", "https://example.org/login", "article details", "identity_required"),
        (200, "text/html", "https://example.org/item", "Sign in via Shibboleth or OpenAthens", "identity_required"),
        (200, "text/html", "https://example.org/item", "Access through your institution", "identity_required"),
        # 9. 200..299 with html content-type and no markers
        (200, "text/html; charset=utf-8", "https://example.org/article", "Regular scholarly article text", "html_or_reader"),
        # 10. 300..399 redirect
        (302, "text/html", "https://example.org/redirect", "", "redirect"),
        # 11. unknown
        (400, "application/json", "https://example.org/api", '{"error": "bad request"}', "unknown"),
        (200, "application/octet-stream", "https://example.org/stream", "binary data", "unknown"),
    ],
)
def test_classify_response_all_branches(status, content_type, url, text, expected):
    """_classify_response covers all classification branches."""
    res = _FakeResponse(
        status_code=status,
        headers={"Content-Type": content_type},
        url=url,
        text=text,
    )
    assert pa._classify_response(res) == expected


def test_observed_access_branches():
    """_observed_access covers all prioritization branches for candidate and landing outcomes."""
    landing_normal = _FakeResponse(status_code=200, headers={"Content-Type": "text/html"}, text="Normal page")
    landing_challenge = _FakeResponse(status_code=403, headers={"Content-Type": "text/html"}, text="Cloudflare challenge")

    # 1. pdf_accessible in candidate probes
    probes_pdf = [{"classification": "identity_required"}, {"classification": "pdf_accessible"}]
    assert pa._observed_access(landing_normal, probes_pdf) == "pdf_accessible"

    # 2. identity_required in candidate probes
    probes_id = [{"classification": "html_or_reader"}, {"classification": "identity_required"}]
    assert pa._observed_access(landing_normal, probes_id) == "identity_required"

    # 3. challenge_or_bot_check in candidate probes
    probes_challenge = [{"classification": "server_error"}, {"classification": "challenge_or_bot_check"}]
    assert pa._observed_access(landing_normal, probes_challenge) == "challenge_or_bot_check"

    # 4. challenge_or_bot_check on landing page while probes are benign
    probes_other = [{"classification": "html_or_reader"}]
    assert pa._observed_access(landing_challenge, probes_other) == "challenge_or_bot_check"

    # 5. empty candidate probes delegates to landing classification
    assert pa._observed_access(landing_normal, []) == "html_or_reader"

    # 6. all candidates not_found
    probes_404 = [{"classification": "not_found"}, {"classification": "not_found"}]
    assert pa._observed_access(landing_normal, probes_404) == "not_found"

    # 7. fallback to first classification
    probes_server_err = [{"classification": "server_error"}, {"classification": "html_or_reader"}]
    assert pa._observed_access(landing_normal, probes_server_err) == "server_error"

    probes_redirect = [{"classification": "redirect"}]
    assert pa._observed_access(landing_normal, probes_redirect) == "redirect"


def test_probe_candidate_success():
    """_probe_candidate returns structured classification and closes the response."""
    resp = _FakeResponse(
        status_code=200,
        headers={"Content-Type": "application/pdf"},
        url="https://example.org/paper.pdf",
    )
    session = _FakeSession(responder=lambda url, **kw: resp)
    res = pa._probe_candidate(session, "https://example.org/target.pdf", timeout=5)

    assert res["url"] == "https://example.org/target.pdf"
    assert res["final_url"] == "https://example.org/paper.pdf"
    assert res["status"] == 200
    assert res["content_type"] == "application/pdf"
    assert res["classification"] == "pdf_accessible"
    assert resp.closed is True

    class _NoCloseResponse(_FakeResponse):
        close = None

    res2 = pa._probe_candidate(
        _FakeSession(responder=lambda url, **kw: _NoCloseResponse(status_code=200, headers={"Content-Type": "text/html"})),
        "https://example.org/t.html",
        timeout=5,
    )
    assert res2["classification"] == "html_or_reader"


def test_probe_candidate_network_error():
    """_probe_candidate returns network_error when a RequestException occurs."""
    class _FailingSession:
        def get(self, url, **kwargs):
            raise requests.ConnectionError("Connection aborted")

    res = pa._probe_candidate(_FailingSession(), "https://example.org/fail.pdf", timeout=5)
    assert res["url"] == "https://example.org/fail.pdf"
    assert res["final_url"] == ""
    assert res["status"] == 0
    assert res["content_type"] == ""
    assert res["classification"] == "network_error"
    assert "Connection aborted" in res["error"]


def test_profile_key_resolution_and_missing(monkeypatch):
    """_profile_key resolves canonical profile keys and raises ValueError on unknowns."""
    assert pa._profile_key("elsevier") == "elsevier"
    assert pa._profile_key("Elsevier") == "elsevier"
    assert pa._profile_key("wiley") == "wiley"

    with pytest.raises(ValueError, match="Unknown publisher profile"):
        pa._profile_key("completely_unknown_publisher")

    monkeypatch.setattr(pa, "list_publisher_profiles", lambda: [])
    with pytest.raises(ValueError, match="Unknown publisher profile"):
        pa._profile_key("elsevier")


def test_verify_publisher_access_unknown_publisher():
    """verify_publisher_access raises ValueError for unknown publishers."""
    with pytest.raises(ValueError, match="Unknown publisher profile"):
        pa.verify_publisher_access("completely_unknown_publisher")


def test_verify_publisher_access_success():
    """verify_publisher_access performs landing probe, candidate probing, and metadata assembly."""
    landing_html = """
    <html><head>
        <meta name="citation_pdf_url" content="https://www.sciencedirect.com/science/article/pii/S1234/pdfft">
    </head><body>Full paper content</body></html>
    """
    landing_resp = _FakeResponse(
        status_code=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        url="https://www.sciencedirect.com/science/article/pii/S1234",
        text=landing_html,
        history=[_FakeResponse(url="https://doi.org/10.1016/j.test")],
    )
    pdf_resp = _FakeResponse(
        status_code=200,
        headers={"Content-Type": "application/pdf"},
        url="https://www.sciencedirect.com/science/article/pii/S1234/pdfft",
    )

    def responder(url, **kwargs):
        if "doi.org" in url:
            return landing_resp
        return pdf_resp

    session = _FakeSession(responder=responder)
    res = pa.verify_publisher_access("elsevier", session=session, probe_pdf=True, max_candidates=2)

    assert res["profile_key"] == "elsevier"
    assert res["display_name"] == "Elsevier / ScienceDirect"
    assert res["sample_doi"] == "10.1016/j.watres.2024.121507"
    assert res["landing_status"] == 200
    assert res["landing_url"] == "https://www.sciencedirect.com/science/article/pii/S1234"
    assert "https://doi.org/10.1016/j.test" in res["redirect_chain"]
    assert res["resolved_to_expected_domain"] is True
    assert len(res["candidate_probes"]) > 0
    assert res["observed_access"] == "pdf_accessible"
    assert isinstance(res["identity"], dict)
    assert isinstance(res["persistence"], dict)
    assert isinstance(res["link_characteristics"], list)


def test_verify_publisher_access_options_and_unexpected_domain(monkeypatch):
    """verify_publisher_access handles probe_pdf=False, empty landing text, and domain mismatch."""
    landing_resp = _FakeResponse(
        status_code=200,
        headers={"Content-Type": "text/html"},
        url="https://unrelated-domain.org/something",
        text="",
        history=None,
    )
    session = _FakeSession(responder=lambda url, **kw: landing_resp)

    res = pa.verify_publisher_access("elsevier", session=session, probe_pdf=False)
    assert res["resolved_to_expected_domain"] is False
    assert res["candidate_probes"] == []
    assert res["observed_access"] == "html_or_reader"


def test_verify_publisher_access_default_session(monkeypatch):
    """verify_publisher_access creates a default session if None is provided."""
    created_sessions: list[_FakeSession] = []

    def make_session():
        s = _FakeSession(responder=lambda url, **kw: _FakeResponse(
            status_code=200,
            headers={"Content-Type": "text/html"},
            url="https://www.sciencedirect.com/article",
        ))
        created_sessions.append(s)
        return s

    monkeypatch.setattr(requests, "Session", make_session)
    res = pa.verify_publisher_access("elsevier", probe_pdf=False)
    assert len(created_sessions) == 1
    assert res["profile_key"] == "elsevier"


def test_verify_publishers(monkeypatch):
    """verify_publishers verifies specified publisher list or defaults to all profiles."""
    calls: list[str] = []

    def fake_verify(pub, **kwargs):
        calls.append(pub)
        return {"profile_key": pub, "observed_access": "pdf_accessible"}

    monkeypatch.setattr(pa, "verify_publisher_access", fake_verify)

    # 1. Explicit publishers list
    res = pa.verify_publishers(["elsevier", "wiley"], probe_pdf=False)
    assert calls == ["elsevier", "wiley"]
    assert len(res) == 2

    # 2. Default publishers from list_publisher_profiles
    calls.clear()
    monkeypatch.setattr(pa, "list_publisher_profiles", lambda: ["ieee"])
    res_default = pa.verify_publishers(probe_pdf=True, max_candidates=2, timeout=10)
    assert calls == ["ieee"]
    assert len(res_default) == 1
