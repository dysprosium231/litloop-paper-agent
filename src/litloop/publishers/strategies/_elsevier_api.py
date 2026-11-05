"""ScienceDirect's article API: ask it for the PDF attachment.

Split out of `_core.py` (2026-11-05): it is the largest self-contained chunk
there — no browser, no DOM, just requests and XML. `_core.py` re-exports the
names below, so callers keep importing from there.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ...log import get_logger

log = get_logger()


def _persist_api_cookies(session: Any, config: dict[str, Any]) -> int:
    """Delegates to `_core`: the cookie store lives with the browser code."""
    from ._core import _persist_api_cookies as impl

    return impl(session, config)


def _elsevier_header(headers: Any, name: str) -> str:
    """Return a response header value using case-insensitive matching."""
    if not headers:
        return ""
    value = headers.get(name)
    if value is not None:
        return str(value)
    needle = name.lower()
    for key, value in headers.items():
        if str(key).lower() == needle:
            return str(value)
    return ""


def _elsevier_response_is_pdf(resp: Any) -> bool:
    content_type = _elsevier_header(getattr(resp, "headers", {}), "content-type").lower()
    content = getattr(resp, "content", b"") or b""
    return "pdf" in content_type or content[:5] == b"%PDF-"


def _elsevier_response_is_xml(resp: Any) -> bool:
    content_type = _elsevier_header(getattr(resp, "headers", {}), "content-type").lower()
    content = (getattr(resp, "content", b"") or b"").lstrip()
    return (
        "xml" in content_type
        or content.startswith(b"<?xml")
        or content.startswith(b"<full-text-retrieval-response")
    )


def _elsevier_response_text(resp: Any) -> str:
    text = getattr(resp, "text", None)
    if isinstance(text, str):
        return text
    content = getattr(resp, "content", b"") or b""
    return content.decode("utf-8", errors="replace")


def _elsevier_xml_local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1].split(":", 1)[-1].lower()


def _elsevier_element_text(el: Any) -> str:
    return " ".join(" ".join(el.itertext()).split())


def _elsevier_looks_like_pdf_eid(value: str) -> bool:
    lowered = value.strip().lower()
    return bool(lowered) and (lowered.endswith(".pdf") or ".pdf" in lowered)


def _elsevier_article_eid_to_main_pdf(value: str) -> str:
    candidate = value.strip()
    if candidate.lower().startswith("eid:"):
        candidate = candidate.split(":", 1)[1].strip()
    if not candidate.startswith("1-s2.0-"):
        return ""
    if candidate.lower().endswith(".pdf"):
        return candidate
    return f"{candidate}-main.pdf"


def _elsevier_attachment_container(el: Any, parent_map: dict[Any, Any]) -> Any:
    node = parent_map.get(el, el)
    while node is not None:
        local = _elsevier_xml_local_name(str(node.tag))
        if "attachment" in local or "object" in local:
            return node
        node = parent_map.get(node)
    return parent_map.get(el, el)


def _elsevier_attachment_metadata(el: Any) -> str:
    parts: list[str] = []
    for node in el.iter():
        local = _elsevier_xml_local_name(str(node.tag))
        text = _elsevier_element_text(node)
        if text:
            parts.append(f"{local}:{text}")
        for attr_name, attr_value in node.attrib.items():
            attr_local = _elsevier_xml_local_name(str(attr_name))
            if attr_value:
                parts.append(f"{attr_local}:{attr_value}")
    return " ".join(parts).lower()


def _elsevier_attachment_score(eid: str, metadata: str) -> int:
    haystack = f"{eid} {metadata}".lower()
    score = 0
    if eid.lower().endswith(".pdf"):
        score += 20
    if "pdf" in haystack:
        score += 10
    if "main" in haystack or "full-text" in haystack or "fulltext" in haystack:
        score += 100
    if "page-count" in haystack or "pages" in haystack:
        score += 5
    if "attachment-size" in haystack or "filesize" in haystack or "file-size" in haystack:
        score += 5
    if any(marker in haystack for marker in (
        "supplement", "supplementary", "mmc", "appendix", "graphical", "thumbnail",
    )):
        score -= 100
    return score


def _extract_elsevier_pdf_attachment_eids(xml_text: str) -> list[str]:
    """Extract candidate publisher PDF object EIDs from Elsevier full-text XML."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.info(f"   [ElsevierAPI] XML parse failed: {exc}")
        return []

    parent_map = {child: parent for parent in root.iter() for child in parent}
    candidates: list[tuple[int, int, str]] = []
    seen: set[str] = set()

    for el in root.iter():
        found: list[str] = []
        local = _elsevier_xml_local_name(str(el.tag))
        if local in {"attachment-eid", "object-eid"}:
            text = _elsevier_element_text(el)
            if _elsevier_looks_like_pdf_eid(text):
                found.append(text)
        elif local in {"eid", "identifier"}:
            text = _elsevier_element_text(el)
            main_pdf = _elsevier_article_eid_to_main_pdf(text)
            if main_pdf:
                found.append(main_pdf)

        for attr_name, attr_value in el.attrib.items():
            attr_local = _elsevier_xml_local_name(str(attr_name))
            if attr_local in {"attachment-eid", "object-eid", "eid"}:
                value = str(attr_value).strip()
                if _elsevier_looks_like_pdf_eid(value):
                    found.append(value)
                else:
                    main_pdf = _elsevier_article_eid_to_main_pdf(value)
                    if main_pdf:
                        found.append(main_pdf)

        for eid in found:
            if eid in seen:
                continue
            seen.add(eid)
            container = _elsevier_attachment_container(el, parent_map)
            metadata = _elsevier_attachment_metadata(container)
            candidates.append((
                _elsevier_attachment_score(eid, metadata),
                len(candidates),
                eid,
            ))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [eid for _, _, eid in candidates]


def _elsevier_pdf_page_count(content: bytes) -> int | None:
    """Best-effort page count for detecting Elsevier one-page preview PDFs."""
    try:
        import fitz  # type: ignore[import-not-found]
    except Exception:
        return None

    try:
        with fitz.open(stream=content, filetype="pdf") as doc:
            return int(doc.page_count)
    except Exception:
        return None


def _save_elsevier_pdf_content(
    doi: str,
    output_path: Path,
    content: bytes,
    config: dict[str, Any],
    source: str,
    *,
    reject_single_page: bool = False,
) -> dict[str, Any] | None:
    from ...download.pdf import is_pdf_file, success

    if len(content) < config.get("min_pdf_size_bytes", 10000):
        log.info(f"   [ElsevierAPI] response too small ({len(content)} bytes)")
        return None
    if reject_single_page:
        page_count = _elsevier_pdf_page_count(content)
        if page_count == 1:
            output_path.unlink(missing_ok=True)
            log.info(f"   [ElsevierAPI] direct PDF is a 1-page preview for {doi}")
            return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(content)
    if is_pdf_file(output_path):
        log.info(f"   [ElsevierAPI] downloaded {len(content)} bytes for {doi}")
        return success(doi, output_path, source)

    output_path.unlink(missing_ok=True)
    return None


def _try_elsevier_object_pdf_from_xml(
    doi: str,
    output_path: Path,
    config: dict[str, Any],
    session: Any,
    article_url: str,
    headers: dict[str, str],
    first_resp: Any,
    *,
    full_xml_already_tried: bool = False,
) -> dict[str, Any] | None:
    import urllib.parse

    xml_resp = None
    if (
        first_resp is not None
        and getattr(first_resp, "status_code", 0) == 200
        and _elsevier_response_is_xml(first_resp)
    ):
        xml_resp = first_resp

    if xml_resp is None:
        xml_headers = dict(headers)
        xml_headers["Accept"] = "application/xml"
        param_options = [None] if full_xml_already_tried else [{"view": "FULL"}, None]
        for params in param_options:
            try:
                request_kwargs: dict[str, Any] = {
                    "headers": xml_headers,
                    "timeout": 30,
                    "allow_redirects": True,
                }
                if params:
                    request_kwargs["params"] = params
                candidate = session.get(article_url, **request_kwargs)
            except Exception as exc:
                log.info(f"   [ElsevierAPI] XML request failed: {exc}")
                continue

            if getattr(candidate, "status_code", 0) != 200:
                view_name = "FULL XML" if params else "XML"
                log.info(
                    f"   [ElsevierAPI] {view_name} HTTP "
                    f"{getattr(candidate, 'status_code', 0)} for {doi}"
                )
                continue
            if not _elsevier_response_is_xml(candidate):
                content_type = _elsevier_header(
                    getattr(candidate, "headers", {}),
                    "content-type",
                )
                view_name = "FULL XML" if params else "XML"
                log.info(
                    f"   [ElsevierAPI] {view_name} request returned non-XML "
                    f"({content_type[:50]})"
                )
                continue

            xml_resp = candidate
            break

        if xml_resp is None:
            return None

    eids = _extract_elsevier_pdf_attachment_eids(_elsevier_response_text(xml_resp))
    if not eids:
        log.info(f"   [ElsevierAPI] XML has no PDF attachment EID for {doi}")
        return None

    object_headers = dict(headers)
    object_headers["Accept"] = "application/pdf"
    for eid in eids:
        object_url = (
            "https://api.elsevier.com/content/object/eid/"
            f"{urllib.parse.quote(eid, safe='')}"
        )
        try:
            object_resp = session.get(
                object_url,
                headers=object_headers,
                timeout=30,
                allow_redirects=True,
            )
        except Exception as exc:
            log.info(f"   [ElsevierAPI] object {eid} request failed: {exc}")
            continue

        if getattr(object_resp, "status_code", 0) != 200:
            log.info(
                f"   [ElsevierAPI] object {eid} HTTP "
                f"{getattr(object_resp, 'status_code', 0)}"
            )
            continue
        if not _elsevier_response_is_pdf(object_resp):
            content_type = _elsevier_header(
                getattr(object_resp, "headers", {}),
                "content-type",
            )
            log.info(f"   [ElsevierAPI] object {eid} returned non-PDF ({content_type[:50]})")
            continue

        result = _save_elsevier_pdf_content(
            doi,
            output_path,
            getattr(object_resp, "content", b"") or b"",
            config,
            "ElsevierAPI",
            reject_single_page=True,
        )
        if result:
            log.info(f"   [ElsevierAPI] downloaded object PDF via attachment EID {eid}")
            return result

    return None


def _try_elsevier_text_fallback(
    doi: str,
    output_path: Path,
    session: Any,
    article_url: str,
    headers: dict[str, str],
    config: dict[str, Any],
) -> dict[str, Any] | None:
    """Last API resort: save the httpAccept=text/plain full text as .txt.

    Only kept when the body is a real plain-text payload above 100KB — the
    same threshold pdf_utils.is_suspicious_pdf treats as certainly full text —
    so the racing pipeline never flags or drops the artifact. API error
    payloads (text/xml) are rejected by the content-type check.
    """
    txt_headers = dict(headers)
    txt_headers["Accept"] = "text/plain"
    try:
        resp = session.get(
            article_url,
            headers=txt_headers,
            params={"httpAccept": "text/plain"},
            timeout=30,
            allow_redirects=True,
        )
    except Exception as e:
        log.info(f"   [ElsevierAPI] text/plain request failed: {e}")
        return None

    if getattr(resp, "status_code", 0) != 200:
        return None
    content_type = _elsevier_header(getattr(resp, "headers", {}), "content-type").lower()
    content = getattr(resp, "content", b"") or b""
    if "text/plain" not in content_type or len(content) < 100_000:
        log.info(
            f"   [ElsevierAPI] no usable full text ({content_type[:40]}, "
            f"{len(content)} bytes)"
        )
        return None

    txt_path = output_path.with_suffix(".txt")
    try:
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_bytes(content)
    except OSError as e:
        log.info(f"   [ElsevierAPI] full text save failed: {e}")
        return None
    log.info(f"   [ElsevierAPI] saved {len(content)} bytes of full text as {txt_path.name}")
    from ...download.pdf import success
    return success(doi, txt_path, "ElsevierAPI(Text)")


def try_elsevier_api(
    doi: str, output_path: Path, config: dict[str, Any],
) -> dict[str, Any] | None:
    from ...sources.elsevier_api import fulltext_enabled
    if not fulltext_enabled(config):
        return None  # off in settings, or this key has no full-text entitlement
    return _try_elsevier_api(doi, output_path, config)


def _try_elsevier_api(
    doi: str, output_path: Path, config: dict[str, Any],
) -> dict[str, Any] | None:
    """Download Elsevier/ScienceDirect PDF via Article Retrieval API.

    Uses the Elsevier Institutional API (api.elsevier.com) with an API key
    and optional institutional token. This is far faster and more reliable
    than browser-based login flows.

    API-first request order per route (browser navigation is only a last
    resort and lives in try_elsevier_browser):
      1. article endpoint with Accept: application/pdf — the full PDF when
         the key's entitlement covers the article. Entitlement follows the
         API key, not the client IP, so this works off-campus. The body is
         validated as a real multi-page PDF before saving (rejects the
         1-page preview served to unentitled keys).
      2. FULL XML → main-PDF attachment EIDs → Content Object API
         (existing chain; covers articles whose direct endpoint misbehaves).
      3. httpAccept=text/plain full text saved as .txt when no PDF is
         returned at all.

    When the API does not return a PDF directly (no institutional access),
    cookies from the request chain (api.elsevier.com → linkinghub.elsevier.com
    → sciencedirect.com) are persisted for the browser strategy to reuse.

    Config keys:
        elsevier_api_key   — personal or institutional API key (required);
                             falls back to the ELSEVIER_API_KEY env var
        elsevier_insttoken — institutional token for campus-level access
    """
    api_key = config.get("elsevier_api_key", "") or os.environ.get("ELSEVIER_API_KEY", "")
    if not api_key:
        log.info(f"   [ElsevierAPI] no API key configured, skipping. "
                 f"Run litloop_elsevier_setup to configure (free).")
        return None

    from ...network import USER_AGENT, proxy_dict, select_proxy_for_url

    url = f"https://api.elsevier.com/content/article/doi/{doi}"

    headers: dict[str, str] = {
        "User-Agent": USER_AGENT,
        "X-ELS-APIKey": api_key,
    }
    insttoken = config.get("elsevier_insttoken", "")
    if insttoken:
        headers["X-ELS-InstToken"] = insttoken

    proxy = select_proxy_for_url(url, config)
    configured_proxies = proxy_dict(proxy)
    route_options: list[tuple[str, dict[str, str]]] = [("direct", {})]
    if configured_proxies:
        route_options.append(("configured proxy", configured_proxies))

    import requests

    for route_name, proxies in route_options:
        session = requests.Session()
        session.trust_env = False
        if proxies:
            session.proxies = proxies

        # Step 1: direct article PDF — entitlement follows the API key, so no
        # campus IP is needed. Reject non-200 and 1-page preview responses.
        pdf_headers = dict(headers)
        pdf_headers["Accept"] = "application/pdf"
        pdf_resp = None
        try:
            log.info(f"   [ElsevierAPI] trying {route_name} route")
            pdf_resp = session.get(
                url,
                headers=pdf_headers,
                timeout=30,
                allow_redirects=True,
            )
        except Exception as e:
            log.info(f"   [ElsevierAPI] {route_name} direct PDF request failed: {e}")

        if pdf_resp is not None and pdf_resp.status_code == 200 and _elsevier_response_is_pdf(pdf_resp):
            result = _save_elsevier_pdf_content(
                doi,
                output_path,
                pdf_resp.content,
                config,
                "ElsevierAPI",
                reject_single_page=True,
            )
            if result:
                return result
        elif pdf_resp is not None and pdf_resp.status_code != 200:
            log.info(
                f"   [ElsevierAPI] {route_name} direct PDF HTTP "
                f"{pdf_resp.status_code} for {doi}, trying FULL XML route"
            )

        try:
            xml_headers = dict(headers)
            xml_headers["Accept"] = "application/xml"
            resp = session.get(
                url,
                headers=xml_headers,
                params={"view": "FULL"},
                timeout=30,
                allow_redirects=True,
            )
        except Exception as e:
            log.info(f"   [ElsevierAPI] {route_name} FULL XML request failed: {e}")
            continue

        if resp is not None and resp.status_code != 200:
            if resp.status_code in (403, 429):
                log.info(
                    f"   [ElsevierAPI] {route_name} HTTP {resp.status_code}; "
                    "API access may be unavailable or rate limited"
                )
            else:
                log.info(f"   [ElsevierAPI] {route_name} HTTP {resp.status_code} for {doi}")

        if resp is not None and resp.status_code == 200 and _elsevier_response_is_pdf(resp):
            result = _save_elsevier_pdf_content(
                doi,
                output_path,
                resp.content,
                config,
                "ElsevierAPI",
                reject_single_page=True,
            )
            if result:
                try:
                    _persist_api_cookies(session, config)
                except Exception:
                    pass
                return result

        result = _try_elsevier_object_pdf_from_xml(
            doi,
            output_path,
            config,
            session,
            url,
            headers,
            resp,
            full_xml_already_tried=True,
        )
        if result:
            try:
                _persist_api_cookies(session, config)
            except Exception:
                pass
            return result

        content_type = _elsevier_header(
            getattr(resp, "headers", {}) if resp is not None else {},
            "content-type",
        )
        log.info(
            f"   [ElsevierAPI] {route_name} non-PDF response "
            f"({content_type[:50]}), trying next route if available"
        )
        try:
            _persist_api_cookies(session, config)
        except Exception as e:
            log.info(f"   [ElsevierAPI] cookie persist failed: {e}")

        # Step 3: full text as .txt — the API returned no PDF deliverable for
        # this DOI; grab the plain-text full text before falling back further.
        txt_result = _try_elsevier_text_fallback(doi, output_path, session, url, headers, config)
        if txt_result:
            return txt_result

    return None
