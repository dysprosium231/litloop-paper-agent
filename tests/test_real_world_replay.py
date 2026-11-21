"""Offline real-world HTTP regression tests using local recorded response fixtures."""

from __future__ import annotations

import functools
import http.server
import json
import socketserver
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

import pytest
import requests

from litloop import config as cfg_mod
from litloop import sources
from litloop.download import doi_check, pdf, pipeline
from litloop.identifiers import safe_filename
from litloop.sources import openalex, scihub, unpaywall

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "replay"
TARGET_DOI = "10.1038/s41598-024-67653-y"
TARGET_META = {
    "title": [
        "Improving thermal conductivity of AlSiC composites by post-oxidization of "
        "reaction-bonded silicon carbide preforms"
    ],
    "author": [{"family": "Lin"}],
    "year": "2024",
}


class _ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _ReplayServer:
    """Local HTTP/1.1 server serving recorded HTML, JSON, redirects, and slow PDFs."""

    def __init__(self) -> None:
        self.requests_log: list[tuple[str, str, dict[str, str]]] = []
        self._lock = threading.Lock()
        self.routes: dict[str, Any] = {}
        self._httpd = _ThreadedHTTPServer(("127.0.0.1", 0), self._make_handler())
        self.port = self._httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def _make_handler(self):
        srv = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                hdrs = {k.lower(): v for k, v in self.headers.items()}
                with srv._lock:
                    srv.requests_log.append(("GET", self.path, hdrs))
                handler = srv.routes.get(path) or srv.routes.get(urllib.parse.unquote(path))
                if handler is None:
                    body = b"not found"
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                handler(self)

        return _Handler

    def count(self, prefix: str) -> int:
        with self._lock:
            return sum(1 for _, p, _ in self.requests_log if p.startswith(prefix))

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=3)


def _send_bytes(handler: http.server.BaseHTTPRequestHandler, status: int, content_type: str, data: bytes) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _load_text_fixture(name: str, base_url: str) -> bytes:
    text = (FIXTURES_DIR / name).read_text(encoding="utf-8")
    return text.replace("{base}", base_url).encode("utf-8")


def _load_pdf_fixture(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


@pytest.fixture()
def replay_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate config/cache/DOI checks and rewrite known public hosts to local replay server."""
    srv = _ReplayServer()
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "out"
    cache_dir = data_dir / "cache"
    data_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(cfg_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", data_dir / "config.json")
    monkeypatch.setattr(sources, "DATA_DIR", data_dir)
    base_cfg = {
        **cfg_mod.DEFAULT_CONFIG,
        "output_dir": str(out_dir),
        "cache_dir": str(cache_dir),
        "email": "researcher@example.edu",
        "polite_delay_seconds": 0.0,
        "race_mode": "full",
        "scihub_enabled": False,
        "use_tor_for_scihub": False,
    }
    monkeypatch.setattr(cfg_mod, "load_config", lambda: dict(base_cfg))
    monkeypatch.setattr(sources, "load_config", lambda: dict(base_cfg))
    monkeypatch.setattr(doi_check, "registered", lambda doi, timeout=6.0: True)
    monkeypatch.setattr("litloop.identifiers.validate_doi", lambda doi: (True, f"https://doi.org/{doi}"))
    monkeypatch.setattr("litloop.citations.formats.fetch_metadata", lambda doi, config=None: dict(TARGET_META))
    sources._NEG_CACHE.clear()
    sources._SKIPPED.clear()
    sources._SETTLED.discard(TARGET_DOI)
    scihub._ABSENT_AT.clear()
    scihub._browser_domains.clear()
    monkeypatch.setitem(pdf._TITLE_CACHE, TARGET_DOI, TARGET_META["title"][0])
    pdf._PREPRINT_CACHE.clear()

    orig_request = requests.Session.request

    @functools.wraps(orig_request)
    def _rewritten_request(self, method: str, url: str, *args: Any, **kwargs: Any):
        rewrites = (
            ("https://api.unpaywall.org", f"{srv.base_url}/unpaywall"),
            ("https://api.openalex.org", f"{srv.base_url}/openalex-api"),
            ("https://content.openalex.org", f"{srv.base_url}/openalex-content"),
        )
        for src_prefix, dst_prefix in rewrites:
            if url.startswith(src_prefix):
                url = dst_prefix + url[len(src_prefix):]
                break
        return orig_request(self, method, url, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "request", _rewritten_request)
    try:
        yield srv, base_cfg, out_dir
    finally:
        srv.close()


def test_manifest_lists_all_replay_fixtures():
    manifest = json.loads((FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8"))
    for fname in manifest["fixtures"]:
        assert (FIXTURES_DIR / fname).is_file(), f"missing fixture: {fname}"


def test_race_filters_cloudflare_and_preview_and_adopts_redirected_vor(replay_env):
    srv, base_cfg, out_dir = replay_env
    vor_bytes = _load_pdf_fixture("vor_scientific_reports.pdf")
    prev_bytes = _load_pdf_fixture("preview_1page.pdf")
    cf_html = _load_text_fixture("scihub_cloudflare_403.html", srv.base_url)

    srv.routes["/cf"] = lambda h: _send_bytes(h, 403, "text/html; charset=utf-8", cf_html)
    srv.routes["/preview.pdf"] = lambda h: _send_bytes(h, 200, "application/pdf", prev_bytes)

    def _redirect_vor(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(302)
        h.send_header("Location", f"{srv.base_url}/repo/vor.pdf")
        h.send_header("Content-Length", "0")
        h.end_headers()

    srv.routes["/redir-vor.pdf"] = _redirect_vor
    srv.routes["/repo/vor.pdf"] = lambda h: _send_bytes(h, 200, "application/pdf", vor_bytes)

    def _cf_lane(doi, out_path, cfg):
        r = requests.get(f"{srv.base_url}/cf", timeout=5)
        return {"success": False, "error": f"HTTP {r.status_code} challenge", "error_type": "cloudflare_blocked"}

    def _preview_lane(doi, out_path, cfg):
        return pdf.download_pdf(f"{srv.base_url}/preview.pdf", out_path, cfg, "PreviewPub")

    def _vor_lane(doi, out_path, cfg):
        time.sleep(0.08)
        return pdf.download_pdf(f"{srv.base_url}/redir-vor.pdf", out_path, cfg, "OpenAlexOA")

    res = sources._run_tiers_parallel(
        [([(_cf_lane, "CFSource"), (_preview_lane, "PreviewPub"), (_vor_lane, "OpenAlexOA")], "Replay", 5)],
        TARGET_DOI,
        out_dir,
        out_dir / "out.pdf",
        base_cfg,
        False,
        5,
    )
    assert res is not None and res["success"] is True
    assert res["source"] == "OpenAlexOA"
    assert Path(res["file"]).read_bytes() == vor_bytes


def test_preprint_replay_prefers_version_of_record_over_earlier_preprint(replay_env):
    srv, base_cfg, out_dir = replay_env
    oa_json = _load_text_fixture("openalex_work_200.json", srv.base_url)
    rs_bytes = _load_pdf_fixture("preprint_research_square.pdf")
    vor_bytes = _load_pdf_fixture("vor_scientific_reports.pdf")

    srv.routes[f"/openalex-api/works/doi:{TARGET_DOI}"] = lambda h: _send_bytes(h, 200, "application/json", oa_json)

    def _content_redir(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(302)
        h.send_header("Location", f"{srv.base_url}/repo/rs.pdf")
        h.send_header("Content-Length", "0")
        h.end_headers()

    srv.routes["/openalex-content/works/W4399000001.pdf"] = _content_redir
    srv.routes["/repo/rs.pdf"] = lambda h: _send_bytes(h, 200, "application/pdf", rs_bytes)
    srv.routes["/pub/vor.pdf"] = lambda h: (time.sleep(0.25), _send_bytes(h, 200, "application/pdf", vor_bytes))[1]

    cfg = {**base_cfg, "openalex_api_key": "test-key"}

    def _pub_lane(doi, out_path, c):
        return pdf.download_pdf(f"{srv.base_url}/pub/vor.pdf", out_path, c, "Publisher")

    res = sources._run_tiers_parallel(
        [([(openalex.try_openalex_content_api, "OpenAlexContent"), (_pub_lane, "Publisher")], "Replay", 5)],
        TARGET_DOI,
        out_dir,
        out_dir / "out.pdf",
        cfg,
        False,
        5,
    )
    assert res is not None and res["success"] is True
    assert res["source"] == "Publisher"
    assert not res.get("preprint")
    assert Path(res["file"]).read_bytes() == vor_bytes


def test_preprint_replay_falls_back_and_tags_filename_when_only_preprint_available(replay_env, monkeypatch):
    srv, base_cfg, out_dir = replay_env
    oa_json = _load_text_fixture("openalex_work_200.json", srv.base_url)
    rs_bytes = _load_pdf_fixture("preprint_research_square.pdf")
    cf_html = _load_text_fixture("scihub_cloudflare_403.html", srv.base_url)

    srv.routes[f"/openalex-api/works/doi:{TARGET_DOI}"] = lambda h: _send_bytes(h, 200, "application/json", oa_json)
    srv.routes["/openalex-content/works/W4399000001.pdf"] = lambda h: _send_bytes(h, 200, "application/pdf", rs_bytes)
    srv.routes["/pub/blocked"] = lambda h: _send_bytes(h, 403, "text/html; charset=utf-8", cf_html)

    def _blocked_lane(doi, out_path, cfg):
        requests.get(f"{srv.base_url}/pub/blocked", timeout=5)
        return None

    cfg = {**base_cfg, "openalex_api_key": "test-key", "rename_format": "author_year_title_doi"}
    monkeypatch.setattr(sources, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(
        sources,
        "_build_free_sources",
        lambda doi, c: [(openalex.try_openalex_content_api, "OpenAlexContent"), (_blocked_lane, "BlockedPub")],
    )

    res = sources.download(TARGET_DOI, out_dir, _institutional=False)
    assert res["success"] is True
    assert res.get("preprint") == "Research Square"
    final_path = Path(res["file"])
    assert final_path.exists()
    assert final_path.name.endswith("_preprint.pdf")


def test_scihub_slow_mirror_streaming_waits_and_cleans_up_with_referer(replay_env):
    srv, base_cfg, out_dir = replay_env
    vor_bytes = _load_pdf_fixture("vor_scientific_reports.pdf")
    article_html = _load_text_fixture("scihub_article_200.html", srv.base_url)

    srv.routes["/m1"] = lambda h: _send_bytes(h, 200, "text/html", b"ok")
    srv.routes["/m2"] = lambda h: _send_bytes(h, 200, "text/html", b"ok")
    srv.routes[f"/m1/{TARGET_DOI}"] = lambda h: _send_bytes(h, 503, "text/plain", b"busy")
    srv.routes[f"/m2/{TARGET_DOI}"] = lambda h: _send_bytes(h, 200, "text/html; charset=utf-8", article_html)

    def _slow_pdf(handler: http.server.BaseHTTPRequestHandler) -> None:
        referer = handler.headers.get("Referer", "")
        ua = handler.headers.get("User-Agent", "")
        if f"/m2/{TARGET_DOI}" not in referer or not ua:
            _send_bytes(handler, 403, "text/plain", b"missing referer")
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "application/pdf")
        handler.send_header("Content-Length", str(len(vor_bytes)))
        handler.end_headers()
        first = 24576
        handler.wfile.write(vor_bytes[:first])
        handler.wfile.flush()
        offset = first
        step = max(8192, (len(vor_bytes) - first) // 4)
        while offset < len(vor_bytes):
            time.sleep(0.35)
            chunk = vor_bytes[offset:offset + step]
            handler.wfile.write(chunk)
            handler.wfile.flush()
            offset += step

    srv.routes["/cdn/slow_paper.pdf"] = _slow_pdf

    cfg = {
        **base_cfg,
        "scihub_enabled": True,
        "scihub_use_browser": False,
        "use_tor_for_scihub": False,
        "scihub_skip_from_year": 0,
        "scihub_domains": [f"{srv.base_url}/m1", f"{srv.base_url}/m2"],
        "scihub_backup_race_seconds": 0.65,
        "scihub_slow_pdf_seconds": 8.0,
        "scihub_slow_pdf_idle_seconds": 2.0,
    }
    out_file = out_dir / "scihub_slow.pdf"
    res = scihub.try_scihub(TARGET_DOI, out_file, cfg, use_tor=False)

    assert res is not None and res["success"] is True
    assert out_file.is_file() and out_file.read_bytes() == vor_bytes
    leftovers = [p.name for p in out_dir.iterdir() if p != out_file]
    assert leftovers == []


def test_late_file_landing_from_slow_http_adopted_without_re_downloading(replay_env, monkeypatch):
    srv, base_cfg, out_dir = replay_env
    vor_bytes = _load_pdf_fixture("vor_scientific_reports.pdf")
    srv.routes["/late/vor.pdf"] = lambda h: _send_bytes(h, 200, "application/pdf", vor_bytes)

    def _delayed_http_write() -> None:
        time.sleep(0.25)
        dest = out_dir / f"{safe_filename(TARGET_DOI)}_Publisher.pdf"
        pdf.download_pdf(f"{srv.base_url}/late/vor.pdf", dest, base_cfg, "Publisher")

    def _fake_fast_lane(entries, out, cfg, workers=8, progress=None):
        t = threading.Thread(target=_delayed_http_write, daemon=True)
        t.start()
        t.join(timeout=5)
        return [{"doi": TARGET_DOI, "success": False, "error": "timeout"}]

    retried: list[list[str]] = []
    monkeypatch.setattr(pipeline, "_enrich_oa_urls", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_run_fast_lane", _fake_fast_lane)
    monkeypatch.setattr(pipeline, "_fetch_si_for_results", lambda *a, **k: None)
    monkeypatch.setattr(
        pipeline,
        "_transient_retry",
        lambda results, *a, **k: retried.append([r["doi"] for r in results if not r.get("success")]),
    )

    rows = pipeline.run_lanes(
        [pipeline.QueueEntry(identifier=TARGET_DOI, channel="oa")],
        out_dir,
        base_cfg,
        allow_grey=False,
        allow_institution=False,
    )
    assert len(rows) == 1 and rows[0]["success"] is True
    assert Path(rows[0]["file"]).read_bytes() == vor_bytes
    assert srv.count("/late/vor.pdf") == 1
    assert retried == [[]]


def test_wrong_paper_over_real_http_is_rejected_and_removed(replay_env):
    srv, base_cfg, out_dir = replay_env
    wrong_bytes = _load_pdf_fixture("wrong_paper.pdf")
    srv.routes["/wrong.pdf"] = lambda h: _send_bytes(h, 200, "application/pdf", wrong_bytes)

    def _wrong_lane(doi, out_path, cfg):
        return pdf.download_pdf(f"{srv.base_url}/wrong.pdf", out_path, cfg, "WrongSource")

    candidate = out_dir / "wrong_candidate.pdf"
    res = sources._try_source(_wrong_lane, TARGET_DOI, candidate, base_cfg, "WrongSource")
    assert res is not None and res["success"] is False
    assert res["error_type"] == "wrong_paper"

    race_res = sources._run_tiers_parallel(
        [([(_wrong_lane, "WrongSource")], "Replay", 5)],
        TARGET_DOI,
        out_dir,
        out_dir / "out.pdf",
        base_cfg,
        False,
        5,
    )
    assert race_res is None and not (out_dir / "out.pdf").exists()


def test_unpaywall_real_http_repo_priority_and_429_rate_limit(replay_env):
    srv, base_cfg, out_dir = replay_env
    upw_200 = _load_text_fixture("unpaywall_200.json", srv.base_url)
    upw_429 = _load_text_fixture("unpaywall_429.json", srv.base_url)
    vor_bytes = _load_pdf_fixture("vor_scientific_reports.pdf")
    prev_bytes = _load_pdf_fixture("preview_1page.pdf")

    srv.routes[f"/unpaywall/v2/{TARGET_DOI.replace('/', '%2F')}"] = lambda h: _send_bytes(
        h, 200, "application/json", upw_200
    )
    srv.routes["/repo/vor.pdf"] = lambda h: _send_bytes(h, 200, "application/pdf", vor_bytes)
    srv.routes["/pub/preview.pdf"] = lambda h: _send_bytes(h, 200, "application/pdf", prev_bytes)

    out_pdf = out_dir / "upw.pdf"
    res = unpaywall.try_unpaywall(TARGET_DOI, out_pdf, base_cfg)
    assert res is not None and res["success"] is True
    assert out_pdf.read_bytes() == vor_bytes
    assert srv.count("/pub/preview.pdf") == 0

    srv.routes[f"/unpaywall/v2/{TARGET_DOI.replace('/', '%2F')}"] = lambda h: _send_bytes(
        h, 429, "application/json", upw_429
    )
    rate_res = unpaywall.try_unpaywall(TARGET_DOI, out_dir / "upw_429.pdf", base_cfg)
    assert rate_res is not None and rate_res["success"] is False
    assert rate_res["error_type"] == "rate_limited"
