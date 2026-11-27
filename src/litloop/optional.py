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
import time
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
    "zotero_mcp": {
        "label": "Zotero 桥接",
        "packages": ["zotero-mcp-server"],
        "module": "zotero_mcp",
        "size_mb": 20,
        "isolated": True,
    },
}

_INSTALL_LOCK = threading.Lock()
_STATUS_LOCK = threading.Lock()
_STATUS: dict[str, dict[str, Any]] = {}

_STAGES = ("准备环境", "正在下载依赖", "正在安装", "正在校验")
_STAGE_ORDER = {name: idx for idx, name in enumerate(_STAGES)}
_NETWORK_ERROR_HINTS = (
    "timed out",
    "timeout",
    "connection",
    "ssl",
    "certificate",
    "dns",
    "name or service not known",
    "temporary failure in name resolution",
    "failed to fetch",
    "network",
    "proxy",
    "403",
    "404",
    "502",
    "503",
    "index",
)


def packages_dir() -> Path:
    """`<data dir>/packages`, next to the config file (LITLOOP_DATA_DIR moves both)."""
    root = Path(os.environ.get("LITLOOP_DATA_DIR") or (Path.home() / ".litloop"))
    return root / "packages"


def component_dir(key: str) -> Path:
    """Install target directory for *key*: isolated components live in `<data dir>/components/<key>`."""
    spec = COMPONENTS.get(key) or {}
    if spec.get("isolated"):
        return packages_dir().parent / "components" / key
    return packages_dir()


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
    if spec.get("isolated"):
        folder = component_dir(key)
        try:
            return (folder / module).is_dir() or (folder / f"{module}.py").is_file()
        except OSError:
            return False
    ensure_on_path()
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _verify_isolated(target: Path, module: str) -> bool:
    """Verify an isolated component imports cleanly in a child process."""
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); __import__(sys.argv[2])",
                str(target),
                module,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _verify_installed(key: str, target: Path) -> bool:
    spec = COMPONENTS.get(key) or {}
    if not installed(key):
        return False
    if spec.get("isolated"):
        module = str(spec.get("module") or "")
        if (target / module).is_dir() or (target / f"{module}.py").is_file():
            return _verify_isolated(target, module)
    return True


def uv_command() -> str | None:
    """uv, if it can be found: shipped next to Python, on PATH, or configured."""
    for candidate in (os.environ.get("LITLOOP_UV", ""),
                      str(Path(sys.executable).with_name("uv.exe")),
                      str(Path(sys.executable).with_name("uv")),
                      shutil.which("uv") or ""):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def _set_status(
    key: str,
    *,
    state: str,
    stage: str = "",
    elapsed: float = 0.0,
    line: str = "",
    ok: bool | None = None,
    log: str = "",
    started_at: float | None = None,
) -> None:
    """Record the latest install progress for *key* under ``_STATUS_LOCK``."""
    with _STATUS_LOCK:
        _STATUS[key] = {
            "key": key,
            "state": state,
            "stage": stage,
            "elapsed": round(float(elapsed), 1),
            "line": line[:160],
            "ok": ok,
            "log": log,
            "started_at": started_at,
        }


def install_status(key: str) -> dict[str, Any]:
    """Return the current install status for an optional component."""
    if key not in COMPONENTS:
        raise KeyError(f"no such component: {key}")
    with _STATUS_LOCK:
        entry = dict(_STATUS.get(key) or {})
    if not entry:
        return {
            "key": key,
            "installed": installed(key),
            "state": "idle",
            "stage": "",
            "elapsed": 0.0,
            "line": "",
            "ok": None,
            "log": "",
        }
    elapsed = float(entry.get("elapsed") or 0.0)
    started_at = entry.get("started_at")
    if entry.get("state") == "running" and isinstance(started_at, (int, float)):
        elapsed = round(max(0.0, time.monotonic() - started_at), 1)
    return {
        "key": key,
        "installed": installed(key),
        "state": str(entry.get("state") or "idle"),
        "stage": str(entry.get("stage") or ""),
        "elapsed": elapsed,
        "line": str(entry.get("line") or ""),
        "ok": entry.get("ok"),
        "log": str(entry.get("log") or ""),
    }


def _stage_from_line(line: str, current: str) -> str:
    """Advance the install stage monotonically from a single line of uv output."""
    low = line.lower()
    detected = current
    if any(tok in low for tok in ("installing", "unpacking", "installed", "built ")):
        detected = "正在安装"
    elif any(tok in low for tok in ("downloading", "fetching", "resolved ", "prepared ")):
        detected = "正在下载依赖"
    if _STAGE_ORDER.get(detected, 0) >= _STAGE_ORDER.get(current, 0):
        return detected
    return current


def _format_failure_log(returncode: int, tail: str) -> str:
    """Build a clear Chinese failure reason with trailing uv output."""
    low = tail.lower()
    if any(hint in low for hint in _NETWORK_ERROR_HINTS):
        prefix = f"网络或镜像连接失败（退出码 {returncode}），请在设置中检查网络或更换镜像源后重试"
    else:
        prefix = f"安装失败（uv 退出码 {returncode}）"
    return f"{prefix}\n{tail}" if tail else prefix


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
        msg = "找不到 uv：安装包应当自带，或在 PATH 里放一个 uv"
        _set_status(key, state="failed", stage="", elapsed=0.0, line=msg, ok=False, log=msg)
        return {"ok": False, "log": msg}
    target = component_dir(key)
    target.mkdir(parents=True, exist_ok=True)
    cmd = [uv, "pip", "install", "--python", sys.executable, "--target", str(target),
           *spec["packages"]]
    env = {**os.environ, "UV_LINK_MODE": "copy"}
    env.setdefault("UV_NATIVE_TLS", "true")
    if index_url:
        env["UV_INDEX_URL"] = index_url
    if not _INSTALL_LOCK.acquire(timeout=5):
        return {"ok": False, "log": "另一个组件正在安装，稍后再试"}

    started = time.monotonic()
    stage = "准备环境"
    lines: list[str] = []
    last_line = ""
    timed_out = False
    _set_status(key, state="running", stage=stage, elapsed=0.0, line="", ok=None, log="", started_at=started)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        _INSTALL_LOCK.release()
        elapsed = round(time.monotonic() - started, 1)
        msg = f"无法运行 uv：{exc}"
        _set_status(key, state="failed", stage=stage, elapsed=elapsed, line=msg, ok=False, log=msg)
        return {"ok": False, "log": msg}

    def _on_timeout() -> None:
        nonlocal timed_out
        timed_out = True
        try:
            proc.kill()
        except OSError:
            pass

    timer = threading.Timer(timeout, _on_timeout)
    timer.daemon = True
    timer.start()
    try:
        if proc.stdout is not None:
            for raw_line in proc.stdout:
                text_line = raw_line.rstrip("\r\n")
                stripped = text_line.strip()
                if stripped:
                    lines.append(stripped)
                    last_line = stripped[:160]
                    stage = _stage_from_line(stripped, stage)
                elapsed = round(time.monotonic() - started, 1)
                _set_status(
                    key,
                    state="running",
                    stage=stage,
                    elapsed=elapsed,
                    line=last_line,
                    ok=None,
                    log="\n".join(lines[-20:]),
                    started_at=started,
                )
        returncode = proc.wait()
    finally:
        timer.cancel()
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass
        _INSTALL_LOCK.release()

    tail = "\n".join(lines[-20:]).strip()
    elapsed = round(time.monotonic() - started, 1)

    if timed_out:
        msg = f"安装超过了 {int(timeout)} 秒，已放弃"
        full_log = f"{msg}\n{tail}" if tail else msg
        _set_status(key, state="failed", stage=stage, elapsed=elapsed, line=msg, ok=False, log=full_log)
        return {"ok": False, "log": full_log}

    if returncode != 0:
        msg = _format_failure_log(returncode, tail)
        _set_status(key, state="failed", stage=stage, elapsed=elapsed, line=last_line or msg, ok=False, log=msg)
        return {"ok": False, "log": msg}

    stage = "正在校验"
    _set_status(
        key,
        state="running",
        stage=stage,
        elapsed=elapsed,
        line=last_line,
        ok=None,
        log=tail,
        started_at=started,
    )
    # The new directory may be new to the import system.
    importlib.invalidate_caches()
    if not spec.get("isolated"):
        ensure_on_path()
    ok = _verify_installed(key, target)
    elapsed = round(time.monotonic() - started, 1)
    if ok:
        if key == "zotero_mcp":
            from .zotero import client as zotero_client
            zotero_client.reset_discovery()
        log = tail or "已安装"
        _set_status(key, state="done", stage=stage, elapsed=elapsed, line=last_line or "已安装", ok=True, log=log)
        return {"ok": True, "log": log}

    msg = f"安装命令已完成，但仍无法导入组件。\n{tail}" if tail else "安装命令已完成，但仍无法导入组件"
    _set_status(key, state="failed", stage=stage, elapsed=elapsed, line=last_line or msg, ok=False, log=msg)
    return {"ok": False, "log": msg}
