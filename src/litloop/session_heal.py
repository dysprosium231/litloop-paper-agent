"""Institutional session self-healing.

Before a download tries the institutional phase, validate the saved sessions
and, when ``auto_relogin`` is enabled, refresh expired ones by opening the
login browser. Conservative by design: only a session that previously existed
(cookies on disk) and is clearly expired triggers the browser; fresh users
and network-unreachable states never do.
"""

from __future__ import annotations

import threading

from typing import Any

from .log import get_logger

log = get_logger()


def _webvpn_configured(config: dict[str, Any]) -> bool:
    return bool(config.get("vpnsci_enabled", False)) and bool(
        config.get("vpnsci_base_url") or config.get("vpnsci_school")
    )


def ensure_webvpn_session(config: dict[str, Any]) -> dict[str, Any]:
    """Validate the WebVPN session and refresh it when clearly expired.

    Returns a status report whose ``status`` is one of ``disabled``,
    ``valid``, ``none`` (no saved cookies — never auto-login), ``unreachable``
    (network failure — never auto-login), ``refreshed``, or ``login_failed``.
    """
    if not config.get("auto_relogin", True):
        return {"status": "disabled", "auto_relogin": False}
    from .sources.instsci import instsci_login, session_status

    status = session_status(config)
    if status in ("valid", "none", "unreachable"):
        return {"status": status, "auto_relogin": True}
    log.info("   [session-heal] WebVPN session expired, opening login browser...")
    from .download import progress as _pr
    school = str(config.get("vpnsci_school") or "学校").strip()
    _pr.set_attention("webvpn-login", f"WebVPN 登录已过期，请在弹出的浏览器窗口中重新登录{school} WebVPN",
                      phase="等待你的操作")
    try:
        ok = instsci_login(config)
    finally:
        _pr.clear_attention("webvpn-login")
    after = session_status(config)
    return {
        "status": "refreshed" if ok and after == "valid" else "login_failed",
        "auto_relogin": True,
        "validated_after": after,
    }


_HEAL_LOCK = threading.Lock()


def ensure_institutional_sessions(config: dict[str, Any], *, use_vpnsci: bool = True) -> dict[str, Any]:
    """Validate/refresh institutional sessions before a download attempt.

    CARSI sessions self-heal inside ``CARSIClient.login`` (cookie freshness
    check with automatic browser fallback), so only WebVPN needs a hook here.
    """
    report: dict[str, Any] = {}
    if use_vpnsci and _webvpn_configured(config):
        # Several downloads at once may all find the session stale: one of them
        # refreshes it (a login window at most once), the others then find it fresh.
        with _HEAL_LOCK:
            report["webvpn"] = ensure_webvpn_session(config)
    return report
