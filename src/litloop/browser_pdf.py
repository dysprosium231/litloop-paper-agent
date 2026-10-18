"""Browser-side helpers shared by the institutional download flows (CARSI, WebVPN).

Publisher PDFs opened in a visible browser often end up in Chrome's inline PDF
viewer (e.g. ScienceDirect: pdfft -> pdf.sciencedirectassets.com). The response
listener cannot read that body, an out-of-browser request is challenged by
Cloudflare, and a page fetch() cannot read a cross-origin redirect. So the browser
itself must open the URL, and the body is read while the response is paused via
the CDP Fetch domain.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from .log import get_logger

log = get_logger()

_LOADING_TITLES = ("loading", "请稍候", "please wait", "just a moment", "redirecting")
_FINAL_BLOCK_MARKERS = ("浏览器不支持", "browser is not supported", "unsupported browser",
                        "your browser is not supported")

PAGE_PDF_LINK_JS = """
(doi) => {
    // "Recommended articles" lists may carry other papers' PDF links: prefer a
    // link naming this DOI, else fall back to the first one in page order
    // (the article's own button comes first; some URLs, e.g. ACS
    // /article-pdf/.../am5c16640.pdf, do not contain the DOI).
    const want = (doi || '').toLowerCase();
    const suffix = want.includes('/') ? want.split('/').slice(1).join('/') : '';
    let first = null;
    for (const a of document.querySelectorAll('a[href]')) {
        const href = a.getAttribute('href') || '';
        const low = href.toLowerCase();
        if (low.includes('pdfft') || low.includes('/doi/pdf') || low.includes('/pdfdirect/')
            || low.includes('/content/pdf/') || low.includes('/stamp/stamp.jsp')
            // Silverchair platforms (RSC since 2026, OUP): /article-pdf/doi/...pdf
            || low.includes('/article-pdf/') || low.includes('/content/articlepdf/')
            // SciOpen: /local/article_pdf/10.26599/JAC....pdf
            || low.includes('/article_pdf/')) {
            let abs;
            try { abs = new URL(href, location.href).href; } catch (e) { continue; }
            if (!want) return abs;
            first = first || abs;
            let plain = abs.toLowerCase();
            try { plain = decodeURIComponent(plain); } catch (e) {}
            if (plain.includes(want) || (suffix && plain.includes(suffix))) return abs;
        }
    }
    if (first) return first;
    // The button itself: "View PDF" / "Download PDF" whose link carries none
    // of the patterns above (2026-10-17: ScienceDirect reached outside the
    // WebVPN showed "View PDF" yet no link was found).
    for (const a of document.querySelectorAll('a[href]')) {
        const text = (a.innerText || a.getAttribute('aria-label') || '').trim().toLowerCase();
        if (/^(view|download|get) pdf\\b|^pdf$|查看 ?pdf|下载 ?pdf/.test(text)) {
            try { return new URL(a.getAttribute('href'), location.href).href; } catch (e) {}
        }
    }
    return null;
}
"""


def wait_page_settled(page: Any, max_wait_s: float = 30.0) -> tuple[str, str]:
    """Wait until the page leaves interstitial/redirect states; return (title, url).

    Interstitials such as WebVPN's "Loading https://..." page or a Cloudflare
    "请稍候" page carry no auth keywords, so a single early look at the title
    misreads where the browser is really heading.
    """
    title, url = "", ""
    waited = 0.0
    while waited < max_wait_s:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        try:
            title = page.title() or ""
            url = page.url or ""
        except Exception:
            title, url = "", ""
        low = title.strip().lower()
        if low and not any(low.startswith(t) or t in low for t in _LOADING_TITLES):
            return title, url
        # Cloudflare's "unsupported browser" verdict keeps the "请稍候" title
        # forever (e.g. a challenge rewritten by a WebVPN); waiting is futile.
        try:
            body = (page.evaluate("document.body ? document.body.innerText.slice(0, 2000) : ''") or "").lower()
            if any(m in body for m in _FINAL_BLOCK_MARKERS):
                return title, url
        except Exception:
            pass
        # Not page.wait_for_timeout: on a page stuck loading (2026-10-17,
        # auth.elsevier.com reached through WebVPN) it never returned.
        time.sleep(1.0)
        waited += 1.0
    return title, url


_TRANSIT_HOSTS = ("id.elsevier.com", "auth.elsevier.com", "linkinghub.elsevier.com",
                  "idp.", "sso.", "wayf.", "shibboleth")


def wait_url_stable(page: Any, max_wait_s: float = 45.0) -> str:
    """Wait until the page stops redirecting; return the final URL.

    Publisher pages bounce through auth/redirect hosts (e.g. ScienceDirect ->
    id.elsevier.com OAuth -> back) after DOMContentLoaded. Reading the DOM during
    those hops fails with "Execution context was destroyed".
    """
    prev = ""
    waited = 0.0
    while waited < max_wait_s:
        try:
            url = page.url or ""
            state = page.evaluate("document.readyState")
        except Exception:
            url, state = "", ""
        host = url.split("/")[2] if url.count("/") >= 2 else ""
        in_transit = any(t in host for t in _TRANSIT_HOSTS)
        # "interactive" (DOM parsed) is enough: pages with ads/trackers (e.g.
        # Wiley) may never reach "complete", and JS-rendered PDF links are
        # waited for separately by find_page_pdf_link.
        if url and url == prev and state in ("interactive", "complete") and not in_transit:
            return url
        prev = url
        try:
            page.wait_for_timeout(1000)
        except Exception:
            return url
        waited += 1.0
    return prev


def find_page_pdf_link(page: Any, max_wait_s: float = 15.0, doi: str = "") -> str | None:
    """Return the article page's own PDF link (carries page-issued tokens), if any.

    Publisher pages (e.g. ScienceDirect) render the "View PDF" link with JS after
    DOMContentLoaded, so poll for it instead of looking once.
    """
    # Wall-clock bound: an evaluate() on a page that is still loading can itself
    # take seconds, so counting iterations under-waits on slow sites.
    deadline = time.monotonic() + max_wait_s
    while time.monotonic() < deadline:
        try:
            href = page.evaluate(PAGE_PDF_LINK_JS, doi or "")
            if href:
                return href
        except Exception:
            pass
        time.sleep(0.5)  # not page.wait_for_timeout: it hangs on a page stuck loading
    return None


def _is_bounce_to_html(ev: dict[str, Any], pdf_url: str) -> bool:
    """A 2xx HTML document at a URL without "pdf" in it, reached while loading a PDF URL.

    ScienceDirect's /pdfft first answers with an HTML check page but stays on a
    pdfft URL, so it is not mistaken for a bounce.
    """
    status = ev.get("responseStatusCode") or 0
    headers = {h["name"].lower(): h["value"] for h in ev.get("responseHeaders") or []}
    req_url = str((ev.get("request") or {}).get("url", ""))
    return (200 <= status < 300 and "text/html" in headers.get("content-type", "").lower()
            and "pdf" not in req_url.lower() and req_url != pdf_url)


def capture_pdf_via_cdp(context: Any, page: Any, url: str, timeout_s: float = 90.0) -> bytes | None:
    """Navigate ``page`` to ``url`` and return the PDF body the browser receives."""
    if not url or not url.startswith("http"):
        return None
    try:
        cdp = context.new_cdp_session(page)
    except Exception as exc:
        log.info(f"   [browser-pdf] CDP session unavailable: {exc}")
        return None

    bodies: list[bytes] = []
    # The sync API cannot call back into the browser from an event handler, so the
    # handler only queues paused requests; the loop below (pumping events via
    # wait_for_timeout) reads and releases them.
    paused: list[dict[str, Any]] = []

    def _handle(ev: dict[str, Any]) -> None:
        rid = ev["requestId"]
        try:
            status = ev.get("responseStatusCode") or 0
            headers = {h["name"].lower(): h["value"] for h in ev.get("responseHeaders") or []}
            ctype = headers.get("content-type", "").lower()
            if 200 <= status < 300 and ("pdf" in ctype or "octet-stream" in ctype):
                got = cdp.send("Fetch.getResponseBody", {"requestId": rid})
                raw = got.get("body", "")
                bodies.append(base64.b64decode(raw) if got.get("base64Encoded") else raw.encode("latin-1"))
        except Exception as exc:
            log.info(f"   [browser-pdf] CDP body read error: {exc}")
        finally:
            try:
                cdp.send("Fetch.continueRequest", {"requestId": rid})
            except Exception:
                pass

    try:
        cdp.on("Fetch.requestPaused", lambda ev: paused.append(ev))
        cdp.send("Fetch.enable", {"patterns": [
            {"urlPattern": "*", "resourceType": "Document", "requestStage": "Response"}
        ]})
        try:
            page.evaluate("(u) => { window.location.href = u; }", url)
        except Exception:
            pass  # context may be torn down by the navigation itself
        # Large PDFs behind ScienceDirect's crasolve check can take ~1 min; bound
        # the wait by wall-clock time rather than by iterations.
        deadline = time.monotonic() + timeout_s
        bounced_at = 0.0
        while time.monotonic() < deadline:
            page.wait_for_timeout(500)
            while paused:
                ev = paused.pop(0)
                if _is_bounce_to_html(ev, url):
                    bounced_at = bounced_at or time.monotonic()
                _handle(ev)
            if bodies:
                break
            # The PDF URL redirected to an ordinary page (no entitlement: Nature
            # sends .pdf back to the article). Waiting the full timeout for a
            # PDF that will never come cost 90 s per attempt.
            if bounced_at and time.monotonic() - bounced_at > 3:
                log.info(f"   [browser-pdf] PDF URL redirected to an HTML page, not entitled: {page.url[:60]}")
                break
    finally:
        try:
            cdp.send("Fetch.disable")
            cdp.detach()
        except Exception:
            pass

    for body in bodies:
        if len(body) > 5000 and body[:5] == b"%PDF-":
            log.info(f"   [browser-pdf] PDF captured via CDP: {len(body)} bytes")
            return body
    log.info(f"   [browser-pdf] no PDF body captured for {url[:60]} (now at {page.url[:60]})")
    return None
