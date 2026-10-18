"""Network session management and HTTP helpers."""

from __future__ import annotations

import os
import random
import threading
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from .config import load_config
from .log import get_logger

log = get_logger()

# Check SOCKS proxy support
try:
    import socks  # noqa: F401
    HAS_SOCKS = True
except ImportError:
    HAS_SOCKS = False

def user_agent(extra: str = "") -> str:
    """Return the LitLoop User-Agent header value."""
    try:
        from . import __version__
    except (ImportError, AttributeError):
        try:
            import litloop
            __version__ = getattr(litloop, "__version__", "1.0")
        except Exception:
            __version__ = "1.0"
    base = f"LitLoop/{__version__}"
    suffix = extra.strip()
    if suffix:
        return f"{base} {suffix}"
    return base


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/124.0 Safari/537.36 {user_agent()}"
)

# Browser UA without the LitLoop suffix: some CDNs (sci.bban.top PDF edge,
# observed 2026-09) 403 any UA that names a non-browser client, even with a
# valid Referer. Use only where impersonation is required to read at all.
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

CLOUDFLARE_CHALLENGE_SIGNALS = (
    "just a moment", "attention required", "verify", "security check",
    "请稍候", "正在验证", "checking", "cloudflare",
)


def is_cloudflare_challenge(title: str) -> bool:
    """Check if a page title indicates a Cloudflare/anti-bot challenge."""
    lower = title.lower()
    return any(sig in lower for sig in CLOUDFLARE_CHALLENGE_SIGNALS)

_session_pool: dict[str, requests.Session] = {}


def configured_proxy(config: dict[str, Any] | None = None) -> str:
    """The one explicit proxy setting: LITLOOP_PROXY env > network_proxy config.

    HTTP_PROXY/HTTPS_PROXY are ignored project-wide (NO_PROXY='*' in the
    package __init__, sessions run trust_env=False — the ZCode proxy CA
    workaround). Campus-bound traffic (Elsevier/Springer TDM entitlement is
    IP-based) must go through THIS setting or it egresses off-campus.
    """
    return os.environ.get("LITLOOP_PROXY") or (config or {}).get("network_proxy") or ""


_PROXY_UP: dict[str, tuple[float, bool]] = {}


def _proxy_reachable(proxy_url: str) -> bool:
    """Is the proxy's port accepting connections? Cached for 30 s."""
    import socket
    from urllib.parse import urlparse
    now = time.monotonic()
    cached = _PROXY_UP.get(proxy_url)
    if cached and now - cached[0] < 30:
        return cached[1]
    u = urlparse(proxy_url)
    try:
        with socket.create_connection((u.hostname or "127.0.0.1", u.port or 80), timeout=1):
            up = True
    except OSError:
        up = False
    _PROXY_UP[proxy_url] = (now, up)
    return up


def host_proxy(config: dict[str, Any] | None) -> tuple[str, tuple[str, ...]]:
    """(proxy_url, domains) of the per-host proxy exception, or ("", ()) when off/down."""
    config = config or {}
    url = str(config.get("host_proxy_url") or "").strip()
    hosts = tuple(str(h).strip().lstrip(".").lower() for h in config.get("host_proxy_hosts") or [] if str(h).strip())
    if not url or not hosts or not _proxy_reachable(url):
        return "", ()
    return url, hosts


def grey_hosts(config: dict[str, Any] | None) -> tuple[str, ...]:
    """Sci-Hub and LibGen mirrors (from the settings' domain lists)."""
    from urllib.parse import urlparse
    config = config or {}
    from .sources.libgen import LIBGEN_MIRRORS
    urls = list(config.get("scihub_domains") or []) + list(LIBGEN_MIRRORS) + ["https://bban.top"]
    return tuple(sorted({(urlparse(str(u)).hostname or "").lower() for u in urls} - {""}))


def grey_proxy(config: dict[str, Any] | None) -> str:
    """The proxy Sci-Hub and LibGen go through: host_proxy_url while its port
    answers, when scihub_route says proxy (or auto measured the proxy faster,
    sources/scihub_speed.py); else "" (direct)."""
    config = config or {}
    url = str(config.get("host_proxy_url") or "").strip()
    from .sources.scihub_speed import route
    if route(config) != "proxy" or not url or not _proxy_reachable(url):
        return ""
    return url


def host_uses_proxy(host: str, hosts: tuple[str, ...]) -> bool:
    host = (host or "").lower()
    return any(host == h or host.endswith("." + h) for h in hosts)


class _HostProxyAdapter(HTTPAdapter):
    """Sends requests for the configured domains through the exception proxy."""

    def __init__(self, proxy_url: str, hosts: tuple[str, ...]) -> None:
        super().__init__()
        self._proxy = proxy_url
        self._hosts = hosts

    def send(self, request, **kwargs):  # type: ignore[override]
        from urllib.parse import urlparse
        if host_uses_proxy(urlparse(request.url).hostname or "", self._hosts):
            kwargs["proxies"] = {"http": self._proxy, "https": self._proxy}
        return super().send(request, **kwargs)


def apply_host_proxy(session: requests.Session, config: dict[str, Any] | None) -> None:
    """Route the per-host proxy exception's domains through its proxy (no-op when off)."""
    url, hosts = host_proxy(config)
    if url:
        adapter = _HostProxyAdapter(url, hosts)
        session.mount("https://", adapter)
        session.mount("http://", adapter)


def _get_session(config: dict[str, Any]) -> requests.Session:
    proxy = configured_proxy(config)
    key = proxy or "__none__"
    if key not in _session_pool:
        s = requests.Session()
        s.trust_env = False
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=0)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        s.headers.update({"User-Agent": USER_AGENT})
        if proxy:
            if proxy.startswith("socks") and not HAS_SOCKS:
                log.warning("SOCKS proxy configured but PySocks not installed. Install: pip install requests[socks]")
            s.proxies = {"http": proxy, "https": proxy}
        _session_pool[key] = s
    return _session_pool[key]


def request_timeout(config: dict[str, Any]) -> tuple[int, int]:
    return (int(config.get("connect_timeout", 3)), int(config.get("read_timeout", 7)))


def proxy_dict(proxy: str | None) -> dict[str, str] | None:
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def select_proxy_for_url(url: str, config: dict[str, Any], use_tor: bool = False) -> str | None:
    if use_tor:
        from .tor import ensure_tor
        tor_proxy = ensure_tor(config)
        if tor_proxy:
            return tor_proxy
        log.warning("Tor requested but unavailable — falling back to direct connection")

    # Some hosts rate-limit by egress IP, and every proxy user shares ONE exit
    # — so proxied requests get walled quickly (measured live on sci-hub.ru,
    # 2026-08-30: proxied = standing verification wall, direct = clean). These
    # hosts route DIRECT by default; ``direct_domains`` extends the set,
    # ``scihub_direct: false`` disables the behavior.
    from urllib.parse import urlparse

    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = ""
    grey = grey_proxy(config)
    if grey and host_uses_proxy(host, grey_hosts(config)):
        return grey  # the user's choice wins over the direct default below
    direct_hosts = {"sci-hub.ru"}
    direct_hosts |= {str(d).lower().lstrip(".") for d in (config.get("direct_domains") or [])}
    if config.get("scihub_direct", True) and any(
        host == h or host.endswith("." + h) for h in direct_hosts if h
    ):
        return None

    explicit = configured_proxy(config)
    if explicit:
        return explicit
    return None


def fetch(
    url: str,
    config: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    stream: bool = False,
    method: str = "GET",
    use_tor: bool = False,
) -> requests.Response:
    domain_rate_limit(url)
    host_concurrency_acquire(url, config)
    try:
        merged_headers = {"User-Agent": USER_AGENT}
        if headers:
            merged_headers.update(headers)
        session = _get_session(config)
        proxies = proxy_dict(select_proxy_for_url(url, config, use_tor=use_tor))
        return session.request(
            method,
            url,
            headers=merged_headers,
            timeout=request_timeout(config),
            proxies=proxies,
            allow_redirects=True,
            stream=stream,
        )
    finally:
        host_concurrency_release(url, config)


def fetch_json(
    url: str,
    config: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    use_tor: bool = False,
) -> dict[str, Any] | None:
    # In-memory probe cache: avoid hammering metadata endpoints during racing.
    ttl = float(config.get("json_probe_cache_seconds", 0) or 0)
    now = time.time()
    if ttl > 0:
        cached = _json_cache.get(url)
        if cached is not None and _json_cache_expires.get(url, 0) > now:
            return cached
    try:
        resp = fetch(url, config, headers={"Accept": "application/json", **(headers or {})}, use_tor=use_tor)
        if resp.status_code >= 400:
            return None
        data = resp.json()
    except Exception:
        return None
    if ttl > 0:
        _json_cache[url] = data
        _json_cache_expires[url] = now + ttl
    return data


# Module-level JSON probe caches (see fetch_json). Mutable for test reset.
_json_cache: dict[str, dict[str, Any]] = {}
_json_cache_expires: dict[str, float] = {}


def _is_cloudflare_block(resp: requests.Response) -> bool:
    """Check if response is a Cloudflare/anti-bot challenge."""
    if resp.status_code in (403, 503):
        server = resp.headers.get("server", "").lower()
        if "cloudflare" in server:
            return True
        try:
            body = resp.text[:2000].lower()
            if "cf-browser-verification" in body or "challenge-platform" in body:
                return True
        except Exception:
            pass
    return False


def fetch_with_browser(
    url: str,
    config: dict[str, Any],
    *,
    stream: bool = False,
) -> requests.Response | None:
    """Fetch URL using CloakBrowser to bypass Cloudflare challenges."""
    from .browser_engine import solve_url, is_available
    if not is_available(config):
        return None
    result = solve_url(url, config)
    if not result:
        return None
    solution = result.get("solution", {})
    status = solution.get("status", 0)
    if status >= 400:
        return None
    resp = requests.Response()
    resp.status_code = status
    resp._content = solution.get("response", "").encode("utf-8")
    resp.url = solution.get("url", url)
    cookies = solution.get("cookies", [])
    if isinstance(cookies, list):
        for c in cookies:
            if "name" in c and "value" in c:
                resp.cookies.set(c["name"], c["value"])
    return resp


def polite_delay(config: dict[str, Any]) -> None:
    # Polite delay is opt-in: only sleep when fixed_request_delay_enabled is True.
    # Without it, racing/parallel sources would be needlessly slowed.
    if not config.get("fixed_request_delay_enabled", False):
        return
    lo = float(config.get("request_delay_min", 0))
    hi = float(config.get("request_delay_max", 0))
    if hi > 0:
        time.sleep(random.uniform(lo, max(lo, hi)))


# ============================================================
# Per-domain rate limiting: max 1 request per domain per interval
# ============================================================
_domain_locks: dict[str, threading.Lock] = {}
_domain_last_request: dict[str, float] = {}
_DOMAIN_RATE_LIMIT = 0.5  # seconds between requests to same domain
# Longer gaps where the provider's terms ask for them (docs/API_LIMITS.md):
# arXiv API "no more than one request every three seconds"; CORE "five single
# requests per 10 seconds".
_DOMAIN_MIN_INTERVAL = {"export.arxiv.org": 3.0, "arxiv.org": 3.0, "api.core.ac.uk": 2.0}
# Hourly allowances: OpenAIRE allows 60 requests/hour without a token. Once
# used up the host is skipped (an OA probe, other sources cover it) instead of
# stalling downloads for up to an hour.
_DOMAIN_HOURLY_CAP = {"api.openaire.eu": 55}
_domain_hour_log: dict[str, list[float]] = {}


def _extract_domain(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return url


def domain_rate_limit(url: str) -> None:
    """Wait if needed to respect per-domain rate limit."""
    domain = _extract_domain(url)
    if not domain:
        return
    if domain not in _domain_locks:
        _domain_locks[domain] = threading.Lock()
    with _domain_locks[domain]:
        cap = _DOMAIN_HOURLY_CAP.get(domain)
        if cap:
            recent = [s for s in _domain_hour_log.get(domain, []) if time.time() - s < 3600]
            if len(recent) >= cap:
                raise requests.ConnectionError(f"{domain}: hourly allowance of {cap} requests used; skipped")
            _domain_hour_log[domain] = recent + [time.time()]
        gap = _DOMAIN_MIN_INTERVAL.get(domain, _DOMAIN_RATE_LIMIT)
        now = time.time()
        last = _domain_last_request.get(domain, 0)
        elapsed = now - last
        if elapsed < gap:
            time.sleep(gap - elapsed)
        _domain_last_request[domain] = time.time()


# ============================================================
# Per-host concurrency control (scimesh-style)
# ============================================================
# Inherited from the original project; not published by the providers, except
# arXiv (single connection) and CORE (see _DOMAIN_MIN_INTERVAL). docs/API_LIMITS.md.
_DEFAULT_HOST_CONCURRENCY: dict[str, int] = {
    "unpaywall.org": 3,
    "api.openalex.org": 5,
    "content.openalex.org": 3,
    "api.crossref.org": 5,
    "api.openaire.eu": 3,
    "doaj.org": 3,
    "api.semanticscholar.org": 3,
    "api.core.ac.uk": 1,     # CORE: five single requests per 10 seconds
    "europepmc.org": 3,
    "www.ebi.ac.uk": 3,
    "arxiv.org": 1,          # arXiv terms: a single connection at a time
    "export.arxiv.org": 1,
}

_host_semaphores: dict[str, threading.Semaphore] = {}


def _get_host_concurrency(host: str, config: dict[str, Any]) -> int:
    """Get max concurrent requests for a host."""
    custom = config.get("host_concurrency", {})
    if isinstance(custom, dict) and host in custom:
        return int(custom[host])
    # Match by suffix (e.g., "sci-hub.ru" matches default)
    for pattern, limit in _DEFAULT_HOST_CONCURRENCY.items():
        if host == pattern or host.endswith("." + pattern):
            return limit
    return 0


def host_concurrency_acquire(url: str, config: dict[str, Any]) -> None:
    """Acquire a per-host concurrency slot. Blocks if at limit."""
    host = _extract_domain(url)
    if not host:
        return
    limit = _get_host_concurrency(host, config)
    if limit <= 0:
        return
    if host not in _host_semaphores:
        _host_semaphores[host] = threading.Semaphore(limit)
    _host_semaphores[host].acquire()


def host_concurrency_release(url: str, config: dict[str, Any]) -> None:
    """Release a per-host concurrency slot."""
    host = _extract_domain(url)
    if not host:
        return
    if host in _host_semaphores:
        _host_semaphores[host].release()
