"""New-version reminder for LitLoop, easyseek (scansci-find) and zotero-mcp.

Checked at most every ``update_check_days`` days (config; 0 = never), in a
background thread, so no search or download waits for it. The result is kept
in ``~/.litloop/update_check.json``; ``notice()`` reads that file and, when it
is stale, starts the next check. Nothing is installed: the notice says how to
update.

Where the versions come from:
- LitLoop, easyseek: ``version`` in ``pyproject.toml`` on the main branch
  (git tags stopped at v1.9.0, so they are not used).
- zotero-mcp: PyPI (``zotero-mcp-server``), against the version installed in
  zotero-mcp's own Python.
"""

from __future__ import annotations

import importlib.metadata
import re
import subprocess
import threading
import time
from typing import Any

import requests

from . import __version__, config, store

RAW = "https://raw.githubusercontent.com/dysprosium231/{repo}/main/pyproject.toml"
PYPI = "https://pypi.org/pypi/{name}/json"
_running = threading.Lock()


def _path():
    return config.DATA_DIR / "update_check.json"


def _parse(v: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])


def _remote_pyproject(repo: str) -> str | None:
    r = requests.get(RAW.format(repo=repo), timeout=15)
    r.raise_for_status()
    m = re.search(r'^version\s*=\s*"([^"]+)"', r.text, re.M)
    return m.group(1) if m else None


def _pypi(name: str) -> str | None:
    r = requests.get(PYPI.format(name=name), timeout=15)
    r.raise_for_status()
    return r.json()["info"]["version"]


def _installed(dist: str) -> str | None:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


def _zotero_mcp_installed(cfg: dict[str, Any]) -> str | None:
    from .zotero.client import discover_zotero_mcp
    found = discover_zotero_mcp(cfg)
    if not found:
        return None
    r = subprocess.run([str(found["python"]), "-c",
                        "import importlib.metadata as m; print(m.version('zotero-mcp-server'))"],
                       capture_output=True, text=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.stdout.strip() or None


# name, how to read the installed version, how to read the latest one, how to update
COMPONENTS = [
    ("LitLoop", lambda cfg: __version__, lambda: _remote_pyproject("litloop-paper-agent"),
     "运行 python scripts/deploy.py 更新"),
    ("easyseek", lambda cfg: _installed("scansci-find"), lambda: _remote_pyproject("easyseek"),
     "运行 python scripts/deploy.py 更新"),
    ("zotero-mcp", _zotero_mcp_installed, lambda: _pypi("zotero-mcp-server"),
     "在 zotero-mcp 的 Python 里运行 pip install -U zotero-mcp-server"),
]


def check(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ask now; store and return {"checked": ts, "components": [...]}. A component that fails is skipped."""
    cfg = cfg or config.load_config()
    out = []
    for name, installed, latest, how in COMPONENTS:
        try:
            have, new = installed(cfg), latest()
        except Exception:  # offline, rate-limited, not installed: try again next time
            continue
        if have and new:
            out.append({"name": name, "installed": have, "latest": new,
                        "update": _parse(new) > _parse(have), "how": how})
    data = {"checked": time.time(), "components": out}
    store.write_json(_path(), data)
    return data


def _due(cfg: dict[str, Any]) -> bool:
    days = float(cfg.get("update_check_days", 7) or 0)
    if days <= 0:
        return False
    try:
        last = float(store.read_json(_path()).get("checked") or 0)
    except (OSError, ValueError):
        last = 0
    return time.time() - last > days * 86400


def _refresh_in_background(cfg: dict[str, Any]) -> None:
    if not _running.acquire(blocking=False):
        return

    def run() -> None:
        try:
            check(cfg)
        except Exception:
            pass
        finally:
            _running.release()
    threading.Thread(target=run, name="litloop-update-check", daemon=True).start()


def available(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Components with a newer version, from the last check; starts a check when one is due."""
    cfg = cfg or config.load_config()
    if float(cfg.get("update_check_days", 7) or 0) <= 0:
        return []
    if _due(cfg):
        _refresh_in_background(cfg)
    try:
        data = store.read_json(_path())
    except (OSError, ValueError):
        return []
    # LitLoop itself may have been updated since: compare with the running version.
    return [c for c in data.get("components", [])
            if c.get("update") and not (c["name"] == "LitLoop" and _parse(c["latest"]) <= _parse(__version__))]


def notice(cfg: dict[str, Any] | None = None) -> str | None:
    """One line for tool results, or None."""
    comps = available(cfg)
    if not comps:
        return None
    return "有新版本：" + "；".join(f"{c['name']} {c['latest']}（当前 {c['installed']}），{c['how']}" for c in comps)
