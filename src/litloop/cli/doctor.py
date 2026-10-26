"""Setup diagnostic: detect system environment and provide install commands."""

from __future__ import annotations

import platform
import shutil
import socket
from typing import Any


def detect_system() -> dict[str, Any]:
    """Detect OS, arch, and relevant system info."""
    sys_info: dict[str, Any] = {
        "os": platform.system(),
        "os_version": platform.version(),
        "arch": platform.machine(),
        "python": platform.python_version(),
    }

    # Windows edition
    if sys_info["os"] == "Windows":
        try:
            import ctypes
            is_64 = ctypes.sizeof(ctypes.c_voidp) == 8
            sys_info["windows_arch"] = "64-bit" if is_64 else "32-bit"
        except Exception:
            pass

    return sys_info


def check_tor() -> dict[str, Any]:
    """Check Tor installation and connectivity."""
    result: dict[str, Any] = {"installed": False, "running": False, "port": 1080, "embedded": False}

    # Check embedded Tor binary
    from ..config import load_config
    config = load_config()
    from ..tor.embedded import _tor_binary
    embedded = _tor_binary(config)
    if embedded:
        result["installed"] = True
        result["embedded"] = True
        result["path"] = str(embedded)

    # Check if tor binary exists in system PATH
    tor_path = shutil.which("tor")
    if tor_path:
        result["installed"] = True
        result["path"] = tor_path

    # Check if Tor is listening on common ports
    for port in (1080, 9050, 9150):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                result["running"] = True
                result["port"] = port
                break
        except (ConnectionRefusedError, OSError):
            pass

    return result


def check_browser() -> dict[str, Any]:
    """Check CloakBrowser connectivity."""
    result: dict[str, Any] = {"running": False}
    try:
        from ..browser.engine import is_available
        from ..config import load_config
        result["running"] = is_available(load_config())
    except Exception:
        pass
    return result


def get_install_commands(sys_info: dict[str, Any], tor: dict) -> dict[str, Any]:
    """Return system-specific installation commands for missing components."""
    os_name = sys_info["os"]
    commands: dict[str, Any] = {}

    if not tor["running"]:
        # Recommend embedded Tor (no Docker needed)
        commands["tor"] = {
            "method": "embedded",
            "reason": "自动下载 Tor Expert Bundle 到 ~/.litloop/tor/，无需 Docker 或系统安装",
            "steps": [
                "1. 运行 litloop_tor_install 自动下载 Tor",
                "2. 运行 litloop_tor_start 启动 Tor SOCKS5 代理",
                "3. 下载时使用 use_tor=true",
            ],
            "bridges": "在受限网络中，使用 litloop_tor_start(use_bridges=true) 启用 obfs4 桥接",
            "alternatives": [],
        }
        if os_name == "Darwin":
            commands["tor"]["alternatives"].append({
                "method": "homebrew",
                "steps": ["brew install tor", "brew services start tor"],
            })
        elif os_name != "Windows":
            commands["tor"]["alternatives"].append({
                "method": "apt",
                "steps": ["sudo apt install tor", "sudo systemctl enable --now tor"],
            })
        elif os_name == "Windows":
            commands["tor"]["alternatives"].append({
                "method": "manual",
                "steps": ["下载 Tor Expert Bundle: https://www.torproject.org/download/tor/"],
            })

    return commands


def setup_check() -> dict[str, Any]:
    """Full setup diagnostic. (Docker is not checked: the Docker setup was removed
    on 2026-10-23, as a container cannot show the user the browser to log in.)"""
    sys_info = detect_system()
    tor = check_tor()
    browser = check_browser()

    commands = get_install_commands(sys_info, tor)

    # Overall readiness
    issues = []
    if not tor["running"]:
        issues.append("Tor 未运行，Sci-Hub/LibGen 通过 Tor 访问受限")
    if not browser["running"]:
        issues.append("CloakBrowser 未运行，Cloudflare 防护可能无法绕过")

    readiness = "ready" if not issues else "partial"
    if not tor["running"] and not browser["running"]:
        readiness = "limited"

    return {
        "system": sys_info,
        "tor": tor,
        "browser": browser,
        "install_commands": commands,
        "issues": issues,
        "readiness": readiness,
    }
