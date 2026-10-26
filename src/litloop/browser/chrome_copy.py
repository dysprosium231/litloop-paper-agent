"""Keep a private copy of the installed Google Chrome on the system's version.

LitLoop can drive a copy of the user's Chrome (``browser_executable`` pointing
at e.g. ``~/.litloop/chrome-real/chrome.exe``) so its process has its own name
for proxy rules and does not collide with the user's browser. Chrome updates
itself; the copy does not, and an outdated build is easier for publishers to
flag as automation. Before each launch the copy is compared with the installed
Chrome and refreshed when the system has moved on.

Windows only. Never blocks a launch: any failure is logged and the existing
copy is used as it is.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from ..log import get_logger

logger = get_logger()

_VERSION_DIR = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
# What a Chrome copy needs; installer leftovers (SetupMetrics, initial_preferences) are skipped.
_TOP_FILES = ("chrome.exe", "chrome_proxy.exe", "chrome.VisualElementsManifest.xml")
_RUNNING_NAME = "litloop-chrome.exe"


def system_chrome_dirs() -> list[Path]:
    """Install locations of Google Chrome on Windows, system-wide first."""
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.environ.get("LOCALAPPDATA", "")]
    return [Path(r) / "Google" / "Chrome" / "Application" for r in roots if r]


def chrome_version(app_dir: Path) -> tuple[int, ...] | None:
    """Highest version folder (e.g. 155.0.8059.41) that holds a chrome.dll."""
    try:
        versions = [tuple(int(x) for x in p.name.split("."))
                    for p in app_dir.iterdir()
                    if p.is_dir() and _VERSION_DIR.match(p.name) and (p / "chrome.dll").exists()]
    except OSError:
        return None
    return max(versions) if versions else None


def _system_chrome() -> tuple[Path, tuple[int, ...]] | None:
    for d in system_chrome_dirs():
        if (d / "chrome.exe").exists():
            v = chrome_version(d)
            if v:
                return d, v
    return None


def is_chrome_copy(exe: Path) -> bool:
    """A chrome.exe outside the install locations, laid out like a Chrome install."""
    exe = Path(exe)
    if exe.name.lower() != "chrome.exe":
        return False
    here = str(exe.parent.resolve()).lower()
    if any(here == str(d.resolve()).lower() for d in system_chrome_dirs() if d.exists()):
        return False
    return chrome_version(exe.parent) is not None


def _copy_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {_RUNNING_NAME}", "/NH"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return False
    return _RUNNING_NAME.lower() in out.lower()


def refresh_chrome_copy(exe: str | Path, *, system: tuple[Path, tuple[int, ...]] | None = None,
                        running: bool | None = None) -> str:
    """Bring the copy at ``exe`` up to the installed Chrome's version.

    Returns what happened: "current", "updated", "skipped-running",
    "not-a-copy", "no-system-chrome" or "failed". The swap is atomic from the
    caller's view: the new build is assembled beside the old one and only then
    renamed into place, so an interrupted copy never leaves a broken browser.
    """
    exe = Path(exe)
    if os.name != "nt" and system is None:
        return "not-a-copy"
    if not is_chrome_copy(exe):
        return "not-a-copy"
    system = system or _system_chrome()
    if not system:
        return "no-system-chrome"
    src_dir, sys_version = system
    copy_dir = exe.parent
    copy_version = chrome_version(copy_dir)
    if copy_version and copy_version >= sys_version:
        return "current"
    if running if running is not None else _copy_running():
        logger.info("chrome copy: system Chrome is %s but the copy (%s) is running; will update next time",
                    _fmt(sys_version), _fmt(copy_version))
        return "skipped-running"

    staging = copy_dir.with_name(copy_dir.name + ".new")
    retired = copy_dir.with_name(copy_dir.name + ".old")
    logger.info("chrome copy: updating %s from %s to %s", copy_dir, _fmt(copy_version), _fmt(sys_version))
    try:
        for leftover in (staging, retired):
            if leftover.exists():
                shutil.rmtree(leftover)
        staging.mkdir(parents=True)
        version_name = _fmt(sys_version)
        shutil.copytree(src_dir / version_name, staging / version_name)
        for name in _TOP_FILES:
            if (src_dir / name).exists():
                shutil.copy2(src_dir / name, staging / name)
        copy_dir.rename(retired)
        try:
            staging.rename(copy_dir)
        except OSError:
            retired.rename(copy_dir)  # put the old copy back
            raise
        shutil.rmtree(retired, ignore_errors=True)
    except Exception as exc:
        logger.warning("chrome copy: update failed (%s); keeping the existing copy", exc)
        shutil.rmtree(staging, ignore_errors=True)
        return "failed"
    logger.info("chrome copy: now on %s", _fmt(sys_version))
    return "updated"


def _fmt(version: tuple[int, ...] | None) -> str:
    return ".".join(str(x) for x in version) if version else "unknown"
