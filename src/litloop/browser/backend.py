"""Unified browser backend: patchright (default), CloakBrowser, or Camoufox.

patchright is the Apache-2.0 open-source Playwright fork that patches the
detectable automation fingerprints (``--enable-automation`` removal,
``Runtime.enable`` leak, ``navigator.webdriver``, ...). Its documented best
practice is ``channel="chrome"`` + ``headless=False`` — i.e. driving the local
Google Chrome, whose kernel auto-updates. CloakBrowser's free tier is pinned
to Chromium 146 (newer 148/150 kernels are Pro-only), so it is kept as an
opt-in fallback. Camoufox is the last-resort fallback: an open-source
anti-detect Firefox (kernel Firefox 152), with a clean headless UA — no
``HeadlessChrome``/``HeadlessFirefox`` leak, verified at runtime.

All backends expose the same Playwright sync API surface (``launch``,
``launch_persistent_context``); the CloakBrowser-only ``humanize`` flag is
accepted everywhere and ignored on patchright.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BACKEND_PATCHRIGHT = "patchright"
BACKEND_CLOAKBROWSER = "cloakbrowser"
BACKEND_CAMOUFOX = "camoufox"
DEFAULT_BACKEND = BACKEND_PATCHRIGHT


# ---------------------------------------------------------------------------
# Availability / resolution
# ---------------------------------------------------------------------------

def is_available(name: str | None = None) -> bool:
    """Check whether a backend package is importable. None → resolved backend."""
    if name is None:
        name = DEFAULT_BACKEND
    try:
        if name == BACKEND_PATCHRIGHT:
            import patchright  # noqa: F401
            return True
        if name == BACKEND_CLOAKBROWSER:
            import cloakbrowser  # noqa: F401
            return True
        if name == BACKEND_CAMOUFOX:
            import camoufox  # noqa: F401
            return True
    except ImportError:
        return False
    return False


def resolve_backend(config: dict[str, Any] | None = None) -> str:
    """Resolve the effective backend from config, falling back gracefully.

    ``browser_backend`` config key (default "patchright"); an unknown value
    warns and resets to the default. Fallback chain:
    patchright → cloakbrowser → camoufox (last resort). A missing patchright
    falls to cloakbrowser; a missing — or below the kernel floor — cloakbrowser
    falls to camoufox when that is installed.
    """
    cfg = config or {}
    requested = str(cfg.get("browser_backend", DEFAULT_BACKEND) or DEFAULT_BACKEND).strip().lower()
    if requested not in (BACKEND_PATCHRIGHT, BACKEND_CLOAKBROWSER, BACKEND_CAMOUFOX):
        logger.warning("browser_backend: unknown backend '%s', using %s", requested, DEFAULT_BACKEND)
        requested = DEFAULT_BACKEND
    if requested == BACKEND_PATCHRIGHT and not is_available(BACKEND_PATCHRIGHT):
        logger.warning(
            "browser_backend: patchright not installed, falling back to cloakbrowser. "
            "Run: pip install patchright"
        )
        requested = BACKEND_CLOAKBROWSER
    if requested == BACKEND_CLOAKBROWSER and not is_available(BACKEND_CLOAKBROWSER):
        logger.warning(
            "browser_backend: cloakbrowser not installed, falling back to camoufox. "
            "Run: pip install cloakbrowser (or pip install camoufox)"
        )
        requested = BACKEND_CAMOUFOX
    if requested == BACKEND_CAMOUFOX and not is_available(BACKEND_CAMOUFOX):
        logger.warning(
            "browser_backend: camoufox not installed, falling back to cloakbrowser. "
            "Run: pip install camoufox"
        )
        requested = BACKEND_CLOAKBROWSER if is_available(BACKEND_CLOAKBROWSER) else BACKEND_PATCHRIGHT
    # A stale cloakbrowser kernel fails the hard gate at launch time; redirect
    # to camoufox when available instead of erroring out (escape hatch kept).
    if requested == BACKEND_CLOAKBROWSER and is_available(BACKEND_CAMOUFOX):
        v = _cloakbrowser_dist_version()
        if v is not None and v < CLOAKBROWSER_REQUIRED_MIN and not os.environ.get("LITLOOP_ALLOW_OLD_CLOAKBROWSER"):
            logger.warning(
                "browser_backend: cloakbrowser %s below required floor %s, using camoufox",
                ".".join(str(x) for x in v), ".".join(str(x) for x in CLOAKBROWSER_REQUIRED_MIN),
            )
            return BACKEND_CAMOUFOX
    return requested


# ---------------------------------------------------------------------------
# Local browser discovery (Chrome/Edge newer than bundled stealth Chromium 146)
# ---------------------------------------------------------------------------
# CloakBrowser's free tier is pinned to Chromium 146 (free builds stopped
# updating after 2026-05; newer 148/150 are Pro-only). Sites behind
# Cloudflare Turnstile repeatedly challenge the stale 146 fingerprint, so we
# prefer a local Chrome/Edge with a newer kernel.
# ---------------------------------------------------------------------------

_BUILTIN_CHROMIUM_MIN = (146, 0)  # bundled stealth Chromium; only swap when newer

_SYSTEM_BROWSER_PATHS_WIN = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

_SYSTEM_BROWSER_NAMES_POSIX = [
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "microsoft-edge", "msedge",
]

_version_cache: dict[str, str] = {}


def _parse_version(v: str) -> tuple[int, ...]:
    """'150.0.7871.187' -> (150, 0, 7871, 187); (0,) when unparseable."""
    nums = [int(n) for n in re.findall(r"\d+", v or "")]
    return tuple(nums[:4]) if nums else (0,)


def _probe_windows_versions() -> dict[str, str]:
    """One PowerShell call: {path: version} for every existing system browser."""
    paths = ",".join(f"'{p}'" for p in _SYSTEM_BROWSER_PATHS_WIN)
    script = (
        f"$paths = @({paths}); "
        "foreach ($p in $paths) { if (Test-Path $p) { "
        'Write-Output ("$p`t" + (Get-Item $p).VersionInfo.ProductVersion) } }'
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return {}
    result: dict[str, str] = {}
    for line in out.splitlines():
        line = line.strip()
        if "\t" in line:
            path, version = line.split("\t", 1)
            result[path.strip()] = version.strip()
    _version_cache.update(result)
    return result


def _probe_posix_versions() -> dict[str, str]:
    result: dict[str, str] = {}
    for name in _SYSTEM_BROWSER_NAMES_POSIX:
        path = shutil.which(name)
        if not path:
            continue
        try:
            out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5).stdout or ""
            m = re.search(r"\d+\.\d+\.\d+\.\d+", out)
            result[path] = m.group(0) if m else "999.0.0.0"
        except Exception:
            result[path] = "999.0.0.0"
    return result


def find_local_browser() -> str | None:
    """Find a local Chrome/Edge newer than the bundled stealth Chromium 146.

    Returns the binary path, or None when the bundled Chromium should be kept.
    """
    versions = _probe_windows_versions() if os.name == "nt" else _probe_posix_versions()
    best: str | None = None
    best_version = _BUILTIN_CHROMIUM_MIN
    for path, version in versions.items():
        parsed = _parse_version(version)
        if parsed > best_version:
            best, best_version = path, parsed
    if best:
        logger.info(
            "browser_backend: using local browser %s (kernel %s) instead of bundled Chromium",
            best, best_version,
        )
    return best


# ---------------------------------------------------------------------------
# CloakBrowser kernel override (CLOAKBROWSER_BINARY_PATH protocol)
# ---------------------------------------------------------------------------

def resolve_browser_binary(config: dict[str, Any] | None = None) -> str | None:
    """Decide which browser binary CloakBrowser should launch.

    Priority: explicit config ``browser_executable`` > an externally pinned
    ``CLOAKBROWSER_BINARY_PATH`` env var (left untouched) > auto-detected
    local Chrome/Edge when ``browser_auto_upgrade`` is on (default).

    Returns a path to set as CLOAKBROWSER_BINARY_PATH, or None to keep the
    bundled stealth Chromium.
    """
    cfg = config or {}
    explicit = str(cfg.get("browser_executable", "") or "").strip()
    if explicit:
        if Path(explicit).exists():
            logger.info("browser_backend: using configured browser_executable: %s", explicit)
            return explicit
        logger.warning("browser_backend: browser_executable '%s' not found, falling back", explicit)
    env_path = os.environ.get("CLOAKBROWSER_BINARY_PATH", "").strip()
    if env_path and Path(env_path).exists():
        return None  # externally pinned — leave as-is
    if cfg.get("browser_auto_upgrade", True):
        return find_local_browser()
    return None


# ---------------------------------------------------------------------------
# patchright launch helpers
# ---------------------------------------------------------------------------

def _patchright_browser_kwargs(config: dict[str, Any] | None) -> dict[str, Any]:
    """Browser selection for the patchright backend.

    Explicit ``browser_executable`` wins; otherwise channel="chrome" (local
    Google Chrome, official best practice). Returns kwargs for launch().
    """
    cfg = config or {}
    explicit = str(cfg.get("browser_executable", "") or "").strip()
    if explicit:
        if Path(explicit).exists():
            # A dedicated browser (e.g. Chrome for Testing) runs under the stable
            # litloop-chrome.exe name; the system Chrome shares its name with the
            # user's own browser and cannot be singled out anyway.
            dedicated = "program files" not in explicit.lower()
            if dedicated and cfg.get("browser_copy_follow_system", True):
                # A copy of the installed Chrome falls behind when Chrome updates.
                from .chrome_copy import refresh_chrome_copy
                refresh_chrome_copy(explicit)
            return {"executable_path": stable_browser_alias(explicit) if dedicated else explicit}
        logger.warning("browser_backend: browser_executable '%s' not found, using channel=chrome", explicit)
    return {"channel": "chrome"}


_DESKTOP_UA_TEMPLATE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
)


def _clean_patchright_ua(browser: Any, headless: bool) -> str | None:
    """Drop the ``HeadlessChrome/...`` UA prefix that headless Chrome sends.

    Returns a desktop-UA string for the launched kernel's real major version
    (so the UA always matches the actual binary), or None when the version
    cannot be read. Only headless launches need this: non-headless Chrome
    already sends the clean desktop UA.
    """
    if not headless:
        return None
    try:
        major = str(browser.version).split(".")[0]
        if not major.isdigit():
            return None
        return _DESKTOP_UA_TEMPLATE.format(major=major)
    except Exception:
        return None


def _launch_patchright(
    config: dict[str, Any] | None,
    headless: bool,
    proxy: Any,
    args: list[str] | None,
    **kwargs: Any,
) -> Any:
    """Launch via patchright (Playwright sync API, patched driver).

    Tries channel=chrome / executable_path first; if the local Chrome is
    missing, retries with the bundled chromium build (patchright install
    chromium). ``humanize`` is a no-op here.
    """
    from patchright.sync_api import sync_playwright

    browser_kwargs = _patchright_browser_kwargs(config)
    pw = sync_playwright().start()
    last_error: Exception | None = None
    for attempt in (browser_kwargs, {}):  # preferred → bundled chromium fallback
        try:
            browser = pw.chromium.launch(
                headless=headless,
                args=args or [],
                proxy=proxy,
                **attempt,
                **kwargs,
            )
            break
        except Exception as exc:  # noqa: BLE001 - try the fallback binary
            last_error = exc
    else:
        pw.stop()
        raise RuntimeError(
            f"patchright could not launch any browser: {last_error}. "
            "Install Google Chrome or run: patchright install chromium"
        ) from last_error

    # Headless Chrome's UA advertises ``HeadlessChrome``, a clear automation
    # signal to anti-bot checks. Inject a clean desktop UA at the context level
    # (renderer-level override — page-level JS UA spoofing is itself detected,
    # verified via sannysoft: JS override trips HEADCHR_UA, context-level UA is
    # "ok"). setdefault keeps an explicit caller-supplied user_agent.
    clean_ua = _clean_patchright_ua(browser, headless)
    if clean_ua:
        _original_new_context = browser.new_context

        def _new_context_with_clean_ua(*a: Any, **kw: Any) -> Any:
            kw.setdefault("user_agent", clean_ua)
            return _original_new_context(*a, **kw)

        browser.new_context = _new_context_with_clean_ua  # type: ignore[method-assign]

    # Stop the Playwright driver when the browser closes (mirrors CloakBrowser).
    original_close = browser.close

    def _close_with_cleanup() -> None:
        try:
            if not _driver_killed(original_close):
                original_close()
        finally:
            try:
                pw.stop()
            except Exception:
                pass

    browser.close = _close_with_cleanup  # type: ignore[method-assign]
    return browser


def _launch_patchright_persistent(
    config: dict[str, Any] | None,
    user_data_dir: str,
    headless: bool,
    proxy: Any,
    args: list[str] | None,
    **kwargs: Any,
) -> Any:
    """Persistent context via patchright, with the same binary fallbacks."""
    from patchright.sync_api import sync_playwright

    browser_kwargs = _patchright_browser_kwargs(config)
    pw = sync_playwright().start()
    last_error: Exception | None = None
    for attempt in (browser_kwargs, {}):
        try:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=headless,
                args=args or [],
                proxy=proxy,
                **attempt,
                **kwargs,
            )
            break
        except Exception as exc:  # noqa: BLE001 - try the fallback binary
            last_error = exc
    else:
        pw.stop()
        raise RuntimeError(
            f"patchright could not launch any browser: {last_error}. "
            "Install Google Chrome or run: patchright install chromium"
        ) from last_error

    original_close = context.close

    def _close_with_cleanup() -> None:
        try:
            if not _driver_killed(original_close):
                original_close()
        finally:
            try:
                pw.stop()
            except Exception:
                pass

    context.close = _close_with_cleanup  # type: ignore[method-assign]
    return context


CLOAKBROWSER_REQUIRED_MIN = (0, 5, 9)  # keep in sync with the pyproject extra floor


def _cloakbrowser_dist_version() -> tuple[int, ...] | None:
    try:
        from importlib.metadata import version as _dist_version

        raw = _dist_version("cloakbrowser")
    except Exception:
        return None
    parts: list[int] = []
    for token in raw.split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _enforce_cloakbrowser_floor() -> None:
    """Hard gate: stale stealth kernels get flagged by publishers, so browser
    launches refuse to run on an outdated cloakbrowser. Escape hatch for
    offline/emergency use: LITLOOP_ALLOW_OLD_CLOAKBROWSER=1."""
    import os

    if os.environ.get("LITLOOP_ALLOW_OLD_CLOAKBROWSER"):
        return
    raw = None
    try:
        from importlib.metadata import version as _dist_version

        raw = _dist_version("cloakbrowser")
    except Exception:
        pass
    v = _cloakbrowser_dist_version()
    if v is None or v >= CLOAKBROWSER_REQUIRED_MIN:
        return
    required = ".".join(str(x) for x in CLOAKBROWSER_REQUIRED_MIN)
    raise RuntimeError(
        f"cloakbrowser {raw} is below the required minimum {required} — stale stealth "
        "kernels get flagged by publishers. Run: pip install -U cloakbrowser "
        "(escape hatch: set LITLOOP_ALLOW_OLD_CLOAKBROWSER=1)"
    )


STABLE_BROWSER_NAME = "litloop-chrome.exe"


def stable_browser_alias(binary: str | Path) -> str:
    """Return a hard link named litloop-chrome.exe next to a dedicated Chromium binary.

    Gives every Chromium process the plugin launches a stable, unique process
    name, so proxy/TUN bypass rules can target it by name: versioned install
    directories break per-path rules on upgrade, and a bare "chrome.exe" rule
    would also catch the user's own Chrome. Recreated automatically when the
    binary is replaced. Windows only; returns the original path on failure.
    """
    original = Path(binary)
    if os.name != "nt" or original.name.lower() == STABLE_BROWSER_NAME:
        return str(original)
    try:
        alias = original.with_name(STABLE_BROWSER_NAME)
        if not alias.exists() or alias.stat().st_size != original.stat().st_size                 or alias.stat().st_mtime < original.stat().st_mtime:
            if alias.exists():
                alias.unlink()
            try:
                os.link(original, alias)
            except OSError:
                shutil.copy2(original, alias)
            logger.info("browser_backend: created %s -> %s", alias, original.name)
        return str(alias)
    except Exception as exc:  # never block a launch over the alias
        logger.info("browser_backend: stable browser name unavailable (%s)", exc)
        return str(original)


def _use_stable_cloak_binary() -> None:
    """Point CloakBrowser at a litloop-chrome.exe alias of its bundled Chromium."""
    if os.name != "nt" or os.environ.get("CLOAKBROWSER_BINARY_PATH"):
        return  # non-Windows, or a binary was pinned explicitly (local Chrome, user build)
    try:
        from cloakbrowser.download import ensure_binary
        os.environ["CLOAKBROWSER_BINARY_PATH"] = stable_browser_alias(ensure_binary())
    except Exception as exc:
        logger.info("browser_backend: stable browser name unavailable (%s)", exc)

def _launch_cloakbrowser(
    headless: bool,
    proxy: Any,
    args: list[str] | None,
    humanize: bool,
    **kwargs: Any,
) -> Any:
    _enforce_cloakbrowser_floor()
    _use_stable_cloak_binary()
    from cloakbrowser import launch

    return launch(headless=headless, humanize=humanize, args=args, proxy=proxy, **kwargs)


def _launch_cloakbrowser_persistent(
    user_data_dir: str,
    headless: bool,
    proxy: Any,
    args: list[str] | None,
    humanize: bool,
    **kwargs: Any,
) -> Any:
    _enforce_cloakbrowser_floor()
    _use_stable_cloak_binary()
    from cloakbrowser import launch_persistent_context

    return launch_persistent_context(
        user_data_dir=user_data_dir,
        headless=headless,
        humanize=humanize,
        args=args,
        proxy=proxy,
        **kwargs,
    )


def _launch_camoufox(
    headless: bool,
    proxy: Any,
    args: list[str] | None,
    humanize: bool,
    **kwargs: Any,
) -> Any:
    """Launch via Camoufox (anti-detect Firefox, Playwright API).

    Returns a vanilla ``playwright.sync_api.Browser`` so the rest of the stack
    (contexts, cookies, close) works unchanged. Handles its own Playwright
    driver lifecycle, mirroring the other backends.

    Callers pass Chromium-style flags (--disable-blink-features=..., ...);
    Camoufox is a Firefox fork that configures its own anti-fingerprinting,
    so caller args are dropped rather than handed to the Firefox binary
    (unknown flags end up treated as open-URL arguments there).
    """
    if args:
        logger.debug("browser_backend: camoufox ignores %d caller arg(s) (Chromium flags)", len(args))
    from camoufox import DefaultAddons, NewBrowser
    from playwright.sync_api import sync_playwright

    opts = dict(kwargs)
    # The bundled uBlock addon downloads at build time and fails (or is
    # rate-limited) -> exclude it, it is not needed for download sessions.
    opts.setdefault("exclude_addons", [DefaultAddons.UBO])
    pw = sync_playwright().start()
    try:
        browser = NewBrowser(
            pw,
            headless=headless,
            humanize=humanize,
            args=[],  # chromium flags dropped — see docstring
            proxy=proxy,
            **opts,
        )
    except Exception:
        pw.stop()
        raise

    original_close = browser.close

    def _close_with_cleanup() -> None:
        try:
            if not _driver_killed(original_close):
                original_close()
        finally:
            try:
                pw.stop()
            except Exception:
                pass

    browser.close = _close_with_cleanup  # type: ignore[method-assign]
    return browser


def _launch_camoufox_persistent(
    user_data_dir: str,
    headless: bool,
    proxy: Any,
    args: list[str] | None,
    humanize: bool,
    **kwargs: Any,
) -> Any:
    """Persistent context via Camoufox (returns a BrowserContext).

    Caller args (Chromium flags) are dropped — see _launch_camoufox.
    """
    if args:
        logger.debug("browser_backend: camoufox ignores %d caller arg(s) (Chromium flags)", len(args))
    from camoufox import DefaultAddons, NewBrowser
    from playwright.sync_api import sync_playwright

    opts = dict(kwargs)
    opts.setdefault("exclude_addons", [DefaultAddons.UBO])
    pw = sync_playwright().start()
    try:
        context = NewBrowser(
            pw,
            persistent_context=True,
            user_data_dir=user_data_dir,
            headless=headless,
            humanize=humanize,
            args=[],  # chromium flags dropped — see _launch_camoufox
            proxy=proxy,
            **opts,
        )
    except Exception:
        pw.stop()
        raise

    original_close = context.close

    def _close_with_cleanup() -> None:
        try:
            if not _driver_killed(original_close):
                original_close()
        finally:
            try:
                pw.stop()
            except Exception:
                pass

    context.close = _close_with_cleanup  # type: ignore[method-assign]
    return context


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def _proxy_from_config(config: dict[str, Any] | None) -> Any:
    """Playwright proxy dict from config, or None.

    Camoufox (Firefox) ignores Chromium's --proxy-server launch arg, so the
    configured egress (browser_static_proxy, else network_proxy) must be
    passed explicitly — otherwise camoufox always goes direct and every
    proxy-required host times out.
    """
    cfg = config or {}
    proxy = (str(cfg.get("browser_static_proxy", "") or "").strip()
             or str(cfg.get("network_proxy", "") or "").strip())
    return {"server": proxy} if proxy else None


def _resolve_launch_config(config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Fall back to the saved config when a caller passes none, so ``browser_backend``
    and ``browser_auto_upgrade`` are honored on every launch path."""
    if config is not None:
        return config
    try:
        from ..config import load_config
        return load_config()
    except Exception:
        return None


def _direct_args(backend: str, proxy: Any, args: list[str] | None,
                 config: dict[str, Any] | None = None) -> list[str] | None:
    """Chromium backends bypass the OS/system proxy unless an explicit proxy is given.

    With a per-host proxy exception configured (host_proxy_url/_hosts), a PAC
    script sends only those domains through it — falling back to DIRECT if the
    proxy goes away mid-session — and everything else stays direct.
    """
    if proxy is not None or backend == BACKEND_CAMOUFOX:
        return args
    args = list(args or [])
    if not any(a.startswith(("--no-proxy-server", "--proxy-server", "--proxy-pac-url")) for a in args):
        from ..network import grey_hosts, grey_proxy, host_proxy
        url, hosts = host_proxy(config)
        grey = grey_proxy(config)
        if grey:  # Sci-Hub / LibGen pages opened in the browser go through it too
            hosts = (tuple(hosts) if url == grey else ()) + grey_hosts(config)
            url = grey
        args.append(_pac_arg(url, hosts) if url else "--no-proxy-server")
    return args


def _pac_arg(proxy_url: str, hosts: tuple[str, ...]) -> str:
    import base64
    from urllib.parse import urlparse
    u = urlparse(proxy_url)
    kind = "SOCKS5" if (u.scheme or "").startswith("socks") else "PROXY"
    cond = " || ".join(f'host == "{h}" || dnsDomainIs(host, ".{h}")' for h in hosts)
    pac = (f"function FindProxyForURL(url, host) {{ if ({cond}) "
           f'return "{kind} {u.hostname}:{u.port}; DIRECT"; return "DIRECT"; }}')
    return "--proxy-pac-url=data:application/x-ns-proxy-autoconfig;base64," + base64.b64encode(pac.encode()).decode()


def _launch_unregistered(
    *,
    headless: bool = True,
    proxy: Any = None,
    args: list[str] | None = None,
    humanize: bool = True,
    config: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Launch the resolved backend's stealth browser (Playwright sync API).

    ``humanize`` is accepted for CloakBrowser compatibility and ignored on
    the patchright backend. Returns the same object as
    ``playwright.chromium.launch()`` / ``cloakbrowser.launch()``.
    """
    config = _resolve_launch_config(config)
    backend = resolve_backend(config)
    if backend == BACKEND_CAMOUFOX and proxy is None:
        proxy = _proxy_from_config(config)
    args = _direct_args(backend, proxy, args, config)
    if backend == BACKEND_PATCHRIGHT:
        if humanize:
            logger.debug("browser_backend: humanize not supported by patchright, ignored")
        return _launch_patchright(config, headless=headless, proxy=proxy, args=args, **kwargs)
    if backend == BACKEND_CAMOUFOX:
        return _launch_camoufox(headless=headless, proxy=proxy, args=args, humanize=humanize, **kwargs)
    return _launch_cloakbrowser(headless=headless, proxy=proxy, args=args, humanize=humanize, **kwargs)


def _launch_persistent_unregistered(
    user_data_dir: str,
    *,
    headless: bool = True,
    proxy: Any = None,
    args: list[str] | None = None,
    humanize: bool = True,
    config: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Launch the resolved backend with a persistent profile.

    Same contract as ``playwright.chromium.launch_persistent_context()``.
    """
    config = _resolve_launch_config(config)
    backend = resolve_backend(config)
    if backend == BACKEND_CAMOUFOX and proxy is None:
        proxy = _proxy_from_config(config)
    args = _direct_args(backend, proxy, args, config)
    if backend != BACKEND_CAMOUFOX:
        # A profile whose browser was killed (the app restarted mid-download)
        # would greet the user with Chrome's "Restore pages?" bubble.
        args = [*(args or []), "--hide-crash-restore-bubble"]
    if backend == BACKEND_PATCHRIGHT:
        if humanize:
            logger.debug("browser_backend: humanize not supported by patchright, ignored")
        return _launch_patchright_persistent(
            config, user_data_dir, headless=headless, proxy=proxy, args=args, **kwargs
        )
    if backend == BACKEND_CAMOUFOX:
        return _launch_camoufox_persistent(
            user_data_dir, headless=headless, proxy=proxy, args=args, humanize=humanize, **kwargs
        )
    return _launch_cloakbrowser_persistent(
        user_data_dir, headless=headless, proxy=proxy, args=args, humanize=humanize, **kwargs
    )


def browser_info(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Human-readable info about the current backend for diagnostics.

    Returns {"backend": ..., "binary": ..., "version": ...}.
    """
    cfg = config or {}
    backend = resolve_backend(cfg)
    info: dict[str, Any] = {"backend": backend, "binary": "", "version": ""}
    if backend == BACKEND_PATCHRIGHT:
        binary = ""
        explicit = str(cfg.get("browser_executable", "") or "").strip()
        if explicit and Path(explicit).exists():
            binary = explicit
        else:
            versions = _probe_windows_versions() if os.name == "nt" else _probe_posix_versions()
            for path, version in versions.items():
                if "chrome" in path.lower():
                    binary, info["version"] = path, version
                    break
            else:
                binary = "channel=chrome"
        info["binary"] = binary
        return info
    if backend == BACKEND_CAMOUFOX:
        try:
            from camoufox.pkgman import INSTALL_DIR, Version
            installed = Version.from_path()
            info["binary"] = str(INSTALL_DIR / "camoufox.exe") if os.name == "nt" else str(INSTALL_DIR)
            info["version"] = installed.full_string if installed.is_supported() else "unsupported"
        except Exception:
            info["binary"] = "camoufox"
            info["version"] = "?"
        return info
    # CloakBrowser backend
    binary = resolve_browser_binary(cfg)
    try:
        from cloakbrowser.config import get_chromium_version
        bundled = get_chromium_version()
    except Exception:
        bundled = "?"
    if binary:
        info["binary"] = binary
        info["version"] = f"local (bundled {bundled})"
    else:
        info["binary"] = "bundled stealth Chromium"
        info["version"] = bundled
    return info


# ---------------------------------------------------------------------------
# Launch registry: lets a finished download close the windows its still-running
# (waived) sources opened. Racing returns on the first success, but the other
# source threads keep their visible browsers open until they time out.
# ---------------------------------------------------------------------------

import threading as _threading
import time as _time

_LAUNCHED: list[tuple[float, Any, str | None]] = []  # (launched at, browser, owner)


def _guard_dead_driver() -> None:
    """Make every sync browser call fail once its driver is gone.

    patchright's sync wrapper waits for an answer by switching to the
    dispatcher greenlet until the task is done; after the driver dies (killed
    by close_browsers_since on cancel, skip or the school-route watchdog) that
    greenlet is dead, the switch returns at once and the wait spins forever
    (2026-10-17: a cancelled download stuck in browser.close() at 100% CPU).
    """
    try:
        from patchright._impl import _sync_base
    except Exception:
        return
    if getattr(_sync_base.SyncBase._sync, "_litloop_guarded", False):
        return
    import asyncio
    import greenlet
    from patchright._impl._errors import Error

    def _sync(self: Any, coro: Any) -> Any:
        if self._loop.is_closed():
            coro.close()
            raise Error("Event loop is closed! Is Playwright already stopped?")
        g_self = greenlet.getcurrent()
        task = self._loop.create_task(coro)
        task.add_done_callback(lambda _: g_self.switch())
        while not task.done():
            if self._dispatcher_fiber.dead:
                task.cancel()
                raise Error("the browser driver is gone")
            self._dispatcher_fiber.switch()
        asyncio._set_running_loop(self._loop)
        return task.result()

    _sync._litloop_guarded = True  # type: ignore[attr-defined]
    _sync_base.SyncBase._sync = _sync  # type: ignore[method-assign]


_guard_dead_driver()
_LAUNCHED_LOCK = _threading.Lock()


# Set by the download orchestrator: returns the paper (DOI key) the calling
# thread works for, so a paper's skip, end or watchdog closes only its own
# browsers, not another paper's.
OWNER: Any = None
# The owner a pool thread inherited from the thread that submitted its task
# (see carry_owner): pool threads have none of the orchestrator's thread-locals.
_OWNER_TLS = _threading.local()


def _owner() -> str | None:
    try:
        owner = OWNER() if OWNER is not None else None
    except Exception:
        owner = None
    return owner or getattr(_OWNER_TLS, "owner", None)


def current_owner() -> str | None:
    """The paper the calling thread works for, if any."""
    return _owner()


def carry_owner(fn: Any) -> Any:
    """Wrap ``fn`` for another thread (a pool worker): browsers it launches belong
    to the paper the submitting thread works for, so that paper's skip or end
    closes them (2026-10-18: Sci-Hub's mirror race opened windows nobody owned)."""
    owner = _owner()

    def run(*args: Any, **kwargs: Any) -> Any:
        prev = getattr(_OWNER_TLS, "owner", None)
        _OWNER_TLS.owner = owner
        try:
            return fn(*args, **kwargs)
        finally:
            _OWNER_TLS.owner = prev
    return run


def _gone(obj: Any) -> bool:
    """Whether a registered browser is already dead (killed, or its driver exited)."""
    conn = _connection_of(obj)
    if conn is None:
        return False
    if getattr(conn, "_litloop_killed", False):
        return True
    try:
        return conn._transport._proc.returncode is not None  # type: ignore[union-attr]
    except Exception:
        return False


# Owner of a browser that several papers take turns on (a Sci-Hub pool thread's
# browser, a publisher group's window, a fetcher's browser): no paper's skip or
# end closes it, since another paper may be using it; a cancel (owner=None) does.
SHARED = "<shared>"
_SHARED_TLS = _threading.local()


class shared:
    """``with shared():`` browsers launched inside are SHARED (2026-10-19, DeepSeek
    review: a Sci-Hub worker reuses its browser, so skipping the paper that
    happened to launch it killed it under the next paper)."""

    def __enter__(self) -> None:
        self._prev = getattr(_SHARED_TLS, "on", False)
        _SHARED_TLS.on = True

    def __exit__(self, *exc: Any) -> None:
        _SHARED_TLS.on = self._prev


def _remember(obj: Any) -> Any:
    owner = SHARED if getattr(_SHARED_TLS, "on", False) else _owner()
    with _LAUNCHED_LOCK:
        # Drop the dead ones, so a browser whose owner never closed it (closed by
        # its own code, or by a crash) does not stay referenced for ever.
        _LAUNCHED[:] = [e for e in _LAUNCHED if not _gone(e[1])]
        _LAUNCHED.append((_time.perf_counter(), obj, owner))
    return obj


# Set by the download orchestrator: returns False when the calling thread works
# for a download that has already finished (a waived racing source). Such
# threads must not (re)launch browsers -- the shared browser would otherwise be
# relaunched after the finished download closed it.
LAUNCH_GUARD: Any = None


def _check_launch_guard() -> None:
    if LAUNCH_GUARD is not None and not LAUNCH_GUARD():
        raise RuntimeError("download already finished; not launching a browser for a waived source")


def launch(**kwargs: Any) -> Any:
    """Launch the resolved backend's stealth browser and register it (see ``close_browsers_since``)."""
    _check_launch_guard()
    return _remember(_launch_unregistered(**kwargs))


def launch_persistent_context(user_data_dir: str, **kwargs: Any) -> Any:
    """Launch a persistent-profile context and register it (see ``close_browsers_since``)."""
    _check_launch_guard()
    return _remember(_launch_persistent_unregistered(user_data_dir, **kwargs))


def _connection_of(obj: Any) -> Any:
    try:
        return obj._impl_obj._connection  # type: ignore[attr-defined]
    except Exception:
        return None


def _driver_killed(bound_close: Any) -> bool:
    """Whether close_browsers_since killed this object's driver: close() would
    then wait forever for an answer (2026-10-17: a cancelled download spun in
    browser.close() after its driver was killed)."""
    conn = _connection_of(getattr(bound_close, "__self__", None))
    return bool(conn is not None and getattr(conn, "_litloop_killed", False))


def close_browsers_since(t0: float = 0.0, owner: str | None = None) -> int:
    """Force-close every browser launched at or after ``time.perf_counter()`` value ``t0``
    (only those launched for ``owner``, a paper's DOI key, when it is given).

    Sync-API objects are bound to the thread that created them, so they cannot
    be closed from here; kill the driver process tree instead. The owning
    threads then fail fast, and the shared per-thread browser is relaunched on
    next use (browser_engine checks ``is_connected``).
    """
    from .engine import _tree_kill

    def hit(ts: float, who: str | None) -> bool:
        return ts >= t0 and (owner is None or who == owner)

    with _LAUNCHED_LOCK:
        targets = [obj for ts, obj, who in _LAUNCHED if hit(ts, who)]
        _LAUNCHED[:] = [e for e in _LAUNCHED if not hit(e[0], e[2])]
    closed = 0
    for obj in targets:
        conn = _connection_of(obj)
        try:
            proc = conn._transport._proc  # type: ignore[union-attr]
        except Exception:
            proc = None
        if conn is not None:
            conn._litloop_killed = True  # its close() must not wait for the dead driver
        try:
            _tree_kill(proc)
            closed += 1
        except Exception:
            pass
    return closed
