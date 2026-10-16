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
    from .config import DATA_DIR
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
