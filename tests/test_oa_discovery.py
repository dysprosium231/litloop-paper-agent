"""OpenAIRE and DOAJ lookups, with the HTTP session and downloader faked."""

from __future__ import annotations

from pathlib import Path

from litloop.sources import oa_discovery


class Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class Session:
    """Stands in for requests.Session; records what the caller asked for."""

    made: list["Session"] = []
    reply: Response = Response({})

    def __init__(self):
        self.trust_env = True
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict]] = []
        Session.made.append(self)

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Session.reply


def _setup(monkeypatch, payload, *, downloads=None):
    Session.made = []
    Session.reply = Response(payload)
    monkeypatch.setattr(oa_discovery.requests, "Session", Session)
    monkeypatch.setattr(oa_discovery, "polite_delay", lambda config: None)
    seen: list[tuple[str, bool]] = []

    def fake_download(url, output_path, config, source, require_pdf_like_url=True):
        seen.append((url, require_pdf_like_url))
        if downloads is not None and url not in downloads:
            return None
        return {"file": str(output_path), "source": source}

    monkeypatch.setattr(oa_discovery, "download_pdf", fake_download)
    return seen


def _openaire_payload(*publications):
    return {"response": {"results": {"result": list(publications)}}}


def _publication(access="OPEN", child_urls=(), web_url=""):
    instance: dict = {}
    if child_urls:
        instance["url"] = {"$": child_urls[0]}
    if web_url:
        instance["webresource"] = {"url": {"$": web_url}}
    child = {"instance": instance}
    return {
        "metadata": {
            "oaf:entity": {
                "oaf:result": {
                    "bestaccessright": {"classid": access},
                    "children": {"result": child},
                }
            }
        }
    }


def test_openaire_downloads_the_open_instance_url(monkeypatch, tmp_path):
    seen = _setup(monkeypatch, _openaire_payload(_publication(child_urls=["https://e.org/a.pdf"])))
    result = oa_discovery.try_openaire("10.1/x", Path(tmp_path) / "o.pdf", {})
    assert seen == [("https://e.org/a.pdf", False)]
    assert result["doi"] == "10.1/x" and result["source"] == "OpenAIRE"
    assert Session.made[0].trust_env is False
    assert Session.made[0].headers["User-Agent"]


def test_openaire_skips_closed_records_and_uses_the_open_one(monkeypatch, tmp_path):
    payload = _openaire_payload(
        _publication(access="CLOSED", child_urls=["https://e.org/closed.pdf"]),
        _publication(child_urls=["https://e.org/open.pdf"]),
    )
    seen = _setup(monkeypatch, payload)
    oa_discovery.try_openaire("10.1/x", Path(tmp_path) / "o.pdf", {})
    assert seen == [("https://e.org/open.pdf", False)]


def test_openaire_falls_back_to_the_web_resource(monkeypatch, tmp_path):
    seen = _setup(monkeypatch, _openaire_payload(_publication(web_url="https://e.org/landing")))
    result = oa_discovery.try_openaire("10.1/x", Path(tmp_path) / "o.pdf", {})
    assert seen == [("https://e.org/landing", False)]
    assert result is not None


def test_openaire_returns_nothing_on_an_error_status(monkeypatch, tmp_path):
    Session.made = []
    Session.reply = Response({}, status=503)
    monkeypatch.setattr(oa_discovery.requests, "Session", Session)
    monkeypatch.setattr(oa_discovery, "polite_delay", lambda config: None)
    assert oa_discovery.try_openaire("10.1/x", Path(tmp_path) / "o.pdf", {}) is None


def test_openaire_returns_nothing_when_no_publication_has_a_link(monkeypatch, tmp_path):
    seen = _setup(monkeypatch, _openaire_payload(_publication()))
    assert oa_discovery.try_openaire("10.1/x", Path(tmp_path) / "o.pdf", {}) is None
    assert seen == []


def test_doaj_uses_only_fulltext_links(monkeypatch, tmp_path):
    payload = {"results": [{"bibjson": {"link": [
        {"url": "https://e.org/about", "type": "homepage"},
        {"url": "https://e.org/paper.pdf", "type": "fulltext"},
    ]}}]}
    seen = _setup(monkeypatch, payload)
    result = oa_discovery.try_doaj("10.1/x", Path(tmp_path) / "o.pdf", {})
    assert seen == [("https://e.org/paper.pdf", False)]
    assert result["doi"] == "10.1/x" and result["source"] == "DOAJ"


def test_doaj_returns_nothing_without_results(monkeypatch, tmp_path):
    _setup(monkeypatch, {"results": []})
    assert oa_discovery.try_doaj("10.1/x", Path(tmp_path) / "o.pdf", {}) is None


def test_doaj_moves_on_when_the_first_link_does_not_download(monkeypatch, tmp_path):
    payload = {"results": [{"bibjson": {"link": [
        {"url": "https://e.org/broken.pdf", "type": "fulltext"},
        {"url": "https://e.org/ok.pdf", "type": "fulltext"},
    ]}}]}
    seen = _setup(monkeypatch, payload, downloads={"https://e.org/ok.pdf"})
    result = oa_discovery.try_doaj("10.1/x", Path(tmp_path) / "o.pdf", {})
    assert [u for u, _ in seen] == ["https://e.org/broken.pdf", "https://e.org/ok.pdf"]
    assert result is not None
