"""Optional parts of LitLoop that the installer leaves out.

The layout model (`pymupdf-layout`, `onnxruntime`, `networkx`, about 65 MB) only
makes PDF-to-markdown tidier: without it `download/markdown.py` falls back to
plain reading order. The installer ships without it; the settings page installs
it on demand with uv into `<data dir>/packages`, and this module puts that
directory on `sys.path` so the rest of LitLoop can import it as if it had been
installed normally.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

#: Where on-demand components go. One directory for all of them: removing one
#: file is enough to uninstall, and a component never shadows a bundled module.
COMPONENTS: dict[str, dict[str, Any]] = {
    "layout": {
        "label": "版面分析",
        "packages": ["pymupdf-layout", "onnxruntime", "networkx"],
        "module": "onnxruntime",
        "size_mb": 65,
    },
}

_INSTALL_LOCK = threading.Lock()


def packages_dir() -> Path:
    """`<data dir>/packages`, next to the config file (LITLOOP_DATA_DIR moves both)."""
    root = Path(os.environ.get("LITLOOP_DATA_DIR") or (Path.home() / ".litloop"))
    return root / "packages"


def ensure_on_path() -> None:
    """Make installed components importable. Called once, when LitLoop starts."""
    folder = packages_dir()
    entry = str(folder)
    try:
        if folder.is_dir() and entry not in sys.path:
            sys.path.insert(0, entry)
    except OSError:
        pass


def installed(key: str) -> bool:
    """Whether the component can be imported right now."""
    spec = COMPONENTS.get(key) or {}
    module = str(spec.get("module") or "")
    if not module:
        return False
    ensure_on_path()
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def uv_command() -> str | None:
    """uv, if it can be found: shipped next to Python, on PATH, or configured."""
    for candidate in (os.environ.get("LITLOOP_UV", ""),
                      str(Path(sys.executable).with_name("uv.exe")),
                      str(Path(sys.executable).with_name("uv")),
                      shutil.which("uv") or ""):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def install(key: str, *, index_url: str = "", timeout: float = 1800.0) -> dict[str, Any]:
    """Download a component into `packages_dir()`. Blocking; run it in a thread.

    ``index_url`` overrides the package index (a domestic mirror), since the
    official PyPI is slow from some networks. Returns ``{ok, log}``: the log is
    the tail of uv's output, which the settings page shows when it fails.
    """
    spec = COMPONENTS.get(key)
    if not spec:
        raise KeyError(f"no such component: {key}")
    uv = uv_command()
    if not uv:
        return {"ok": False, "log": "找不到 uv：安装包应当自带，或在 PATH 里放一个 uv"}
    target = packages_dir()
    target.mkdir(parents=True, exist_ok=True)
    cmd = [uv, "pip", "install", "--python", sys.executable, "--target", str(target),
           *spec["packages"]]
    env = {**os.environ, "UV_LINK_MODE": "copy"}
    if index_url:
        env["UV_INDEX_URL"] = index_url
    if not _INSTALL_LOCK.acquire(timeout=5):
        return {"ok": False, "log": "另一个组件正在安装，稍后再试"}
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "log": f"安装超过了 {int(timeout)} 秒，已放弃"}
    except OSError as exc:
        return {"ok": False, "log": f"无法运行 uv：{exc}"}
    finally:
        _INSTALL_LOCK.release()
    tail = "\n".join((done.stdout or "").strip().splitlines()[-15:] +
                     (done.stderr or "").strip().splitlines()[-15:]).strip()
    if done.returncode != 0:
        return {"ok": False, "log": tail or f"uv 退出码 {done.returncode}"}
    # The new directory may be new to the import system.
    importlib.invalidate_caches()
    ensure_on_path()
    return {"ok": installed(key), "log": tail or "已安装"}
