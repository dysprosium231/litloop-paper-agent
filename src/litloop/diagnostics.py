"""Everything a bug report needs, in one zip: no PDFs, no credentials.

The app's "导出诊断包" button and `litloop diagnose` both land here. The point
is that a user who says "it doesn't work" can hand over one file that says
which version, which Python, which settings (keys masked) and what the last
few hundred log lines were — without us asking them to run a dozen commands.
"""

from __future__ import annotations

import json
import platform
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .config import DATA_DIR, load_config
from .log import get_logger, recent

log = get_logger()

_SECRET_NAME = re.compile(r"(api_key|token|secret|password|cookie|session_id)", re.I)
_KEEP_EMPTY = object()


def redact(value: Any, key: str = "") -> Any:
    """Mask anything that looks like a credential, keep the shape of everything else."""
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if _SECRET_NAME.search(key) and isinstance(value, str) and value.strip():
        return "***"
    return value


def _components() -> dict[str, Any]:
    try:
        from .optional import COMPONENTS, installed
    except Exception:
        return {}
    return {key: {"label": info.get("label", key), "installed": bool(installed(key))}
            for key, info in COMPONENTS.items()}


def _uv() -> str:
    try:
        from .optional import uv_command
        found = uv_command()
    except Exception:
        return ""
    return str(found) if found else ""


def _data_dir_summary() -> dict[str, Any]:
    """Counts only — enough to see whether something is empty or huge."""
    summary: dict[str, Any] = {"path": str(DATA_DIR)}
    for name in ("papers", "downloads", "screening", "exports", "cache", "packages"):
        folder = DATA_DIR / name
        if not folder.is_dir():
            summary[name] = 0
            continue
        try:
            summary[name] = sum(1 for item in folder.iterdir() if item.is_file())
        except OSError:
            summary[name] = -1
    return summary


def collect(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """The JSON that goes into the bundle."""
    config = load_config() if config is None else config
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "app": {
            "version": __version__,
            "python": platform.python_version(),
            "executable": sys.executable,
            "frozen": bool(getattr(sys, "frozen", False)),
        },
        "system": {
            "os": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "platform": platform.platform(terse=True),
        },
        "paths": _data_dir_summary(),
        "settings": redact(dict(config)),
        "components": _components(),
        "uv": _uv(),
        "log": recent(300),
    }


def _bundle_name(when: datetime) -> str:
    return f"litloop-diagnostics-{when.strftime('%Y%m%d-%H%M%S')}.zip"


def write_bundle(path: str | Path | None = None, config: dict[str, Any] | None = None) -> Path:
    """Write the zip and return its path. `path` may be a directory or a file name."""
    now = datetime.now()
    target = Path(path) if path else (DATA_DIR / "exports" / _bundle_name(now))
    if target.is_dir() or (not target.suffix and not target.exists()):
        target = target / _bundle_name(now)
    target.parent.mkdir(parents=True, exist_ok=True)

    data = collect(config)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagnostics.json", json.dumps(data, ensure_ascii=False, indent=2))
        archive.writestr("log.txt", "\n".join(data["log"]) or "(没有日志)")
        archive.writestr("README.txt", _README)
    log.info("诊断包写到了 %s", target)
    return target


_README = """这个压缩包里有什么
==================
diagnostics.json  版本、系统信息、数据目录里各类文件的数量、脱敏后的设置、可选组件与 uv 的状态
log.txt           本次运行的最近 300 行日志
不含 PDF、不含 Zotero 数据、不含任何 API Key 与 cookie（设置里的密钥已显示为 ***）。
"""
