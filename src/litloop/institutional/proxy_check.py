"""Is a proxy program (v2rayN, Clash, ...) in the way of the institutional route?

Institutional access is granted by the campus network's IP, so LitLoop's own
traffic must leave directly, not through the user's proxy:

* system-proxy mode (the proxy program sets Windows' proxy setting): LitLoop
  already ignores it — requests run with ``trust_env=False`` and the browsers
  it starts get ``--no-proxy-server``. Nothing to do.
* TUN / virtual-adapter mode: the proxy program captures all traffic below
  the applications, which no program can opt out of. The user has to add
  "direct" rules for LitLoop's processes in the proxy program.

Only local facts are read (network adapters, process names, the Windows
proxy setting); nothing is sent anywhere.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# Proxy programs, by process name (lower case, without .exe) -> what to call them.
PROGRAMS = {
    "v2rayn": "v2rayN", "xray": "Xray", "sing-box": "sing-box", "v2ray": "V2Ray",
    "clash-verge": "Clash Verge", "clash verge": "Clash Verge", "verge-mihomo": "Clash Verge",
    "mihomo": "mihomo", "clash-meta": "Clash Meta", "clash": "Clash", "clashx": "ClashX",
    "nekoray": "NekoRay", "hiddify": "Hiddify",
}
# Virtual adapters such programs create for TUN mode (name or description).
_TUN = re.compile(r"sing-?tun|singbox|wintun|mihomo|clash|meta|\btun\b|v2ray|xray|nekoray|hiddify|tap-windows", re.I)

BROWSER_PROCESS = "litloop-chrome.exe"  # browser_backend.STABLE_BROWSER_NAME


def litloop_processes() -> list[str]:
    """Process names that need a direct rule: the Python running LitLoop and its browser."""
    names = [Path(sys.executable).name or "python.exe", BROWSER_PROCESS]
    return list(dict.fromkeys(names))


def litloop_paths() -> list[str]:
    """Full paths of those programs, for proxy programs that match by path.

    A virtual environment's python.exe only starts the base interpreter, which
    is the process that talks to the network, so that is the one listed.
    """
    paths = [getattr(sys, "_base_executable", "") or sys.executable]
    from ..config import DATA_DIR
    try:
        paths += [str(p) for p in Path(DATA_DIR).glob(f"*/{BROWSER_PROCESS}")]
    except OSError:
        pass
    return list(dict.fromkeys(p for p in paths if p))


def _windows_facts() -> dict[str, Any]:
    script = (
        "$a = Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | Where-Object Status -eq 'Up' | "
        "Select-Object Name, InterfaceDescription; "
        "$p = Get-Process -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name; "
        "$s = Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings' -ErrorAction SilentlyContinue; "
        "@{adapters=@($a); processes=@($p); proxy_enable=$s.ProxyEnable; proxy_server=$s.ProxyServer} | ConvertTo-Json -Depth 3 -Compress"
    )
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return json.loads(out.stdout or "{}")


def detect(facts: dict[str, Any] | None = None) -> dict[str, Any]:
    """What is running and in which mode.

    ``mode``: ``tun`` (traffic is captured: direct rules needed), ``system``
    (system proxy only: LitLoop bypasses it by itself), ``none``, or
    ``unknown`` (could not look, e.g. not Windows).
    """
    if facts is None:
        if os.name != "nt":
            return {"mode": "unknown", "programs": [], "tun": [], "system_proxy": None,
                    "direct_processes": litloop_processes()}
        try:
            facts = _windows_facts()
        except (OSError, ValueError, subprocess.SubprocessError):
            return {"mode": "unknown", "programs": [], "tun": [], "system_proxy": None,
                    "direct_processes": litloop_processes()}
    running = {str(p).lower() for p in facts.get("processes") or []}
    programs = list(dict.fromkeys(label for name, label in PROGRAMS.items() if name in running))
    tun = [a for a in facts.get("adapters") or []
           if _TUN.search(f"{a.get('Name', '')} {a.get('InterfaceDescription', '')}")]
    system = facts.get("proxy_server") if facts.get("proxy_enable") in (1, True, "1") else None
    mode = "tun" if tun else "system" if (system or programs) else "none"
    return {"mode": mode, "programs": programs, "tun": [a.get("Name") for a in tun],
            "system_proxy": system, "direct_processes": litloop_processes()}


ACADEMIC_PROBE_SITES: list[dict[str, Any]] = [
    {"name": "ScienceDirect", "host": "www.sciencedirect.com", "url": "https://www.sciencedirect.com/", "campus_sensitive": True},
    {"name": "Springer", "host": "link.springer.com", "url": "https://link.springer.com/", "campus_sensitive": True},
    {"name": "Nature", "host": "www.nature.com", "url": "https://www.nature.com/", "campus_sensitive": True},
    {"name": "Wiley", "host": "onlinelibrary.wiley.com", "url": "https://onlinelibrary.wiley.com/", "campus_sensitive": True},
    {"name": "ACS", "host": "pubs.acs.org", "url": "https://pubs.acs.org/", "campus_sensitive": True},
    {"name": "RSC", "host": "pubs.rsc.org", "url": "https://pubs.rsc.org/", "campus_sensitive": True},
    {"name": "Taylor & Francis", "host": "www.tandfonline.com", "url": "https://www.tandfonline.com/", "campus_sensitive": True},
    {"name": "SAGE", "host": "journals.sagepub.com", "url": "https://journals.sagepub.com/", "campus_sensitive": True},
    {"name": "OUP", "host": "academic.oup.com", "url": "https://academic.oup.com/", "campus_sensitive": True},
    {"name": "IEEE", "host": "ieeexplore.ieee.org", "url": "https://ieeexplore.ieee.org/", "campus_sensitive": True},
    {"name": "arXiv", "host": "arxiv.org", "url": "https://arxiv.org/", "campus_sensitive": False},
    {"name": "MDPI", "host": "www.mdpi.com", "url": "https://www.mdpi.com/", "campus_sensitive": False},
    {"name": "doi.org", "host": "doi.org", "url": "https://doi.org/", "campus_sensitive": False},
    {"name": "SSRN", "host": "papers.ssrn.com", "url": "https://papers.ssrn.com/", "campus_sensitive": False},
    {"name": "bioRxiv", "host": "www.biorxiv.org", "url": "https://www.biorxiv.org/", "campus_sensitive": False},
]

_COMMON_PROXY_PORTS = (10808, 7890, 7897, 10809, 1080, 10801)


def _normalize_proxy_url(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if "://" not in text:
        return f"http://{text}"
    return text


def detect_local_proxy(configured_url: str = "") -> str | None:
    """Return configured proxy URL, active system proxy, or first listening local proxy port."""
    explicit = _normalize_proxy_url(configured_url)
    if explicit:
        return explicit

    info = detect()
    sys_proxy = _normalize_proxy_url(str(info.get("system_proxy") or ""))
    if sys_proxy:
        return sys_proxy

    import socket
    for port in _COMMON_PROXY_PORTS:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return f"http://127.0.0.1:{port}"
        except OSError:
            continue
    return None


def _probe_one_url(url: str, *, proxy_url: str | None = None, timeout: float = 6.0) -> dict[str, Any]:
    """Measure TCP/TLS/HTTP reachability and latency for one URL, bypassing env proxies."""
    import time
    import requests

    session = requests.Session()
    session.trust_env = False
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else {"http": None, "https": None}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
    }
    t0 = time.perf_counter()
    try:
        with session.get(url, timeout=timeout, allow_redirects=True, stream=True, proxies=proxies, headers=headers) as resp:
            ms = int(round((time.perf_counter() - t0) * 1000))
            return {"ok": resp.status_code < 500, "status": resp.status_code, "ms": ms, "error": ""}
    except requests.RequestException as exc:
        ms = int(round((time.perf_counter() - t0) * 1000))
        return {"ok": False, "status": 0, "ms": ms, "error": type(exc).__name__}
    finally:
        session.close()


def recommend_site_route(
    site: dict[str, Any],
    direct: dict[str, Any],
    proxy: dict[str, Any] | None,
    *,
    is_campus: bool,
) -> dict[str, str]:
    """Recommend direct vs proxy for one site while protecting campus IP entitlement."""
    direct_ok = bool(direct.get("ok"))
    if proxy is None:
        if direct_ok:
            return {"route": "direct", "reason": "直连可达（未检测到本地代理）"}
        return {"route": "unavailable", "reason": "直连失败且未检测到本地代理"}

    proxy_ok = bool(proxy.get("ok"))
    if not direct_ok and proxy_ok:
        return {"route": "proxy", "reason": "直连失败，代理可达"}
    if not direct_ok and not proxy_ok:
        return {"route": "unavailable", "reason": "直连与代理均不可达"}
    if direct_ok and not proxy_ok:
        return {"route": "direct", "reason": "直连可达，代理不可用"}

    if is_campus and site.get("campus_sensitive"):
        return {"route": "direct", "reason": "校园网下直连可用，保留机构 IP 权限"}

    d_ms = int(direct.get("ms") or 0)
    p_ms = int(proxy.get("ms") or 0)
    if d_ms >= 2000 and p_ms > 0 and d_ms >= p_ms * 2:
        return {"route": "proxy", "reason": f"代理明显更快（{d_ms} ms → {p_ms} ms）"}
    return {"route": "direct", "reason": "直连速度可用，无需走代理"}


def probe_academic_routes(
    *,
    proxy_url: str = "",
    is_campus: bool | None = None,
    configured_hosts: list[str] | None = None,
    timeout: float = 6.0,
) -> dict[str, Any]:
    """Benchmark direct vs local proxy reachability across common academic sites."""
    from concurrent.futures import ThreadPoolExecutor
    from .. import config as _config

    cfg = _config.load_config()
    if is_campus is None:
        is_campus = bool(cfg.get("is_campus_network"))
    if configured_hosts is None:
        configured_hosts = [str(h).strip().lower() for h in (cfg.get("host_proxy_hosts") or []) if str(h).strip()]
    else:
        configured_hosts = [str(h).strip().lower() for h in configured_hosts if str(h).strip()]

    active_proxy = detect_local_proxy(proxy_url or str(cfg.get("host_proxy_url") or ""))

    def _probe_site(site: dict[str, Any]) -> dict[str, Any]:
        direct_res = _probe_one_url(site["url"], proxy_url=None, timeout=timeout)
        proxy_res = _probe_one_url(site["url"], proxy_url=active_proxy, timeout=timeout) if active_proxy else None
        rec = recommend_site_route(site, direct_res, proxy_res, is_campus=bool(is_campus))
        return {
            "name": site["name"],
            "host": site["host"],
            "campus_sensitive": bool(site["campus_sensitive"]),
            "direct": direct_res,
            "proxy": proxy_res,
            "recommendation": rec["route"],
            "reason": rec["reason"],
            "currently_proxied": site["host"].lower() in configured_hosts,
        }

    with ThreadPoolExecutor(max_workers=8) as pool:
        sites = list(pool.map(_probe_site, ACADEMIC_PROBE_SITES))

    recommended_hosts = [s["host"] for s in sites if s["recommendation"] == "proxy"]
    return {
        "proxy_url": active_proxy or "",
        "is_campus": bool(is_campus),
        "configured_hosts": configured_hosts,
        "recommended_hosts": recommended_hosts,
        "sites": sites,
    }
