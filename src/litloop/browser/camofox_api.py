"""CamoFox cloud-API client — NOT the local camoufox browser backend.

``camofox_*`` config keys (``camofox_enabled``, ``camofox_url``,
``camofox_api_key``, ``camofox_access_key``) belong to THIS module: a
remote stealth-browser service for Cloudflare bypass. The local
anti-detect browser backend is ``camoufox`` (one "u") — see
``browser_backend.py`` and the ``browser_backend`` config key. The two
names are unfortunately close; they share no code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import urllib3

from ..log import get_logger

log = get_logger()

_USER_ID = "litloop"
_SESSION_KEY = "download"

_DEFAULT_TIMEOUT = 30.0


def _build_headers(config: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json"}
    access_key = config.get("camofox_access_key", "")
    api_key = config.get("camofox_api_key", "")
    if access_key:
        headers["Authorization"] = f"Bearer {access_key}"
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _api_request(
    config: dict[str, Any],
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    url = config.get("camofox_url", "http://localhost:9377").rstrip("/") + path
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode(params)

    headers = _build_headers(config)
    body_bytes = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        body_bytes = json.dumps(body).encode("utf-8")

    retry = urllib3.Retry(total=2, backoff_factor=1.0, status_forcelist=[503])
    pool = urllib3.PoolManager(retries=retry)
    resp = pool.request(
        method, url,
        headers=headers, body=body_bytes,
        timeout=urllib3.Timeout(connect=timeout, read=timeout),
    )
    payload = json.loads(resp.data.decode("utf-8"))
    if resp.status >= 400:
        error_msg = payload.get("error", "Unknown error") if isinstance(payload, dict) else str(payload)
        raise RuntimeError(f"Camofox {resp.status}: {error_msg}")
    return payload if isinstance(payload, dict) else {"result": payload}


def is_available(config: dict[str, Any]) -> bool:
    """Check if camofox-browser is reachable."""
    url = config.get("camofox_url", "")
    if not url:
        return False
    try:
        _api_request(config, "GET", "/health", timeout=5.0)
        return True
    except Exception:
        return False


def solve_url(
    url: str,
    config: dict[str, Any],
    *,
    max_timeout: int = 60000,
) -> dict[str, Any] | None:
    """Fetch URL via camofox-browser. Returns dict with status/solution keys.

    Returns None on failure.
    """
    tab_id: str | None = None
    timeout_sec = max_timeout / 1000.0
    try:
        # Create tab and navigate
        result = _api_request(
            config, "POST", "/tabs",
            body={"userId": _USER_ID, "sessionKey": _SESSION_KEY, "url": url},
            timeout=timeout_sec,
        )
        tab_id = result.get("tabId")
        if not tab_id:
            log.info("camofox: no tabId returned")
            return None

        # Get snapshot for final URL
        snapshot = _api_request(
            config, "GET", f"/tabs/{tab_id}/snapshot",
            params={"userId": _USER_ID},
            timeout=timeout_sec,
        )
        final_url = snapshot.get("url", url)

        # Extract HTML via JS evaluation
        html = ""
        try:
            eval_result = _api_request(
                config, "POST", f"/tabs/{tab_id}/evaluate",
                body={"userId": _USER_ID, "expression": "document.documentElement.outerHTML"},
                timeout=15.0,
            )
            html = eval_result.get("result", "")
        except Exception as e:
            log.info(f"camofox: HTML eval failed: {e}")

        if not html:
            snapshot_text = snapshot.get("snapshot", "")
            html = f"<html><body><pre>{snapshot_text}</pre></body></html>"

        # Extract cookies via JS
        cookies: list[dict[str, str]] = []
        try:
            cookie_result = _api_request(
                config, "POST", f"/tabs/{tab_id}/evaluate",
                body={
                    "userId": _USER_ID,
                    "expression": (
                        "document.cookie.split(';').map(c => c.trim()).filter(Boolean).map(c => { "
                        "const i = c.indexOf('='); "
                        "return {name: c.substring(0, i), value: c.substring(i+1)}; "
                        "})"
                    ),
                },
                timeout=10.0,
            )
            raw = cookie_result.get("result", [])
            if isinstance(raw, list):
                for c in raw:
                    if isinstance(c, dict) and c.get("name"):
                        cookies.append({"name": c["name"], "value": c.get("value", "")})
        except Exception as e:
            log.info(f"camofox: cookie extraction failed: {e}")

        log.info(f"camofox: ok, final_url={final_url}, html_len={len(html)}")
        return {
            "status": "ok",
            "solution": {
                "url": final_url,
                "status": 200,
                "response": html,
                "cookies": cookies,
            },
        }
    except Exception as e:
        log.info(f"camofox: error - {e}")
        return None
    finally:
        if tab_id:
            try:
                _api_request(
                    config, "DELETE", f"/tabs/{tab_id}",
                    params={"userId": _USER_ID},
                    timeout=5.0,
                )
            except Exception:
                pass


def get_html(
    url: str,
    config: dict[str, Any],
    *,
    max_timeout: int = 60000,
) -> str | None:
    """Solve and return page HTML."""
    result = solve_url(url, config, max_timeout=max_timeout)
    if not result:
        return None
    return result.get("solution", {}).get("response")


def import_cookies(cookie_file: str | Path, config: dict[str, Any], *, domain_suffix: str | None = None) -> int:
    """Import Netscape-format cookies into camofox-browser. Returns count imported."""
    cookie_path = Path(cookie_file)
    if not cookie_path.exists():
        raise FileNotFoundError(f"Cookie file not found: {cookie_path}")

    text = cookie_path.read_text(encoding="utf-8")
    cookies = _parse_netscape_cookies(text)
    if domain_suffix:
        cookies = [c for c in cookies if c.get("domain", "").endswith(domain_suffix)]
    if not cookies:
        return 0

    _api_request(
        config, "POST", f"/sessions/{_USER_ID}/cookies",
        body={"cookies": cookies},
        timeout=15.0,
    )
    log.info(f"camofox: imported {len(cookies)} cookies from {cookie_path.name}")
    return len(cookies)


def _parse_netscape_cookies(text: str) -> list[dict[str, Any]]:
    """Parse Netscape-format cookie file into structured cookie objects."""
    cookies: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        http_only = False
        if line.startswith("#HttpOnly_"):
            http_only = True
            line = line.removeprefix("#HttpOnly_")
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain = parts[0]
        cookie_path = parts[2]
        secure = parts[3].upper() == "TRUE"
        try:
            expires = int(parts[4])
        except (ValueError, IndexError):
            expires = 0
        name = parts[5]
        value = "\t".join(parts[6:])
        cookie: dict[str, Any] = {
            "name": name, "value": value,
            "domain": domain, "path": cookie_path,
            "secure": secure, "expires": expires,
        }
        if http_only:
            cookie["httpOnly"] = True
        cookies.append(cookie)
    return cookies


def evaluate_js(
    tab_id: str,
    expression: str,
    config: dict[str, Any],
    *,
    timeout: float = 15.0,
) -> Any:
    """Evaluate JavaScript expression in a camofox tab. Returns the JS result."""
    result = _api_request(
        config, "POST", f"/tabs/{tab_id}/evaluate",
        body={"userId": _USER_ID, "expression": expression},
        timeout=timeout,
    )
    return result.get("result")


def create_tab(url: str, config: dict[str, Any], *, timeout: float = 30.0) -> str | None:
    """Create a new camofox tab and navigate to URL. Returns tab_id or None."""
    try:
        result = _api_request(
            config, "POST", "/tabs",
            body={"userId": _USER_ID, "sessionKey": _SESSION_KEY, "url": url},
            timeout=timeout,
        )
        return result.get("tabId")
    except Exception as e:
        log.info(f"camofox: create_tab failed - {e}")
        return None


def close_tab(tab_id: str, config: dict[str, Any]) -> None:
    """Close a camofox tab."""
    try:
        _api_request(
            config, "DELETE", f"/tabs/{tab_id}",
            params={"userId": _USER_ID},
            timeout=5.0,
        )
    except Exception:
        pass


def navigate_tab(tab_id: str, url: str, config: dict[str, Any], *, timeout: float = 30.0) -> bool:
    """Navigate an existing tab to a new URL."""
    try:
        _api_request(
            config, "POST", f"/tabs/{tab_id}/navigate",
            body={"userId": _USER_ID, "url": url},
            timeout=timeout,
        )
        return True
    except Exception as e:
        log.info(f"camofox: navigate failed - {e}")
        return False


def _is_pdf_url(url: str) -> bool:
    """Check if a URL looks like a direct PDF link."""
    lower = url.lower()
    return (
        lower.endswith(".pdf")
        or "/pdf/" in lower
        or "content/pdf" in lower
        or "pdfdirect" in lower
        or "/doi/pdf/" in lower
        or "type=printable" in lower
    )


def fetch_url(
    tab_id: str,
    url: str,
    config: dict[str, Any],
    *,
    timeout: float = 30.0,
) -> dict[str, Any] | None:
    """Navigate tab to URL and capture any PDF responses from the network layer."""
    import base64

    try:
        result = _api_request(
            config, "POST", f"/tabs/{tab_id}/fetch-url",
            body={"userId": _USER_ID, "url": url, "timeout": int(timeout * 1000)},
            timeout=timeout + 15,
        )
    except Exception as e:
        log.info(f"camofox: fetch_url failed - {e}")
        return None

    responses = result.get("responses", [])
    for resp in responses:
        data = resp.get("dataBase64", "")
        if data:
            pdf_bytes = base64.b64decode(data)
            if pdf_bytes[:5] == b"%PDF-" and len(pdf_bytes) > 5000:
                return {"status": "ok", "bytes": len(pdf_bytes), "data": pdf_bytes}
    return None


def get_captured_responses(
    tab_id: str,
    config: dict[str, Any],
    *,
    consume: bool = True,
) -> list[dict[str, Any]]:
    """Get captured PDF responses for a tab. Optionally consume (clear) them."""
    try:
        result = _api_request(
            config, "GET", f"/tabs/{tab_id}/captured-responses",
            params={"userId": _USER_ID, "consume": str(consume).lower()},
            timeout=10.0,
        )
        return result.get("responses", [])
    except Exception as e:
        log.info(f"camofox: get_captured_responses failed: {e}")
        return []

