"""Phase 1/2 source assembly, institutional routing sequence, watchdog, and failure guidance.

Split out of `sources/__init__.py` (2026-11-20); `sources/__init__.py` re-exports
all symbols below so callers and tests keep importing from there.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

from ..identifiers import safe_filename
from ..log import get_logger
from .core_api import try_core
from .crossref import try_crossref_page_scrape
from .europepmc import try_europepmc, try_pmc
from .libgen import try_libgen
from .oa_discovery import try_doaj
from .openaire import try_openaire
from .openalex import try_openalex_content_api, try_openalex_oa
from .publishers import get_publisher_fast_sources
from .scibban import try_scibban
from .scihub import try_scihub
from .semantic_scholar import try_semanticscholar
from .unpaywall import try_unpaywall

log = get_logger()


def _pkg() -> Any:
    return sys.modules[__package__]


def _build_free_sources(doi: str, config: dict[str, Any]) -> list[tuple[Any, str]]:
    """Build Phase 1 sources: all free/OA/grey sources, sorted by adaptive score.

    Respects config['download_strategy']:
      - fastest:     all sources, EMA-sorted (default)
      - scihub_first: all sources, but grey sources sorted first
      - scihub_only:  only SciBban + Sci-Hub + LibGen
      - oa_first:     all sources, but OA sources sorted before grey
      - legal_only:   exclude grey sources (SciBban, Sci-Hub, LibGen)
    """
    from .scoring import sort_sources

    strategy = config.get("download_strategy", "fastest")

    publisher_fast = get_publisher_fast_sources(doi)
    _fast_names = {label for _, label in publisher_fast}

    extra_fast = []
    for fn, label in [
        (try_unpaywall, "Unpaywall"),
        (try_openalex_oa, "OpenAlexOA"),
        (try_semanticscholar, "SemanticScholar"),
        (try_openaire, "OpenAIRE"),
    ]:
        if label not in _fast_names:
            extra_fast.append((fn, label))

    from .elsevier_api import fulltext_enabled
    if not fulltext_enabled(config):  # off in settings, or the key has no full-text entitlement
        publisher_fast = [(fn, label) for fn, label in publisher_fast if label != "ElsevierAPI"]
    legal_sources = publisher_fast + extra_fast
    # Every publisher's own site is tried here, in phase 1, from this exit:
    # those without a browser lane of their own (PNAS, Optica, World Scientific,
    # unlisted ones) get the generic one. Open-access hosts need none.
    if not any("Browser" in label for label in _fast_names):
        from .publishers import get_publisher
        if get_publisher(doi) not in _pkg()._OPEN_HOSTS:
            from .publishers import _FN_MAP
            legal_sources.append((_FN_MAP["GenericBrowser"], "GenericBrowser"))
    # Publishers whose site is not worth phase 1 (publisher_site_skip_prefixes):
    # the school routes reach the same content faster.
    skip = tuple(str(p).strip().lower().rstrip("/") + "/" for p in config.get("publisher_site_skip_prefixes") or [] if p)
    if skip and doi.lower().startswith(skip):
        legal_sources = [(fn, label) for fn, label in legal_sources if "Browser" not in label]
        log.info("   Publisher site skipped in phase 1 (publisher_site_skip_prefixes)")
    legal_sources += [(try_doaj, "DOAJ"), (try_crossref_page_scrape, "CrossrefPage")]
    legal_sources += [(try_europepmc, "EuropePMC"), (try_core, "CORE")]
    if not config.get("_batch"):
        legal_sources.append((try_pmc, "PMC"))

    if config.get("openalex_api_key"):
        legal_sources.append((try_openalex_content_api, "OpenAlexContent"))

    # Grey / shadow-library sources (Sci-Hub, SciBban, LibGen) are all gated
    # by scihub_enabled and are off unless the user turned it on (CHARTER: the
    # user must choose them explicitly; config.DEFAULT_CONFIG has False). A
    # partial config dict without the key therefore means off, never on.
    grey_sources: list[tuple[Any, str]] = []
    if config.get("scihub_enabled", False):
        from .scihub import recently_absent, too_new
        new = too_new(doi, config)  # published in or after scihub_skip_from_year
        if not new:
            grey_sources.append((try_scibban, "SciBban"))  # a Sci-Hub mirror
        grey_sources.append((try_libgen, "LibGen"))
        if not new and not recently_absent(doi):  # Sci-Hub said within the hour it does not have it
            grey_sources.append((try_scihub, "Sci-Hub"))

    if strategy == "scihub_only":
        # Only Sci-Hub (not SciBban, not LibGen)
        return sort_sources([(try_scihub, "Sci-Hub")]) if config.get("scihub_enabled", False) else []
    elif strategy == "grey_only":
        return sort_sources(grey_sources)
    elif strategy == "legal_only":
        return sort_sources(legal_sources)
    elif strategy == "scihub_first":
        # Grey sources first, then legal
        return sort_sources(grey_sources) + sort_sources(legal_sources)
    elif strategy == "oa_first":
        # Legal sources first, then grey
        return sort_sources(legal_sources) + grey_sources
    else:
        # fastest: all sources EMA-sorted
        return sort_sources(legal_sources + grey_sources)


# Publishers whose papers are open: no site visit from phase 1 is needed.
_OPEN_HOSTS = frozenset({"MDPI", "arXiv", "bioRxiv", "PLOS", "Frontiers", "BMC", "Beilstein", "EMBO",
                         "Research Square", "chemRxiv", "Preprints.org"})


def _webvpn_http(doi: str, output_path: Path, config: dict[str, Any]) -> dict[str, Any] | None:
    """WebVPN without a browser: the saved session over HTTP (and a campus
    connector's SOCKS5 when one is configured). Asks the user nothing."""
    from . import instsci
    if instsci._is_campus_connector_mode(config):
        result = instsci._try_instsci_socks5(doi, output_path, config)
        if result:
            return result
    if not instsci.instsci_cookie_path(config).exists():
        return None
    return instsci._try_instsci_http(doi, output_path, config)


def _webvpn_browser(doi: str, output_path: Path, config: dict[str, Any]) -> dict[str, Any] | None:
    from . import instsci
    return instsci._try_instsci_browser(doi, output_path, config)


# The school's routes, tried one after another (institutional_order): the ones
# that ask the user nothing first, then those that may need a login, so at most
# one window waits for the user at a time. Each step: (enabled?, function, label);
# the label decides whether it takes the browser slot (_BROWSER_SOURCE_LABELS).
_INSTITUTIONAL_STEPS: dict[str, tuple[Any, Any, str]] = {
    "webvpn_http": (lambda c: bool(c.get("vpnsci_enabled")), _webvpn_http, "WebVPN-HTTP"),
    "webvpn_browser": (lambda c: bool(c.get("vpnsci_enabled")), _webvpn_browser, "WebVPN"),
    "carsi": (lambda c: bool(c.get("carsi_enabled") and str(c.get("carsi_idp_name") or "").strip()),
              lambda d, o, c: _pkg().try_carsi(d, o, c), "CARSI"),
    "ezproxy": (lambda c: bool(c.get("ezproxy_enabled")), lambda d, o, c: _pkg().try_ezproxy(d, o, c), "EZProxy"),
}
DEFAULT_INSTITUTIONAL_ORDER = ["webvpn_http", "webvpn_browser", "carsi", "ezproxy"]


def _build_institutional_sources(doi: str, config: dict[str, Any], *, use_vpnsci: bool = False) -> list[tuple[Any, str]]:
    """Phase 2: the school's routes that are switched on, in institutional_order.

    WebVPN follows its setting (use_vpnsci is kept for callers; a single
    download used to leave WebVPN out unless asked). The publisher's own site
    is not here: phase 1 opened it from this same exit.
    """
    order = config.get("institutional_order") or _pkg().DEFAULT_INSTITUTIONAL_ORDER
    out = []
    for name in order:
        step = _pkg()._INSTITUTIONAL_STEPS.get(str(name))
        if step and step[0](config):
            out.append((step[1], step[2]))
    return out


def _run_institutional_sequence(steps: list[tuple[Any, str]], doi: str, target_dir: Path, output_path: Path,
                                config: dict[str, Any], failures: list[dict[str, str]] | None = None,
                                ) -> dict[str, Any] | None:
    """Try the school's routes one at a time; the first PDF wins."""
    from ..download import progress as progress_reporter
    log.info(f"   School routes in turn: {', '.join(label for _, label in steps)}")
    t_seq = time.time()
    for fn, label in steps:
        if _pkg()._cancelled():
            return None
        progress_reporter.item_stage(doi, f"正在通过学校认证下载：{_pkg()._STEP_TEXT.get(label, label)}")
        src_output = target_dir / f"{safe_filename(doi)}_{label}.pdf"
        done = threading.Event()
        threading.Thread(target=_pkg()._step_watchdog, args=(doi, label, done, config), daemon=True).start()
        try:
            result = _pkg()._try_source(fn, doi, src_output, config, label)
        finally:
            done.set()
        if result and result.get("success"):
            final_path = Path(result.get("file", ""))
            if final_path != output_path and final_path.exists():
                if output_path.exists():
                    output_path.unlink()
                final_path.rename(output_path)
                result["file"] = str(output_path)
            log.info(f"   OK {label}")
            log.info(f"   [timing] school routes: {time.time() - t_seq:.1f}s")
            return result
        if failures is not None:
            _pkg()._record_source_failure(failures, label, result or {"success": False, "error": "no PDF"})
        if src_output.exists():
            src_output.unlink(missing_ok=True)
    log.info(f"   [timing] school routes: {time.time() - t_seq:.1f}s, none had it")
    return None


def _step_watchdog(doi: str, label: str, done: threading.Event, config: dict[str, Any]) -> None:
    """End a school route that has gone quiet: after institutional_step_seconds
    without a result (a login the user is doing keeps it alive), kill the
    browsers it opened, so its blocked browser calls fail and the next route
    runs. 2026-10-17: a WebVPN browser step hung over 10 minutes on a page
    that never finished loading, with no timeout of its own firing."""
    from ..browser.backend import close_browsers_since
    limit = float(config.get("institutional_step_seconds", 240) or 240)
    since = time.perf_counter()  # close_browsers_since's clock
    deadline = time.time() + limit
    while not done.wait(5):
        if _pkg()._user_waiting_for(doi):
            deadline = max(deadline, time.time() + 60)  # the user is at it: give them time
            continue
        if time.time() >= deadline:
            n = close_browsers_since(since, owner=doi)
            log.info(f"   [timing] {label}: nothing after {limit:.0f}s; closed {n} browser(s), next route")
            return


_STEP_TEXT = {"WebVPN-HTTP": "WebVPN 直接下载", "WebVPN": "WebVPN 浏览器", "CARSI": "CARSI", "EZProxy": "EZProxy"}


def _build_failure_guidance(doi: str, config: dict[str, Any], error_type: str = "") -> list[str]:
    """Build actionable guidance when all download sources fail.

    error_type: what this paper's browser strategies reported (its DownloadAttempt).
    """
    import os
    tips = []

    if error_type == "paywall":
        tips.append("此论文需要机构订阅才能下载")
        tips.append(f"→ 运行 litloop_login(identifier=\"{doi}\") 打开浏览器登录")
        tips.append("→ 在浏览器中点击 'Access through your institution' 选择你的机构")
        tips.append("→ 登录后关闭浏览器，cookies 自动保存")
        tips.append("→ 重新运行下载命令即可")
    elif error_type == "cloudflare_blocked":
        tips.append("Cloudflare 防护阻止了访问")
        tips.append("→ 安装 CloakBrowser (pip install cloakbrowser) 绕过反爬")
        tips.append("→ 或配置代理: litloop config_set network_proxy \"socks5://127.0.0.1:1080\"")
    elif error_type == "browser_unavailable":
        tips.append("CloakBrowser 不可用，无法使用浏览器下载策略")
        tips.append("→ 运行: pip install cloakbrowser")

    # Check litloop proxy config
    cfg_proxy = config.get("network_proxy", "")
    env_proxy = os.environ.get("LITLOOP_PROXY", "")

    if cfg_proxy or env_proxy:
        active = env_proxy or cfg_proxy
        tips.append(f"当前代理: {active} — 如果 Sci-Hub 仍不通，尝试更换代理地址")
    else:
        # Check if system has proxy that litloop ignores
        sys_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
        if sys_proxy:
            tips.append(f"检测到系统代理 {sys_proxy}，但 litloop 未使用")
            tips.append(f"→ 运行: litloop config_set network_proxy \"{sys_proxy}\"")
        else:
            tips.append("未配置代理 — 如果网络受限，运行: litloop config_set network_proxy \"socks5://127.0.0.1:1080\"")

    # Check Tor
    try:
        from ..tor.proxy import check_tor_circuit
        if not check_tor_circuit(config):
            tips.append("Tor 未运行 → 运行: litloop tor_start（匿名访问 Sci-Hub/LibGen）")
    except Exception:
        pass

    # Check WebVPN
    if not config.get("vpnsci_enabled"):
        tips.append("有高校账号？运行: litloop config_set vpnsci_enabled true")

    # Manual fallback
    tips.append(f"手动下载: https://sci-hub.ru/{doi}")

    # Network diagnostic
    tips.append("运行网络诊断: litloop network_diagnose")

    return tips
