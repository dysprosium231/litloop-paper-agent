"""Regression tests for the fork's institutional-download fixes.

Each test pins one bug that was hit live (BUAA campus/WebVPN/CARSI setup);
all run offline with fakes — no browser, no network.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from litloop import browser_backend as bb
from litloop import browser_pdf
from litloop import sources


# ---------------------------------------------------------------- browser setup

def test_direct_args_bypass_system_proxy():
    assert bb._direct_args(bb.BACKEND_CLOAKBROWSER, None, ["--x"]) == ["--x", "--no-proxy-server"]
    # an explicit proxy wins; Firefox (camoufox) takes no Chromium flags
    assert bb._direct_args(bb.BACKEND_PATCHRIGHT, {"server": "http://p"}, ["--x"]) == ["--x"]
    assert bb._direct_args(bb.BACKEND_CAMOUFOX, None, None) is None
    # never duplicated
    assert bb._direct_args(bb.BACKEND_PATCHRIGHT, None, ["--no-proxy-server"]) == ["--no-proxy-server"]


def test_launch_config_falls_back_to_saved_config(monkeypatch):
    import litloop.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "load_config", lambda: {"browser_backend": "cloakbrowser"})
    assert bb._resolve_launch_config(None) == {"browser_backend": "cloakbrowser"}
    assert bb._resolve_launch_config({"a": 1}) == {"a": 1}


@pytest.mark.skipif(os.name != "nt", reason="stable alias is Windows-only")
def test_stable_browser_alias_created_and_refreshed(tmp_path):
    exe = tmp_path / "chromium-1.2.3" / "chrome.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"v1")
    alias = Path(bb.stable_browser_alias(exe))
    assert alias.name == bb.STABLE_BROWSER_NAME and alias.read_bytes() == b"v1"
    # binary replaced by an upgrade -> alias follows
    exe.unlink()
    exe.write_bytes(b"v2-longer")
    assert Path(bb.stable_browser_alias(exe)).read_bytes() == b"v2-longer"
    # the alias itself is passed through untouched
    assert bb.stable_browser_alias(alias) == str(alias)


def test_close_browsers_since_kills_only_newer(monkeypatch):
    import litloop.browser_engine as be
    killed = []
    monkeypatch.setattr(be, "_tree_kill", lambda proc: killed.append(proc))

    def fake(proc):
        return SimpleNamespace(_impl_obj=SimpleNamespace(
            _connection=SimpleNamespace(_transport=SimpleNamespace(_proc=proc))))

    monkeypatch.setattr(bb, "_LAUNCHED", [])
    bb._remember(fake("old"))
    t0 = time.perf_counter()
    bb._remember(fake("new"))
    assert bb.close_browsers_since(t0) == 1
    assert killed == ["new"]
    assert len(bb._LAUNCHED) == 1  # the older browser stays registered


def test_launch_guard_blocks_waived_sources(monkeypatch):
    """A source thread whose download already returned must not (re)launch."""
    monkeypatch.setattr(bb, "_launch_unregistered", lambda **kw: pytest.fail("launched"))
    sources._SOURCE_TLS.doi = "10.1/settled"
    sources._SETTLED.add("10.1/settled")
    try:
        with pytest.raises(RuntimeError, match="download already finished"):
            bb.launch(headless=True)
    finally:
        sources._SOURCE_TLS.doi = None
        sources._SETTLED.discard("10.1/settled")


# ---------------------------------------------------------------- racing hygiene

def test_late_finisher_drops_its_copy(tmp_path, monkeypatch):
    out = tmp_path / "10.1_late_SlowSource.pdf"
    sources._SETTLED.add("10.1/late")

    def slow_source(doi, output_path, config):
        output_path.write_bytes(b"%PDF-1.4 late copy")
        return {"success": True, "file": str(output_path)}

    try:
        assert sources._try_source(slow_source, "10.1/late", out, {}, "SlowSource") is None
        assert not out.exists()
    finally:
        sources._SETTLED.discard("10.1/late")


def test_rename_removes_duplicate_source_copies(tmp_path, monkeypatch):
    import litloop.citation as citation
    doi = "10.1038/s1"
    safe = sources.safe_filename(doi)
    winner = tmp_path / f"{safe}.pdf"
    winner.write_bytes(b"%PDF")
    for label in ("NatureDirect", "PublisherDirect"):
        (tmp_path / f"{safe}_{label}.pdf").write_bytes(b"%PDF")
    final = tmp_path / "Wu2024_Bridging.pdf"
    monkeypatch.setattr(citation, "fetch_metadata", lambda d, c: {"title": ["x"]})
    monkeypatch.setattr(sources, "rename_pdf", lambda p, m, **kw: Path(p).rename(final) or final)
    monkeypatch.setattr(sources, "_update_doi_index", lambda *a, **k: None)
    result = {"success": True, "file": str(winner)}
    sources._auto_rename(result, doi, {"auto_rename": True}, doi=doi, target_dir=tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Wu2024_Bridging.pdf"]


# ---------------------------------------------------------------- publisher URLs

def test_elsevier_pii_keeps_check_char(monkeypatch):
    import requests
    from litloop import _publisher_strategies_core as core

    class _Sess:
        trust_env = True
        headers: dict = {}

        def get(self, url, **kw):
            return SimpleNamespace(url="https://linkinghub.elsevier.com/retrieve/pii/S030626192400494X",
                                   text="sciencedirect.com")

    monkeypatch.setattr(requests, "Session", _Sess)
    assert core._resolve_elsevier_pii("10.1016/x", {}).endswith("/pii/S030626192400494X")


# ---------------------------------------------------------------- session checks

def test_webvpn_session_probe_hits_gateway_root(monkeypatch):
    from litloop.sources import instsci
    seen = []

    class _Sess:
        trust_env = True
        cookies = SimpleNamespace(update=lambda jar: None)

        def get(self, url, **kw):
            seen.append(url)
            return SimpleNamespace(url="https://d.example.edu/", status_code=200)

    monkeypatch.setattr(instsci, "_load_cookies", lambda c: {"t": "1"})
    monkeypatch.setattr(instsci, "_get_webvpn_base", lambda c: "https://d.example.edu")
    monkeypatch.setattr(instsci.requests, "Session", _Sess)
    assert instsci.session_status({}) == "valid"
    assert seen == ["https://d.example.edu/"]  # not a proxied publisher


def test_carsi_cloudflare_probe_trusts_fresh_cookies(tmp_path, monkeypatch):
    from litloop.sources import carsi
    client = carsi.CARSIClient.__new__(carsi.CARSIClient)
    client._publisher_configs = {"sd": SimpleNamespace(domains=["www.sciencedirect.com"])}
    cookie = tmp_path / "sd.json"
    cookie.write_text("[]")
    client._cookie_path = lambda p: cookie
    resp = SimpleNamespace(url="https://www.sciencedirect.com/", status_code=403,
                           headers={"server": "cloudflare", "cf-mitigated": "challenge"})
    client._get_session = lambda p: SimpleNamespace(get=lambda *a, **k: resp)
    assert client._validate_session("sd") is True
    # a stale cookie file is still rejected before any probe
    old = time.time() - 30 * 3600
    os.utime(cookie, (old, old))
    assert client._validate_session("sd") is False


# ---------------------------------------------------------------- browser_pdf helpers

class _FakePage:
    def __init__(self, titles, href=None, body=""):
        self._titles = list(titles)
        self.url = "https://pub.example/article"
        self._href = href
        self._body = body
        self.waits = 0

    def wait_for_load_state(self, *a, **k):
        pass

    def title(self):
        return self._titles.pop(0) if len(self._titles) > 1 else self._titles[0]

    def wait_for_timeout(self, ms):
        self.waits += 1

    def evaluate(self, expr, *a):
        if "innerText" in expr:
            return self._body
        if "document.readyState" in expr:
            return "interactive"
        return self._href


def test_wait_page_settled_skips_interstitials():
    page = _FakePage(["Loading https://x", "请稍候…", "Article title"])
    assert browser_pdf.wait_page_settled(page, max_wait_s=10)[0] == "Article title"


def test_wait_page_settled_stops_on_cloudflare_verdict():
    page = _FakePage(["请稍候…"], body="pubs.aip.org 浏览器不支持")
    assert browser_pdf.wait_page_settled(page, max_wait_s=30)[0] == "请稍候…"
    assert page.waits == 0  # returned at once, not after 30 s


def test_find_page_pdf_link_polls_until_rendered():
    page = _FakePage(["t"])
    hrefs = [None, None, "https://pub.example/pdfft?md5=1"]
    page.evaluate = lambda expr, *a: hrefs.pop(0)
    assert browser_pdf.find_page_pdf_link(page, max_wait_s=5) == "https://pub.example/pdfft?md5=1"


def test_capture_pdf_via_cdp_reads_paused_document():
    import base64
    pdf = b"%PDF-1.7" + b"0" * 6000
    handlers = {}
    sent = []

    class _Cdp:
        def on(self, ev, fn):
            handlers[ev] = fn

        def send(self, method, params=None):
            sent.append(method)
            if method == "Fetch.getResponseBody":
                return {"body": base64.b64encode(pdf).decode(), "base64Encoded": True}
            return {}

        def detach(self):
            pass

    class _Page(_FakePage):
        def evaluate(self, expr, *a):  # the navigation: the browser answers with a PDF
            handlers["Fetch.requestPaused"]({
                "requestId": "1", "responseStatusCode": 200,
                "responseHeaders": [{"name": "Content-Type", "value": "application/pdf"}]})

    page = _Page(["t"])
    ctx = SimpleNamespace(new_cdp_session=lambda p: _Cdp())
    assert browser_pdf.capture_pdf_via_cdp(ctx, page, "https://pub.example/pdf") == pdf
    assert "Fetch.continueRequest" in sent  # the paused response was released


# ---------------------------------------------------------------- logging

def test_plugin_logger_does_not_duplicate_to_root():
    from litloop.log import get_logger
    assert get_logger().propagate is False
    assert isinstance(logging.getLogger("litloop"), logging.Logger)


# ---------------------------------------------------------------- cancellation

@pytest.fixture
def no_browser_kill(monkeypatch):
    monkeypatch.setattr(bb, "close_browsers_since", lambda t0: 0)


def test_cancel_marks_running_download_cancelled(monkeypatch, no_browser_kill):
    seen = {}

    def impl(identifier, *a, **k):
        seen["out"] = sources.cancel_downloads()  # user cancels mid-download
        return {"success": False, "reason": "all sources failed"}

    monkeypatch.setattr(sources, "_download_impl", impl)
    r = sources.download("10.1/cancel-me")
    assert r["error_type"] == "cancelled"
    assert "10.1/cancel-me" in seen["out"]["cancelled"]


def test_download_started_after_cancel_is_unaffected(monkeypatch, no_browser_kill):
    sources.cancel_downloads()
    monkeypatch.setattr(sources, "_download_impl", lambda *a, **k: {"success": False, "reason": "x"})
    assert sources.download("10.1/after-cancel").get("error_type") != "cancelled"


def test_wait_any_returns_early_once_cancelled(no_browser_kill):
    import threading
    sources._DL_TLS.gen = sources._CANCEL_GEN[0]
    try:
        sources.cancel_downloads()
        t = time.monotonic()
        assert sources._wait_any((threading.Event(), threading.Event()), 5) is False
        assert time.monotonic() - t < 1
        # and no new race starts for a cancelled download
        assert sources._run_tiers_parallel(
            [([(lambda *a: pytest.fail("raced"), "X")], "T", 3)],
            "10.1/x", Path("."), Path("o.pdf"), {}, False, 3) is None
    finally:
        sources._DL_TLS.gen = None


# ---------------------------------------------------------------- OpenAlex key

def test_openalex_key_only_sent_to_openalex(monkeypatch):
    import requests
    import litloop.search as search_mod
    monkeypatch.setattr(search_mod, "load_config", lambda: {"openalex_api_key": "K123"})
    sent = {}
    monkeypatch.setattr(requests.Session, "request",
                        lambda self, m, url, *a, **kw: sent.setdefault(url, kw.get("params")))
    s = search_mod._plain_session()
    s.get("https://api.openalex.org/works", params={"search": "x"})
    s.get("https://api.semanticscholar.org/graph/v1/paper/search", params={"query": "x"})
    assert sent["https://api.openalex.org/works"] == {"search": "x", "api_key": "K123"}
    assert "api_key" not in (sent["https://api.semanticscholar.org/graph/v1/paper/search"] or {})


def test_openalex_source_sends_key_as_api_key(monkeypatch, tmp_path):
    from litloop.sources import openalex
    urls = []
    monkeypatch.setattr(openalex, "fetch_json", lambda url, cfg: urls.append(url) or None)
    openalex.try_openalex_content_api("10.1/x", tmp_path / "o.pdf", {"openalex_api_key": "K123"})
    assert urls and "api_key=K123" in urls[0] and "mailto=K123" not in urls[0]


# ---------------------------------------------------------------- login in progress

def test_race_keeps_waiting_while_user_logs_in(tmp_path, monkeypatch, no_browser_kill):
    """The race must not give up (and open the next phase's window) mid-login."""
    from litloop import pdf_utils
    real_wait = sources._wait_any
    monkeypatch.setattr(sources, "_wait_any", lambda ev, t: real_wait(ev, min(t, 0.2)))
    monkeypatch.setattr(pdf_utils, "is_suspicious_pdf", lambda *a: False)

    def logging_in(doi, out, config):
        for _ in range(15):  # the user takes longer than the race's grace period
            sources.note_user_waiting()
            time.sleep(0.1)
        out.write_bytes(b"%PDF-1.4 ok")
        return {"success": True, "file": str(out)}

    tiers = [([(logging_in, "ScienceBrowser"), (lambda *a: None, "Fast")], "T", 0)]
    r = sources._run_tiers_parallel(tiers, "10.1/login", tmp_path, tmp_path / "o.pdf",
                                    {"hedge_delay_seconds": 0},
                                    False, 0)
    assert r and r["success"]


def test_waived_lane_cannot_launch_a_browser(monkeypatch):
    import threading
    monkeypatch.setattr(bb, "_launch_unregistered", lambda **kw: pytest.fail("launched"))
    race = threading.Event()
    sources._SOURCE_TLS.race = race
    try:
        assert sources._source_may_continue()
        race.set()  # the race returned without this lane
        with pytest.raises(RuntimeError):
            bb.launch(headless=True)
    finally:
        sources._SOURCE_TLS.race = None


def test_browser_errors_are_per_thread():
    import threading
    from litloop import _publisher_strategies_core as core
    core._set_error("login_abandoned", "retry_login")
    other = {}
    t = threading.Thread(target=lambda: (core._clear_error(), other.update(e=core.get_last_error())))
    t.start(); t.join()
    assert other["e"] == ("", "")
    assert core.get_last_error() == ("login_abandoned", "retry_login")
    core._clear_error()


def test_school_login_hosts_are_not_tsinghua_only():
    from litloop.institutional.publisher_batch import _is_school_login_host as f
    assert f("sso.buaa.edu.cn") and f("idp.tsinghua.edu.cn")
    assert not f("ds.carsi.edu.cn") and not f("www.science.org")


def test_science_check_access_is_a_paywall():
    from litloop._publisher_strategies_core import _detect_paywall
    assert _detect_paywall('<button>CHECK ACCESS</button>')


def test_race_waits_for_download_after_login(tmp_path, monkeypatch, no_browser_kill):
    """Login done (heartbeats stop), PDF capture still running: keep waiting."""
    from litloop import pdf_utils
    real_wait = sources._wait_any
    monkeypatch.setattr(sources, "_wait_any", lambda ev, t: real_wait(ev, min(t, 0.2)))
    monkeypatch.setattr(pdf_utils, "is_suspicious_pdf", lambda *a: False)

    def login_then_download(doi, out, config):
        sources.note_user_waiting()  # last heartbeat: login finished
        time.sleep(1.5)              # reopening the article + capturing the PDF
        out.write_bytes(b"%PDF-1.4 ok")
        return {"success": True, "file": str(out)}

    tiers = [([(login_then_download, "ScienceBrowser"), (lambda *a: None, "Fast")], "T", 0)]
    r = sources._run_tiers_parallel(tiers, "10.1/after-login", tmp_path, tmp_path / "o.pdf",
                                    {"hedge_delay_seconds": 0}, False, 0)
    assert r and r["success"]


def test_http_pdf_download_has_wall_clock_deadline(tmp_path, monkeypatch):
    """A trickling server must not hold a racing lane for minutes."""
    import requests
    from litloop import _publisher_strategies_core as core

    class Resp:
        status_code = 200
        headers = {"content-type": "application/pdf"}
        def iter_content(self, chunk_size=8192):
            yield b"%PDF-1.4\n"
            while True:
                time.sleep(0.05)
                yield b"x" * 10

    monkeypatch.setattr(requests.Session, "get", lambda self, *a, **k: Resp())
    t = time.monotonic()
    ok = core._try_http_download("https://example.org/a.pdf", tmp_path / "a.pdf",
                                 {"download_deadline_seconds": 0.5})
    assert ok is False and time.monotonic() - t < 3
    assert not (tmp_path / "a.pdf.part").exists()


def test_navigate_retries_transient_network_error(monkeypatch):
    from litloop import browser_engine as be
    calls = []

    class Page:
        def goto(self, url, **kw):
            calls.append(url)
            if len(calls) == 1:
                raise RuntimeError("Page.goto: net::ERR_TIMED_OUT at " + url)

    monkeypatch.setattr(be, "_resolve_tab", lambda tab_id: Page())
    assert be.navigate_tab("t", "https://example.org", {}) is True
    assert len(calls) == 2


def test_one_page_article_pdf_is_not_a_preview(tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    from litloop import pdf_utils
    pdf = tmp_path / "a.pdf"
    doc = fitz.open(); doc.new_page(); doc.save(pdf); doc.close()
    pdf.write_bytes(pdf.read_bytes() + b"%" + b"0" * 60_000)  # past the size floor
    monkeypatch.setattr(pdf_utils, "_article_is_single_page", lambda doi: doi == "10.1/one-page")
    assert pdf_utils.is_suspicious_pdf(pdf, "10.1/long-article")
    assert not pdf_utils.is_suspicious_pdf(pdf, "10.1/one-page")


def test_pdf_capture_recognises_bounce_to_article():
    from litloop.browser_pdf import _is_bounce_to_html
    html = [{"name": "Content-Type", "value": "text/html; charset=utf-8"}]
    pdf = "https://www.nature.com/articles/x.pdf"
    bounce = {"responseStatusCode": 200, "responseHeaders": html,
              "request": {"url": "https://www.nature.com/articles/x"}}
    crasolve = {"responseStatusCode": 200, "responseHeaders": html,
                "request": {"url": "https://www.sciencedirect.com/science/article/pii/S1/pdfft?md5=1"}}
    assert _is_bounce_to_html(bounce, pdf)
    assert not _is_bounce_to_html(crasolve, "https://www.sciencedirect.com/science/article/pii/S1/pdfft")


def test_host_proxy_exception_routes_only_listed_domains(monkeypatch):
    import base64
    from litloop import network
    monkeypatch.setattr(network, "_proxy_reachable", lambda url: True)
    cfg = {"host_proxy_url": "http://127.0.0.1:10808", "host_proxy_hosts": ["nature.com"]}
    url, hosts = network.host_proxy(cfg)
    assert network.host_uses_proxy("www.nature.com", hosts) and network.host_uses_proxy("nature.com", hosts)
    assert not network.host_uses_proxy("www.sciencedirect.com", hosts)
    assert not network.host_uses_proxy("notnature.com", hosts)
    args = bb._direct_args(bb.BACKEND_PATCHRIGHT, None, [], cfg)
    pac = base64.b64decode(args[0].split("base64,", 1)[1]).decode()
    assert args[0].startswith("--proxy-pac-url=") and "PROXY 127.0.0.1:10808; DIRECT" in pac
    monkeypatch.setattr(network, "_proxy_reachable", lambda url: False)  # proxy off: all direct
    assert bb._direct_args(bb.BACKEND_PATCHRIGHT, None, [], cfg) == ["--no-proxy-server"]


def test_publisher_browser_lane_is_launched_second():
    lanes = [(None, n) for n in ("Crossref", "Unpaywall", "OpenAlexOA", "CORE", "ACSBrowser")]
    sources._promote_publisher_browser(lanes, "10.1021/acsami.5c16640")
    assert [n for _, n in lanes][:2] == ["Crossref", "ACSBrowser"]


def test_known_landing_urls_skip_doi_org():
    from litloop._publisher_strategies_core import _known_landing_url as f
    assert f("10.1126/science.aeg4972") == "https://www.science.org/doi/10.1126/science.aeg4972"
    assert f("10.1038/s41563-025-02281-z") == "https://www.nature.com/articles/s41563-025-02281-z"
    assert f("10.1007/978-3-030-1") == "https://link.springer.com/chapter/10.1007/978-3-030-1"
    assert f("10.1063/5.0282339") == ""  # AIP needs doi.org


def test_direct_http_skipped_behind_bot_walls():
    from litloop._publisher_strategies_core import _direct_pdf_urls
    assert _direct_pdf_urls("10.1021/acsami.5c16640", "ACS") == []
    springer = _direct_pdf_urls("10.1007/s10853-026-13435-y", "Springer")
    assert springer and len(springer) == len(set(springer))


def test_altcha_pow_solver_round_trip():
    import base64, hashlib, json
    from litloop.sources import altcha
    target = hashlib.sha256(b"salt" + b"4242").hexdigest()
    payload = altcha.solve({"algorithm": "SHA-256", "challenge": target, "salt": "salt",
                            "maxnumber": 10000, "signature": "sig"})
    decoded = json.loads(base64.b64decode(payload))
    assert decoded["number"] == 4242 and decoded["signature"] == "sig"
    assert altcha.is_altcha_page('<altcha-widget challengeurl="/altcha/challenge/x">')
    assert not altcha.is_altcha_page("<html>plain article</html>")


def test_scihub_use_browser_false_disables_grey_browsers():
    from litloop.sources import scihub
    assert scihub._is_browser_available({"scihub_use_browser": False}) is False


# --- search polish (2026-10-11) -------------------------------------------

def test_search_sort_aliases():
    from litloop.search import _normalize_sort
    assert _normalize_sort("cited") == "cited_by_count"
    assert _normalize_sort("Citations") == "cited_by_count"
    assert _normalize_sort("newest") == "publication_date"
    assert _normalize_sort("relevance") is None
    assert _normalize_sort(None) is None


def test_search_drops_off_topic_records():
    from litloop.search import _drop_off_topic
    rows = [
        {"title": "High-entropy ceramics with low thermal conductivity", "abstract": ""},
        {"title": "Endoscopic mucosal resection for lesions", "abstract": ""},
        {"title": "Cytotoxicity of dental alloys, metals, and ceramics", "abstract": ""},
        {"title": "Unrelated title", "abstract": "", "_matched": True},
    ]
    kept = _drop_off_topic(rows, "high-entropy ceramics thermal conductivity")
    assert [r["title"] for r in kept] == [
        "High-entropy ceramics with low thermal conductivity", "Unrelated title"]
    assert "_matched" not in kept[1]
    # CJK queries are left alone
    assert len(_drop_off_topic(rows[:2], "高熵陶瓷 热导率")) == 2


def test_discovery_results_report_real_citation_counts():
    from litloop.discovery import to_legacy_results
    out = to_legacy_results([{"doi": "10.1/x", "title": "t", "citation_count": 619,
                              "source_hits": ["OpenAlex", "Crossref"]}])
    assert out[0]["cited_by_count"] == 619
    assert out[0]["sources"] == ["OpenAlex", "Crossref"]


def test_openalex_cited_sort_matches_title_and_abstract(monkeypatch):
    from litloop import search as s
    seen = {}

    class Resp:
        status_code = 200
        def json(self):
            return {"results": []}

    class Sess:
        def get(self, url, params=None, timeout=None):
            seen.update(params)
            return Resp()

    monkeypatch.setattr(s, "_plain_session", lambda: Sess())
    monkeypatch.setattr(s, "load_config", lambda: {"openalex_api_key": "k"})
    s._search_openalex("SiOC, oxidation", 5, 2022, None, "cited_by_count")
    assert "search" not in seen
    assert "title_and_abstract.search:SiOC  oxidation" in seen["filter"]
    assert seen["api_key"] == "k" and seen["sort"] == "cited_by_count:desc"


def test_search_results_get_journal_rank_when_tool_installed(monkeypatch):
    import sys, types
    from litloop import search as s
    fake = types.ModuleType("litloop_journal_rank")
    fake.lookup = lambda journal="", issn=None: (
        {"found": True, "label": "中科院2区", "warnings": [{"year": 2025}]} if issn == ["0272-8842"]
        else {"found": False, "label": ""})
    monkeypatch.setitem(sys.modules, "litloop_journal_rank", fake)
    out = s._add_journal_rank([{"journal": "Ceramics International", "issn": ["0272-8842"]},
                               {"journal": "Unknown", "issn": []}, {"title": "no journal"}])
    assert out[0]["journal_rank"] == "中科院2区" and out[0]["journal_warning"] is True
    assert "journal_rank" not in out[1] and "journal_rank" not in out[2]


def test_search_works_without_journal_rank_tool(monkeypatch):
    import sys
    from litloop import search as s
    monkeypatch.setitem(sys.modules, "litloop_journal_rank", None)  # import fails
    rows = [{"journal": "X"}]
    assert s._add_journal_rank(rows) == [{"journal": "X"}]


def test_journal_filters_and_drop_counts():
    from litloop.search import _filter_by_journal
    rows = [
        {"title": "a", "cas_zone": 1, "jcr_quartile": 1},
        {"title": "b", "cas_zone": 3, "jcr_quartile": 1},
        {"title": "c"},  # journal not in any table
        {"title": "d", "cas_zone": 1, "jcr_quartile": 1, "journal_warning": True},
    ]
    stats = {}
    kept = _filter_by_journal(rows, 2, None, True, stats)
    assert [r["title"] for r in kept] == ["a"]
    assert stats == {"dropped_unranked": 1, "dropped_below_threshold": 1, "dropped_warning": 1}
    # warning filter alone keeps unranked journals
    assert [r["title"] for r in _filter_by_journal(rows, None, None, True, None)] == ["a", "b", "c"]


def test_finalize_flags_and_optionally_drops_retracted(monkeypatch):
    from litloop import abstracts, search as s
    monkeypatch.setattr(s, "_add_journal_rank", lambda rows: rows)
    monkeypatch.setattr(abstracts, "fill_abstracts", lambda rows, config=None: None)

    def fake_flag(rows):
        for r in rows:
            if r["doi"] == "10.1/bad":
                r["retracted"] = True
    monkeypatch.setattr(s, "_flag_retractions", fake_flag)
    rows = lambda: [{"doi": "10.1/bad"}, {"doi": "10.1/ok"}]
    shown = s._finalize(rows(), 5)
    assert shown[0]["retracted"] is True and len(shown) == 2
    stats = {}
    assert s._finalize(rows(), 5, exclude_retracted=True, stats=stats) == [{"doi": "10.1/ok"}]
    assert stats["dropped_retracted"] == 1


def test_retraction_check_is_one_batched_openalex_request(monkeypatch):
    from litloop import search as s
    calls = []

    class Resp:
        status_code = 200
        def json(self):
            return {"results": [{"doi": "https://doi.org/10.1/bad", "is_retracted": True},
                                {"doi": "https://doi.org/10.1/ok", "is_retracted": False}]}

    class Sess:
        def get(self, url, params=None, timeout=None):
            calls.append(params)
            return Resp()

    import sys
    monkeypatch.setitem(sys.modules, "litloop_journal_rank.retractions", None)  # no local copy
    monkeypatch.setattr(s, "_plain_session", lambda: Sess())
    monkeypatch.setattr(s, "load_config", lambda: {})
    rows = [{"doi": "10.1/BAD"}, {"doi": "10.1/ok"}, {"title": "no doi"}]
    s._flag_retractions(rows)
    assert len(calls) == 1 and calls[0]["filter"] == "doi:10.1/bad|10.1/ok"
    assert rows[0]["retracted"] is True and "retracted" not in rows[1]


def test_subcategory_zone_filter_keeps_niche_top_journals():
    from litloop.search import _filter_by_journal
    rows = [
        {"title": "jecs", "cas_zone": 2, "cas_sub_zone": 1},   # 大类2区, 小类1区
        {"title": "ci", "cas_zone": 2, "cas_sub_zone": 2},
        {"title": "no-sub", "cas_zone": 1},
    ]
    stats = {}
    kept = _filter_by_journal(rows, None, None, False, stats, max_cas_sub_zone=1)
    assert [r["title"] for r in kept] == ["jecs"]
    assert stats["dropped_below_threshold"] == 1 and stats["dropped_unranked"] == 1
    assert [r["title"] for r in _filter_by_journal(rows, 1, None, False, None)] == ["no-sub"]


def test_retraction_flags_prefer_local_retraction_watch(monkeypatch):
    import sys, types
    from litloop import search as s
    fake = types.ModuleType("litloop_journal_rank.retractions")
    fake.ensure_fresh = lambda proxy=None: "fresh"
    fake.lookup_papers = lambda dois: {
        "10.1/bad": {"retracted": True, "concern": False,
                     "notices": [{"nature": "Retraction", "date": "2023-07-01", "reasons": ["Paper Mill"],
                                  "notice_doi": "x", "record_id": "1"}]},
        "10.1/eoc": {"retracted": False, "concern": True,
                     "notices": [{"nature": "Expression of concern", "date": "2024-01-02", "reasons": [],
                                  "notice_doi": "", "record_id": "2"}]},
    }
    monkeypatch.setitem(sys.modules, "litloop_journal_rank.retractions", fake)
    monkeypatch.setattr(s, "_plain_session", lambda: (_ for _ in ()).throw(AssertionError("went online")))
    rows = [{"doi": "10.1/BAD"}, {"doi": "10.1/eoc"}, {"doi": "10.1/ok"}]
    s._flag_retractions(rows)
    assert rows[0]["retracted"] is True and rows[0]["notices"][0]["reasons"] == ["Paper Mill"]
    assert rows[1]["concern"] is True and "retracted" not in rows[1]
    assert rows[2] == {"doi": "10.1/ok"}


# ---------------------------------------------------------------- same-name cache hits

def _pdf_with_text(path, text):
    import pymupdf
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def test_name_match_requires_doi_in_pdf(tmp_path):
    pdf = tmp_path / "Jiang2024_Lightweight.pdf"
    _pdf_with_text(pdf, "Chemical Engineering Journal\nhttps://doi.org/10.1016/j.cej.2024.\n148878")
    assert sources._pdf_mentions_doi(pdf, "10.1016/j.cej.2024.148878")
    assert not sources._pdf_mentions_doi(pdf, "10.1016/j.cej.2024.157656")


def test_index_claims_block_other_dois(tmp_path):
    pdf = tmp_path / "Jiang2024_Lightweight.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    (tmp_path / ".doi_index.json").write_text(json.dumps(
        {"10.1016/j.cej.2024.148878": {"file": str(pdf), "source": "x", "strategy": "", "ts": 0}}),
        encoding="utf-8")
    claimed = sources._files_claimed_by_other_dois(tmp_path, "10.1016/j.cej.2024.157656")
    assert sources._norm_path(pdf) in claimed
    assert not sources._files_claimed_by_other_dois(tmp_path, "10.1016/j.cej.2024.148878")
