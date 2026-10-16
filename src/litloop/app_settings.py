"""What the settings page shows, and the "test" button behind each group.

One list of settings, in the groups of CHARTER's first-run setup, so the
desktop app's settings page, its first-run guide and (later) the built-in
agent all describe the same things the same way. Values are read with
``config.load_config`` and written with ``config.update_config``; secrets are
shown as their first and last characters only.

Each test makes the smallest real request it can. Those that spend a unit of
a provider's quota say so in ``cost`` (limits: docs/API_LIMITS.md).
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

from . import config as _config

# type: secret | text | email | path | bool | select
GROUPS: list[dict[str, Any]] = [
    {
        "id": "sources", "title": "数据源", "test": "sources",
        "fields": [
            {"key": "openalex_api_key", "label": "OpenAlex Key", "type": "secret",
             "link": "https://help.openalex.org/how-to-use-the-api/rate-limits-and-authentication"},
            {"key": "elsevier_api_key", "label": "Elsevier Key", "type": "secret", "link": "https://dev.elsevier.com"},
            {"key": "springer_meta_api_key", "label": "Springer Nature Key", "type": "secret",
             "help": "Meta API 的 Key", "link": "https://dev.springernature.com"},
            {"key": "email", "label": "联系邮箱", "type": "email"},
            {"key": "core_api_key", "label": "CORE Key", "type": "secret", "link": "https://core.ac.uk/services/api"},
        ],
    },
    {
        "id": "downloads", "title": "下载", "test": "downloads",
        "fields": [
            {"key": "output_dir", "label": "保存位置", "type": "path"},
            {"key": "auto_rename", "label": "按作者-年份-标题重命名", "type": "bool"},
            {"key": "download_strategy", "label": "来源顺序", "type": "select",
             "options": [["fastest", "谁快用谁"], ["oa_first", "开放获取优先"], ["legal_only", "只用合法来源"],
                         ["scihub_first", "Sci-Hub 优先"], ["scihub_only", "只用 Sci-Hub"], ["grey_only", "只用 Sci-Hub、LibGen 等"]]},
            {"key": "scihub_enabled", "label": "允许 Sci-Hub、LibGen 等来源", "type": "bool", "help": "可能不合规，自行决定", "warn": True},
        ],
    },
    {
        "id": "network", "title": "网络", "test": "network",
        "fields": [
            # network_proxy is the campus egress (auth.py skips the school login when it is set),
            # not a personal VPN: filling in Clash's port breaks institutional downloads.
            {"key": "network_proxy", "label": "校园出口代理", "type": "text",
             "help": "学校提供的校园隧道地址；个人翻墙代理不要填，否则机构下载会失败"},
            {"key": "is_campus_network", "label": "电脑连在校园网上", "type": "bool",
             "help": "打开后直接按学校 IP 下载，不再登录 WebVPN / CARSI"},
        ],
    },
    {
        "id": "school", "title": "学校认证",
        "fields": [
            {"key": "vpnsci_school", "label": "学校代号", "type": "text", "placeholder": "buaa"},
            {"key": "vpnsci_enabled", "label": "WebVPN", "type": "bool"},
            {"key": "carsi_enabled", "label": "CARSI", "type": "bool"},
            {"key": "ezproxy_enabled", "label": "EZProxy", "type": "bool"},
        ],
    },
    {
        "id": "zotero", "title": "Zotero", "test": "zotero",
        "fields": [
            {"key": "zotero_mcp_python", "label": "zotero-mcp 的 Python", "type": "path", "placeholder": "留空自动查找"},
        ],
    },
]

_FIELDS = {f["key"]: f for g in GROUPS for f in g["fields"]}


def mask(value: str) -> str:
    """First and last four characters of a secret, the rest hidden."""
    value = str(value or "")
    if not value:
        return ""
    return value[:4] + "…" + value[-4:] if len(value) > 12 else "•" * 8


def _source(key: str, cfg: dict[str, Any]) -> str:
    """Where the value comes from: the config file, an environment variable, or the default."""
    env = _config.ENV_KEYS.get(key)
    on_file = {}
    try:
        import json
        on_file = json.loads(_config.CONFIG_FILE.read_text(encoding="utf-8")) if _config.CONFIG_FILE.exists() else {}
    except (OSError, ValueError):
        pass
    if key in on_file and on_file[key] not in ("", None):
        return "file"
    if env and os.environ.get(env) and cfg.get(key) == os.environ.get(env):
        return "env"
    return "default"


def settings() -> list[dict[str, Any]]:
    cfg = _config.load_config()
    out = []
    for g in GROUPS:
        fields = []
        for f in g["fields"]:
            value = cfg.get(f["key"])
            if f["key"] == "email" and str(value).endswith(".invalid"):
                value = ""  # the shipped placeholder is not an address
            shown = {**f, "set": bool(value), "source": _source(f["key"], cfg)}
            shown["value"] = mask(value) if f["type"] == "secret" else value
            if f["key"] in _config.ENV_KEYS:
                shown["env"] = _config.ENV_KEYS[f["key"]]
            fields.append(shown)
        out.append({**g, "fields": fields})
    return out


def update(key: str, value: Any) -> dict[str, Any]:
    """Save one setting from the app. Only keys on the settings page can be changed."""
    field = _FIELDS.get(key)
    if field is None:
        raise KeyError(f"{key} is not a setting the app may change")
    if field["type"] == "bool":
        value = "true" if value in (True, "true", "1", 1) else "false"
    elif field["type"] == "select" and value not in [o[0] for o in field["options"]]:
        raise ValueError(f"{key} must be one of {[o[0] for o in field['options']]}")
    elif field["type"] == "email" and value and "@" not in str(value):
        raise ValueError("not an email address")
    _config.update_config(key, str(value if value is not None else "").strip())
    return next(f for g in settings() for f in g["fields"] if f["key"] == key)


# --------------------------------------------------------------------------- tests

def _result(name: str, ok: bool | None, detail: str, cost: str = "", **extra: Any) -> dict[str, Any]:
    """ok: True passed, False failed, None not configured (nothing to test).

    extra: ``guides``, each with a ``title``, numbered ``steps``, ``copy`` blocks
    ({label, lines}) to paste, and an optional ``note``.
    """
    return {"name": name, "ok": ok, "detail": detail, **({"cost": cost} if cost else {}), **extra}


def _get(url: str, **kw: Any):
    import requests
    return requests.get(url, timeout=15, **kw)


def test_sources() -> list[dict[str, Any]]:
    from . import quota
    cfg = _config.load_config()
    out = []

    live = quota.openalex_live()
    if not live or live.get("error"):
        out.append(_result("OpenAlex", False, f"连不上 OpenAlex：{(live or {}).get('error', '无响应')}"))
    else:
        left, limit = live.get("credits_remaining"), live.get("credits_limit")
        if live.get("has_key"):
            out.append(_result("OpenAlex", True, f"可用，今日剩余 {left} / {limit} 积分"))
        else:
            out.append(_result("OpenAlex", None, f"未填 Key，今日剩余 {left} / {limit} 积分"))

    key = str(cfg.get("elsevier_api_key") or "").strip()
    if not key:
        out.append(_result("Elsevier", None, "未填"))
    else:
        try:
            r = _get("https://api.elsevier.com/content/abstract/doi/10.1016/j.jeurceramsoc.2008.06.007",
                     headers={"X-ELS-APIKey": key, "Accept": "application/json"})
            quota.record("scopus", r)
            left = r.headers.get("X-RateLimit-Remaining")
            if r.status_code == 200:
                out.append(_result("Elsevier", True, "可用" + (f"，本周剩余 {left} 次" if left else ""),
                                   cost="Scopus 1 次"))
            elif r.status_code in (401, 403):
                out.append(_result("Elsevier", False, f"Key 无效（HTTP {r.status_code}）"))
            else:
                out.append(_result("Elsevier", False, f"HTTP {r.status_code}"))
        except Exception as exc:
            out.append(_result("Elsevier", False, f"连不上 Elsevier：{exc}"))

    key = str(cfg.get("springer_meta_api_key") or "").strip()
    if not key:
        out.append(_result("Springer Nature", None, "未填"))
    else:
        try:
            r = _get("https://api.springernature.com/meta/v2/json",
                     params={"q": "doi:10.1007/bf00723813", "api_key": key, "p": 1})
            quota.record("springer", r)
            if r.status_code == 200:
                out.append(_result("Springer Nature", True, "可用", cost="Meta API 1 次"))
            elif r.status_code in (401, 403):
                out.append(_result("Springer Nature", False, f"Key 无效或不是 Meta API 的 Key（HTTP {r.status_code}）"))
            else:
                out.append(_result("Springer Nature", False, f"HTTP {r.status_code}"))
        except Exception as exc:
            out.append(_result("Springer Nature", False, f"连不上 Springer Nature：{exc}"))

    email = str(cfg.get("email") or "").strip()
    if not email or email.endswith(".invalid"):
        out.append(_result("Crossref", None, "未填联系邮箱"))
    else:
        try:
            r = _get("https://api.crossref.org/works", params={"rows": 0, "mailto": email})
            out.append(_result("Crossref", r.status_code == 200,
                               "可用" if r.status_code == 200 else f"HTTP {r.status_code}"))
        except Exception as exc:
            out.append(_result("Crossref", False, f"连不上 Crossref：{exc}"))
    return out


def test_downloads() -> list[dict[str, Any]]:
    folder = Path(str(_config.load_config().get("output_dir") or "")).expanduser()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".litloop-test-", delete=True):
            pass
        free = None
        try:
            import shutil
            free = shutil.disk_usage(folder).free / 1024 ** 3
        except OSError:
            pass
        return [_result("保存位置", True, f"可写入" + (f"，剩余 {free:.0f} GB" if free is not None else ""))]
    except OSError as exc:
        return [_result("保存位置", False, f"无法写入：{exc}")]


def test_zotero() -> list[dict[str, Any]]:
    try:
        r = _get("http://127.0.0.1:23119/connector/ping", proxies={"http": None, "https": None})
        if r.status_code == 200:
            return [_result("Zotero", True, "正在运行")]
        return [_result("Zotero", False, f"HTTP {r.status_code}")]
    except Exception:
        return [_result("Zotero", False, "未运行")]


def test_network() -> list[dict[str, Any]]:
    """Proxy programs: system-proxy mode is bypassed by itself; TUN mode needs direct rules.

    The steps follow the programs' own docs (checked 2026-10-16):
    v2rayN https://github.com/2dust/v2rayN/wiki/Description-of-some-ui ,
    Clash Verge Rev https://www.clashverge.dev/guide/rules.html ,
    mihomo find-process-mode https://wiki.metacubex.one/config/general/ .
    """
    from . import proxy_check
    out = []
    cfg = _config.load_config()
    if str(cfg.get("network_proxy") or "").strip():
        out.append(_result("校园出口代理", None, "已填写：LitLoop 会跳过学校登录。只有学校提供的校园隧道才该填在这里。"))
    found = proxy_check.detect()
    names, paths = found["direct_processes"], proxy_check.litloop_paths()
    programs = "、".join(found["programs"]) or "代理软件"
    if found["mode"] == "unknown":
        out.append(_result("代理软件", None, "暂不支持在此系统上检测。"))
    elif found["mode"] == "none":
        out.append(_result("代理软件", True, "未检测到代理软件。"))
    elif found["mode"] == "system":
        out.append(_result("代理软件", True, f"{programs} 以系统代理模式运行，LitLoop 会自动直连，无需设置。"))
    else:
        guides = []
        if any(p in ("v2rayN", "sing-box", "Xray") for p in found["programs"]) or not found["programs"]:
            # v2rayN's wiki names the 进程名 field and the direct outbound but not the
            # value's format; full paths are offered for when the name does not match.
            guides.append({
                "title": "在 v2rayN 中设置直连",
                "steps": ["打开「路由设置」，新建一条规则。",
                          "在「进程名」中填写下方的一个程序名，出站选择「direct」。每个程序单独建一条规则。",
                          "将这些规则移到列表最上方并保存。规则按顺序生效。"],
                "copy": [{"label": "程序名", "lines": names},
                         {"label": "完整路径（填程序名不生效时使用）", "lines": paths}],
            })
        if any("Clash" in p or p == "mihomo" for p in found["programs"]):
            guides.append({
                "title": "在 Clash Verge 中设置直连",
                "steps": ["右键当前订阅，选择「编辑规则」。",
                          "在「前置规则」中添加下方的规则并保存。"],
                "copy": [{"label": "规则", "lines": [f"PROCESS-NAME,{n},DIRECT" for n in names]}],
                "note": "若规则未生效，在「扩展配置」中加入 find-process-mode: always。",
            })
        out.append(_result("代理软件", False,
                           f"{programs} 正以 TUN 模式运行。学校通道的流量会经由代理出口，无法通过学校的身份认证。",
                           guides=guides))
    return out


TESTS = {"sources": test_sources, "downloads": test_downloads, "network": test_network, "zotero": test_zotero}


def run_test(group: str) -> dict[str, Any]:
    if group not in TESTS:
        raise KeyError(f"no test for {group}")
    t0 = time.time()
    return {"group": group, "results": TESTS[group](), "seconds": round(time.time() - t0, 1)}
