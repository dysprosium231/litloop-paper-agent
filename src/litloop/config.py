"""Configuration management for LitLoop."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.environ.get("LITLOOP_DATA_DIR", str(Path.home() / ".litloop")))
CONFIG_FILE = DATA_DIR / "config.json"

DEFAULT_SCIHUB_DOMAINS = [
    # Direct PDF mirrors (no CAPTCHA, PDF via sci.bban.top iframe) — verified 2026-07
    # vg/al 现为 Turnstile 门/首页壳（2026-08-31 实测），保留用于人工点一次模式
    "https://sci-hub.vg",
    "https://sci-hub.al",
    "https://sci-hub.mk",
    # 2026-08-31 深夜直连实测新增：bz/mksa 稳定出文章页（iframe PDF，无验证墙）
    "https://sci-hub.bz",
    "https://sci-hub.mksa.top",
    # ALTCHA-protected (stable; used as manual-download hint)
    "https://sci-hub.ru",
    "https://sci-hub.ee",
    # Cloudflare-protected (requires CloakBrowser JS challenge bypass)
    "https://sci-hub.st",
    # Reported intermittent (2026-06); kept but deprioritized
    "https://sci-hub.mksa.top",
    # Legacy (currently down, kept for future recovery)
    "https://sci-hub.se",
    "https://sci-hub.is",
    "https://sci-hub.41610.org",
]

DEFAULT_CONFIG: dict[str, Any] = {
    "email": "litloop@example.invalid",
    "output_dir": str(DATA_DIR / "papers"),
    "cache_dir": str(DATA_DIR / "cache"),
    "network_proxy": "",
    "proxy_pool": "",  # 逗号分隔的代理列表；非空时批量下载按代理轮换出口 IP
    "download_strategy": "fastest",  # fastest / grey_only(all 3 grey) / scihub_only(Sci-Hub only) / scihub_first / oa_first / legal_only
    "race_mode": "hedge",  # hedge: score-ordered staggered cascade (fewer requests, less anti-bot heat) / full: flat parallel race
    "hedge_delay_seconds": 1.5,  # hedged cascade: wait this long before widening to the next lane
    "batch_default_lanes": True,  # batch downloads default to pretriage + channel-lane scheduling (fast HTTP -> grey -> institutional)
    "lane_s2_batch_min": 10,  # minimum batch size to enrich OA URLs via the S2 batch endpoint (500 ids/request)
    "lane_mdpi_cdn": True,  # fast lane: construct mdpi-res.com CDN URLs for 10.3390 DOIs (bypasses the bot-walled main site)
    # Grey sources (Sci-Hub, LibGen, SciBban) only when the user turns them on (2026-10-16).
    "scihub_enabled": False,
    "scihub_domains": DEFAULT_SCIHUB_DOMAINS,
    "vpnsci_enabled": False,
    "vpnsci_school": "",
    "vpnsci_base_url": "",
    "vpnsci_cookie_file": "",
    "carsi_enabled": False,
    "carsi_idp_name": "",
    "ezproxy_enabled": False,
    "ezproxy_login_url": "",
    # 机构会话自愈：下载走机构渠道前自动校验 WebVPN/CARSI 会话，
    # 已过期时打开浏览器重新登录。仅当存在历史 cookie 且校验明确判定
    # 过期才触发；新用户或网络不可达绝不弹浏览器。
    "auto_relogin": True,
    "core_api_key": "",
    "openalex_api_key": "",
    "elsevier_api_key": "",
    "springer_api_key": "",  # Springer Nature Full Text API (TDM) — entitlement follows the institution (ORCID-affiliated key, needs a Springer subscription with TDM rights)
    "elsevier_insttoken": "",
    "connect_timeout": 15,
    "read_timeout": 30,
    "request_delay_min": 2.0,
    "request_delay_max": 5.0,
    "fixed_request_delay_enabled": False,
    "json_probe_cache_seconds": 3600,
    "cache_ttl_hours": 168,
    "parallel_sources": True,
    "parallel_probes": True,
    "batch_workers": 10,
    "batch_stagger_seconds": 0.3,
    "min_pdf_size_bytes": 10000,
    "browser_headless": False,
    # 出版商官网的专用窗口先最小化，需要登录或人机验证时再弹到前台
    "publisher_window_minimized": True,
    "browser_humanize": True,
    # 浏览器后端：patchright（默认，Apache-2.0 开源 playwright fork，内核随本机 Chrome 自动更新）
    # 或 cloakbrowser（免费版内核卡 Chromium 146，作为可选回退）
    "browser_backend": "patchright",
    # 批量下载时每 N 篇回收一次浏览器上下文（cookie 内存交接，登录态不丢）。
    # 0 = 关闭。长批次（上千篇）建议 100-200，避免 Chrome 长会话内存漂移。
    "browser_restart_every": 0,
    # 批量下载成功后自动抓取附件/补充材料（SI）。默认关——大多数任务只要主 PDF。
    # 开启后成功论文会尝试抓取出版商补充材料，存为 {DOI}_SI{n}.{ext}，并写 si_manifest.json。
    "fast_retry_wait_sec": 60,
    # 冷却重试期间的逐篇间隔（秒）。实测 15s/篇可 100% 绕开 Elsevier API 限流。
    "fast_retry_delay_sec": 15,
    # Turnstile 交互门（如 sci-hub.vg）：无头会话下无法点击，默认跳过并冷却。
    # scihub_browser_headless=false 时可开启人工点一次模式（点一次整批复用）。
    "scihub_turnstile_click": True,
    "turnstile_wait_sec": 180,
    # 机构级联并行 fetcher 数（各持独立登录会话）。默认 1 = 串行；2 可省约一半
    # Phase 2 时间，代价是同 IP 双浏览器会话。
    "institutional_workers": 1,
    # 灰色源竞速浏览器的独立无头开关（只影响 sci-hub 竞速，机构登录仍有可见窗口）。
    # true = 竞速全程零窗口、零任务栏闪烁；指纹安全性由 UA 清洗保证。
    "scihub_browser_headless": False,
    "scihub_browser_first": True,  # false = pure-HTTP Sci-Hub lane (use when the PDF CDN challenges headless browsers but serves plain requests)
    # 浏览器内核选择（CloakBrowser 免费版内置 Chromium 146 已过老，遇 Cloudflare Turnstile 会反复验证）：
    #   browser_executable: 显式指定浏览器二进制路径（本机 Chrome/Edge）；留空=自动探测
    #   browser_auto_upgrade: True 时自动探测本机 Chrome/Edge（版本 > 146 优先于内置 stealth Chromium）
    "browser_executable": "",
    "browser_auto_upgrade": True,
    # browser_executable 指向本机 Chrome 的副本时，启动前若系统 Chrome 已升级就同步更新副本
    "browser_copy_follow_system": True,
    "is_campus_network": False,
    "tor_proxy": os.environ.get("TOR_PROXY", ""),
    # Optional Springer Nature Meta API key (not the TDM key above): abstracts of Springer Nature papers,
    # free plan 500 requests/day. See docs/API_LIMITS.md.
    "springer_meta_api_key": "",
    "springer_meta_daily_limit": 500,
    "tor_use_bridges": False,
    "use_tor_for_scihub": True,
    "google_scholar_limit": 5,
    "max_browser_workers": 1,
    "scihub_browser_workers": 3,  # Number of Sci-Hub domains to race in parallel via browser
    "host_concurrency": {},
    "auto_rename": True,  # older switch; false means rename_format "off"
    "rename_format": "author_year_title_doi",  # see rename.FORMATS
    # Fetch abstracts missing from search results (Semantic Scholar, then
    # Scopus with elsevier_api_key, then Crossref) so screening reads them.
    "search_fill_abstracts": True,
    # Chinese abstracts in the app (translate.py): which ratings are translated
    # without asking ("高", "高中", "all", "off"), and by whom: "deepseek" /
    # "deepl" (the app, when the paper is opened; needs the key) or "agent"
    # (the agent, when it rates: costs conversation tokens).
    "abstract_translation": "高",
    # Look for new versions of LitLoop, easyseek and zotero-mcp every N days
    # (updates.py; 0 = never). Only a notice in results; nothing is installed.
    "update_check_days": 7,
    # Batches up to this many papers: Sci-Hub for all first (in parallel: plain
    # HTTP), then a full single download (school routes included) for each miss,
    # batch_small_workers at a time (pipeline.run_scihub_then_single). Those are
    # the papers that need the browser, of which there is one
    # (max_browser_workers): one at a time, so no paper's race clock runs out
    # while it queues for the browser and each login window is for one paper.
    # Longer lists use the lanes.
    # The school's routes after phase 1, one after another (sources
    # _INSTITUTIONAL_STEPS): no-login ones first, so one window at a time asks
    # the user. Steps switched off in settings are skipped.
    "institutional_order": ["webvpn_http", "webvpn_browser", "carsi", "ezproxy"],
    # Late results after the 20 s race: a browser lane still at work gets up to
    # race_grace_browser_seconds, otherwise race_grace_seconds; a lane waiting
    # for the user is waited for as long as it says so (up to 10 min).
    # Download full text through the Elsevier API (abstracts and Scopus always
    # use the key). Off when the key has no full-text entitlement; LitLoop also
    # learns that from the API's 403 and stops asking for 30 days.
    "elsevier_api_fulltext": True,
    # Sci-Hub and LibGen through host_proxy_url (the user's proxy) instead of
    # the school's exit; direct when that proxy's port is closed.
    "scihub_via_proxy": False,
    # Papers published in or after this year skip Sci-Hub (and SciBban): it
    # has almost none of them. The year comes from the app's list or the DOI
    # (j.ceramint.2026.06.274); a paper with no known year is still asked. 0: off.
    "scihub_skip_from_year": 2024,
    # Route for Sci-Hub and LibGen: "direct", "proxy" (host_proxy_url only) or
    # "auto" (a batch first measures mirrors on both routes, the fastest wins;
    # sources/scihub_speed.py). Empty: "proxy" if scihub_via_proxy else "direct".
    "scihub_route": "direct",
    # The speed test: a paper Sci-Hub has (about 1 MB), how many mirrors, how long.
    "scihub_speedtest_doi": "10.1016/j.colsurfb.2017.09.051",
    "scihub_speedtest_mirrors": 5,
    "scihub_speedtest_seconds": 40,
    # A short list's first round: papers asked of Sci-Hub at once (HTTP only),
    # and the workers their mirror requests share. More papers at once risks
    # Sci-Hub's per-IP rate limit.
    "scihub_round_workers": 10,
    "scihub_http_workers": 12,
    "race_grace_seconds": 15,
    "race_grace_browser_seconds": 180,
    # A school route (institutional_order) with no result after this long has its
    # browsers closed and the next route runs; a login the user is doing extends it.
    "institutional_step_seconds": 240,
    # After a login to the publisher site with the school's identity still gives
    # no full text, the school routes (WebVPN, CARSI, EZProxy) are skipped: they
    # reach the same publisher as the same school. True tries them anyway.
    "school_routes_after_publisher_login": False,
    # DOI prefixes whose publisher site phase 1 does not open (2026-10-18 user
    # decision): Taylor & Francis books (CRC 10.1201, Routledge 10.4324). The
    # T&F site answers in about 12 s per request from here and has no chapter
    # PDFs; WebVPN downloads the whole book directly.
    "publisher_site_skip_prefixes": ["10.1201", "10.4324"],
    "batch_small_max": 30,
    "batch_small_workers": 1,
    # Desktop app: the first-run guide (app_settings.STEPS) was finished or skipped.
    "onboarding_done": False,
    # Desktop app: searches started from the app are sent to this agent as a
    # message (agent_runner.py); the prompt goes on stdin. Claude Code by default:
    # stream-json lets the app show which tool it is using; only the litloop
    # tools and skills are allowed, since nobody is there to approve anything.
    "agent_command": "claude",
    # An agent run that prints nothing for this long is stopped (it hung or
    # waits on a prompt nobody sees). A deep-search batch takes ~1-3 minutes.
    "agent_idle_minutes": 15,
    "agent_args": "-p --output-format stream-json --verbose --allowedTools mcp__litloop Skill Read",
    "translation_provider": "deepseek",
    "deepseek_api_key": "",
    "deepl_api_key": "",
    # zotero-mcp (github.com/54yyyu/zotero-mcp), the only route to Zotero: the
    # Python that has it installed ("" = find it automatically), and an optional
    # script that sets its ZOTERO_* env vars (its serve call must be under __main__).
    "zotero_mcp_python": "",
    "zotero_mcp_env_script": "",
    "zotero_push_timeout": 900,
    # Desktop app: push a download job to Zotero when it finishes (off: the
    # user pushes from the job page), into <root>/<the job's title>.
    "zotero_auto_push": False,
    "zotero_collection_root": "LitLoop",
    # Per-host proxy exception: these domains (and subdomains) go through
    # host_proxy_url, everything else stays direct. For publishers that grant
    # access by login cookie rather than campus IP, where the direct route is
    # slow (Springer Nature's CDN). Skipped while the proxy port is closed.
    # false: Sci-Hub/LibGen never open a browser (ALTCHA is solved over HTTP;
    # mirrors behind Cloudflare Turnstile are skipped).
    "scihub_use_browser": True,
    "host_proxy_url": "",
    "host_proxy_hosts": [],
    "cookie_path": "",
    "chrome_profile_dir": "",
    "carsi_cookie_dir": "",
}


ENV_KEYS = {"openalex_api_key": "OPENALEX_API_KEY", "elsevier_api_key": "ELSEVIER_API_KEY",
            "springer_meta_api_key": "SPRINGER_META_API_KEY"}


def load_config() -> dict[str, Any]:
    config = DEFAULT_CONFIG.copy()
    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as fh:
                existing = json.load(fh)
            if isinstance(existing, dict):
                config.update(existing)
        except Exception:
            pass
    for key, value in DEFAULT_CONFIG.items():
        config.setdefault(key, value)
    for key, env in ENV_KEYS.items():  # the config file wins; the environment fills the gap
        if not config.get(key) and os.environ.get(env):
            config[key] = os.environ[env]
    return config


def save_config(config: dict[str, Any]) -> None:
    """Write only what differs from DEFAULT_CONFIG, so a later change of a default
    reaches everyone who never set that key (load_config fills defaults back in)."""
    # Keys that came from the environment stay there: never write them to disk.
    config = {k: ("" if k in ENV_KEYS and v and v == os.environ.get(ENV_KEYS[k]) else v) for k, v in config.items()}
    config = {k: v for k, v in config.items() if k not in DEFAULT_CONFIG or v != DEFAULT_CONFIG[k]}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_FILE.open("w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2, ensure_ascii=False)


_VALIDATION_RULES: dict[str, tuple[type, Any, Any]] = {
    # key: (type, min_value, max_value)
    "connect_timeout": (int, 1, 60),
    "read_timeout": (int, 1, 120),
    "request_delay_min": (float, 0.0, 30.0),
    "request_delay_max": (float, 0.0, 60.0),
    "json_probe_cache_seconds": (int, 0, 86400),
    "cache_ttl_hours": (int, 1, 8760),
    "batch_workers": (int, 1, 50),
    "batch_stagger_seconds": (float, 0.0, 10.0),
    "min_pdf_size_bytes": (int, 100, 1000000),
    "google_scholar_limit": (int, 1, 50),
}

_VALID_STRATEGIES = frozenset({"fastest", "scihub_first", "scihub_only", "grey_only", "oa_first", "legal_only"})


def update_config(key: str, value: str) -> dict[str, Any]:
    import warnings as _warnings

    config = load_config()

    # Validate: warn on unknown keys
    if key not in DEFAULT_CONFIG:
        _warnings.warn(
            f"Unknown config key '{key}' — it will be stored but may have no effect. "
            f"Valid keys include: {', '.join(sorted(DEFAULT_CONFIG.keys()))}",
            stacklevel=2,
        )

    # Special handling for download_strategy
    if key == "download_strategy":
        value_lower = value.lower().strip()
        if value_lower not in _VALID_STRATEGIES:
            raise ValueError(
                f"Invalid download_strategy '{value}'. Valid options: {', '.join(sorted(_VALID_STRATEGIES))}"
            )
        config[key] = value_lower
        save_config(config)
        return config

    if key in config:
        old_type = type(config[key])
        if old_type == bool:
            config[key] = value.lower() in ("true", "1", "yes")
        elif old_type == int:
            try:
                config[key] = int(value)
            except ValueError:
                raise ValueError(f"Invalid integer value for '{key}': '{value}'")
        elif old_type == float:
            try:
                config[key] = float(value)
            except ValueError:
                raise ValueError(f"Invalid float value for '{key}': '{value}'")
        elif old_type == list:
            # Try JSON parse for list values (e.g. scihub_domains)
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    config[key] = parsed
                else:
                    raise ValueError(f"Expected a JSON list for '{key}', got {type(parsed).__name__}")
            except json.JSONDecodeError:
                raise ValueError(
                    f"Invalid list value for '{key}'. Use JSON format, e.g.: '[\"https://sci-hub.vg\",\"https://sci-hub.al\"]'"
                )
        else:
            config[key] = value
    else:
        config[key] = value

    if key in _VALIDATION_RULES:
        _, min_val, max_val = _VALIDATION_RULES[key]
        if config[key] < min_val or config[key] > max_val:
            config[key] = DEFAULT_CONFIG[key]

    save_config(config)
    return config


SENSITIVE_KEYS = [
    "core_api_key",
    "vpnsci_cookie_file",
    "zotero_api_key",  # no longer used (Zotero goes through zotero-mcp); old configs still masked
    "zotero_library_id",
    "elsevier_api_key",
    "elsevier_insttoken",
]

_PROXY_URL_CREDS_RE = re.compile(r"(//[^/@\s]+:)[^@\s]+@")


def mask_config_value(key: str, value: Any) -> Any:
    """Return a display-safe copy of a config value.

    Secrets (API keys, tokens, cookie files) are fully masked; proxy URLs keep
    host/port but hide the password (``http://user:***@host:port``).
    """
    if value is None:
        return value
    if key in SENSITIVE_KEYS or key.endswith(("_api_key", "_token", "_insttoken")):
        return "***" if value else value
    if "proxy" in key.lower() and isinstance(value, str) and "@" in value:
        return _PROXY_URL_CREDS_RE.sub(r"\1***@", value)
    return value


def get_config_safe() -> dict[str, Any]:
    config = load_config()
    return {key: mask_config_value(key, value) for key, value in config.items()}


def parse_proxy_pool(value: str | None) -> list[str]:
    """Parse a comma-separated proxy list into a deduplicated list.

    Accepts forms like ``"socks5://1.1.1.1:1080, http://2.2.2.2:8080"`` and
    returns ``["socks5://1.1.1.1:1080", "http://2.2.2.2:8080"]``. Empty/blank
    entries are dropped. Order is preserved; duplicates removed.
    """
    if not value:
        return []
    seen: set[str] = set()
    proxies: list[str] = []
    for token in str(value).split(","):
        proxy = token.strip()
        if proxy and proxy not in seen:
            seen.add(proxy)
            proxies.append(proxy)
    return proxies
