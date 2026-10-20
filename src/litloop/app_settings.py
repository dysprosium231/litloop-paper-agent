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
from .rename import FORMATS as _RENAME_FORMATS
from .rename import rename_format as _rename_format

# type: secret | text | email | path | bool | select; a path may say pick: dir | file (the app offers a chooser)
GROUPS: list[dict[str, Any]] = [
    {
        "id": "sources", "title": "检索接口", "test": "sources",
        "fields": [
            {"key": "openalex_api_key", "label": "OpenAlex Key", "type": "secret",
             "link": "https://help.openalex.org/how-to-use-the-api/rate-limits-and-authentication"},
            {"key": "elsevier_api_key", "label": "Elsevier Key", "type": "secret", "link": "https://dev.elsevier.com"},
            {"key": "elsevier_api_fulltext", "label": "用 Elsevier Key 下载全文", "type": "bool",
             "help": "Key 没有全文权限时关闭；补摘要和 Scopus 检索不受影响"},
            {"key": "springer_meta_api_key", "label": "Springer Nature Key", "type": "secret",
             "help": "Meta API 的 Key", "link": "https://dev.springernature.com"},
            {"key": "email", "label": "联系邮箱", "type": "email"},
            {"key": "core_api_key", "label": "CORE Key", "type": "secret", "link": "https://core.ac.uk/services/api"},
        ],
    },
    {
        "id": "downloads", "title": "下载", "test": "downloads",
        "fields": [
            {"key": "output_dir", "label": "保存位置", "type": "path", "pick": "dir"},
            {"key": "rename_format", "label": "文件名", "type": "select",
             "options": [[k, v] for k, v in _RENAME_FORMATS.items()]},
            {"key": "download_strategy", "label": "来源顺序", "type": "select",
             "options": [["fastest", "速度优先"], ["oa_first", "开放获取优先"], ["legal_only", "只用合法来源"],
                         ["scihub_first", "Sci-Hub 优先"], ["scihub_only", "只用 Sci-Hub"], ["grey_only", "只用 Sci-Hub、LibGen 等"]]},
            {"key": "scihub_enabled", "label": "允许 Sci-Hub、LibGen 等来源", "type": "bool",
             "help": "可能违反出版商条款或学校规定，自行判断是否开启", "warn": True},
            {"key": "scihub_skip_from_year", "label": "较新的文献不查 Sci-Hub", "type": "select",
             "options": [["0", "不限"], ["2022", "2022 年起"], ["2023", "2023 年起"], ["2024", "2024 年起"], ["2025", "2025 年起"]],
             "help": "Sci-Hub 很少收录新文献；不知道年份的文献仍会查"},
            {"key": "scihub_route", "label": "Sci-Hub、LibGen 线路", "type": "select",
             "options": [["direct", "直连"], ["proxy", "经代理"], ["auto", "下载前测速选择"]],
             "help": "代理是「网络」中的代理软件地址；选「经代理」时不从校园网直连"},
        ],
    },
    {
        "id": "network", "title": "网络", "test": "network",
        "fields": [
            # network_proxy is the campus egress (auth.py skips the school login when it is set),
            # not a personal VPN: filling in Clash's port breaks institutional downloads.
            {"key": "network_proxy", "label": "校园出口代理", "type": "text",
             "help": "只填学校提供的代理地址，个人代理软件的端口会导致学校认证失败"},
            {"key": "host_proxy_url", "label": "代理软件地址", "type": "text", "placeholder": "http://127.0.0.1:10808",
             "help": "只有指定的网站（如 Sci-Hub）经它访问，其余直连"},
            {"key": "is_campus_network", "label": "已连接校园网", "type": "bool",
             "help": "打开后直接按学校 IP 下载，不再登录 WebVPN 或 CARSI"},
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
        "id": "screening", "title": "筛选", "test": "screening",
        "fields": [
            {"key": "abstract_translation", "label": "自动翻译摘要", "type": "select",
             "options": [["高", "评为高的文献"], ["高中", "评为高、中的文献"], ["all", "全部文献"], ["off", "不自动翻译"]],
             "help": "打开范围内的文献时自动翻译并保存，其他文献可在详情页翻译"},
            {"key": "translation_provider", "label": "翻译服务", "type": "select",
             "options": [["deepseek", "DeepSeek"], ["deepl", "DeepL"], ["agent", "Agent（评分时翻译）"]],
             "help": "DeepSeek 翻译专业术语较好；Agent 不需要 Key，但消耗对话额度"},
            {"key": "deepseek_api_key", "label": "DeepSeek Key", "type": "secret",
             "link": "https://platform.deepseek.com/api_keys"},
            {"key": "deepl_api_key", "label": "DeepL Key", "type": "secret",
             "link": "https://www.deepl.com/your-account/keys", "help": "免费版 Key 以 :fx 结尾"},
        ],
    },
    {
        "id": "agent", "title": "Agent",
        "fields": [
            {"key": "agent_command", "label": "Agent 命令", "type": "text", "placeholder": "claude",
             "help": "在 app 里发起检索时，把检索要求发给这个 Agent"},
            {"key": "agent_args", "label": "命令参数", "type": "text",
             "help": "检索要求从标准输入传入；默认只允许 Agent 调用 LitLoop 的工具"},
        ],
    },
    {
        "id": "zotero", "title": "Zotero", "test": "zotero",
        "fields": [
            {"key": "zotero_mcp_python", "label": "zotero-mcp 的 Python", "type": "path", "pick": "file", "placeholder": "留空自动查找"},
            {"key": "zotero_auto_push", "label": "下载完自动推送", "type": "bool"},
            {"key": "zotero_collection_root", "label": "默认分类", "type": "text", "placeholder": "LitLoop",
             "help": "推送到「默认分类/检索问题」"},
        ],
    },
]

_FIELDS = {f["key"]: f for g in GROUPS for f in g["fields"]}

# The first-run guide: settings groups in the order a new user needs them, each
# with why it matters. Every step can be checked on the spot or skipped.
STEPS = [
    {"group": "sources", "intro": "检索和补摘要靠这些接口。OpenAlex Key 免费，没有时每天的检索额度很少；"
                                  "Elsevier Key 用于 ScienceDirect 全文和 Scopus 摘要，对深度检索影响很大。"
                                  "联系邮箱只发给 Crossref、OpenAlex、Unpaywall，它们要求填写。"},
    {"group": "screening", "intro": "筛选时可把摘要翻译成中文。填 DeepSeek 或 DeepL 的 Key 后由 app 翻译，不占用对话额度；也可以以后再设置。"},
    {"group": "downloads", "intro": "设置 PDF 的保存位置和文件名。Sci-Hub、LibGen 等来源可能违反出版商条款或学校规定，默认不使用，是否开启由你决定。"},
    {"group": "network", "intro": "开启代理软件时，学校认证的流量需要直连，否则学校无法识别你的身份。选择「检测」查看当前网络和需要的设置。"},
    {"group": "school", "intro": "下载学校订阅的期刊需要学校认证。填写学校代号并选择认证方式；不在校园网时会弹出浏览器让你登录一次。"},
    {"group": "zotero", "intro": "下载的文献可推送到 Zotero。需要 Zotero 7 以上和 zotero-mcp；推荐授权本地写入，速度最快。"},
]
_CONFIGURED = ("openalex_api_key", "elsevier_api_key", "springer_meta_api_key")


def onboarding() -> dict[str, Any]:
    """Whether the first-run guide still has to show, and its steps.

    Someone who already set a key or their e-mail (by hand or through an agent)
    has set LitLoop up: the guide stays away for them too.
    """
    cfg = _config.load_config()
    done = bool(cfg.get("onboarding_done")) or any(str(cfg.get(k) or "").strip() for k in _CONFIGURED) \
        or str(cfg.get("email") or "") not in ("", _config.DEFAULT_CONFIG["email"])
    return {"done": done, "steps": STEPS}


def finish_onboarding(done: bool = True) -> dict[str, Any]:
    _config.update_config("onboarding_done", "true" if done else "false")
    return {"done": done}


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
            value = _rename_format(cfg) if f["key"] == "rename_format" else cfg.get(f["key"])
            if f["key"] == "email" and str(value).endswith(".invalid"):
                value = ""  # the shipped placeholder is not an address
            shown = {**f, "set": bool(value), "source": _source(f["key"], cfg)}
            shown["value"] = mask(value) if f["type"] == "secret" else value
            if f["key"] in _config.ENV_KEYS:
                shown["env"] = _config.ENV_KEYS[f["key"]]
            fields.append(shown)
        out.append({**g, "fields": fields})
    return out


def _check_path(value: str, pick: str) -> None:
    r"""A path setting must be a full path on a drive that exists (2026-10-19: the
    user typed ":\Users\Echo\.litloop\papers" and the app said it was saved).
    A folder that does not exist yet is fine: downloads create it."""
    p = Path(value).expanduser()
    if not p.is_absolute():
        raise ValueError(r"填写从盘符开始的完整路径，例如 D:\文献\papers")
    if not Path(p.anchor).exists():
        raise ValueError(f"找不到 {p.drive or p.anchor}，换成本机存在的盘符")
    if pick == "dir" and p.exists() and not p.is_dir():
        raise ValueError("这里是一个文件，填写文件夹的路径")
    if pick == "file" and not p.is_file():
        raise ValueError("找不到这个文件，检查路径后重新填写")


def update(key: str, value: Any) -> dict[str, Any]:
    """Save one setting from the app. Only keys on the settings page can be changed."""
    field = _FIELDS.get(key)
    if field is None:
        raise KeyError(f"{key} is not a setting the app may change")
    if field["type"] == "bool":
        value = "true" if value in (True, "true", "1", 1) else "false"
    elif field["type"] == "select" and value not in [o[0] for o in field["options"]]:
        raise ValueError(f"从列表中选择一项（{key} 不接受 {value!r}）")
    elif field["type"] == "email" and value and "@" not in str(value):
        raise ValueError("填写完整邮箱，例如 name@example.edu")
    elif field["type"] == "path" and str(value or "").strip():
        _check_path(str(value).strip(), field.get("pick", ""))
    _config.update_config(key, str(value if value is not None else "").strip())
    if key == "rename_format":
        _config.update_config("auto_rename", "true")  # the older switch must not override the choice
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


def _cannot_reach(name: str, raw: Any) -> str:
    """'无法连接 <name>：<reason>' with the raw error translated (docs/UI_COPY.md 7.2)."""
    from . import error_text
    why = error_text.reason(str(raw)) if raw else "没有响应"
    return f"无法连接 {name}" if why == "出现意外错误" else f"无法连接 {name}：{why}"


def test_sources() -> list[dict[str, Any]]:
    from . import quota
    cfg = _config.load_config()
    out = []

    live = quota.openalex_live()
    if not live or live.get("error"):
        out.append(_result("OpenAlex", False, _cannot_reach("OpenAlex", (live or {}).get("error"))))
    else:
        left, limit = live.get("credits_remaining"), live.get("credits_limit")
        if live.get("has_key"):
            out.append(_result("OpenAlex", True, f"可用，今日剩余 {left} / {limit} 积分"))
        else:
            out.append(_result("OpenAlex", None, f"未填写 Key，今日剩余 {left} / {limit} 积分"))

    key = str(cfg.get("elsevier_api_key") or "").strip()
    if not key:
        out.append(_result("Elsevier", None, "未填写"))
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
                out.append(_result("Elsevier", False, f"无法使用（HTTP {r.status_code}）"))
        except Exception as exc:
            out.append(_result("Elsevier", False, _cannot_reach("Elsevier", exc)))

    key = str(cfg.get("springer_meta_api_key") or "").strip()
    if not key:
        out.append(_result("Springer Nature", None, "未填写"))
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
                out.append(_result("Springer Nature", False, f"无法使用（HTTP {r.status_code}）"))
        except Exception as exc:
            out.append(_result("Springer Nature", False, _cannot_reach("Springer Nature", exc)))

    email = str(cfg.get("email") or "").strip()
    if not email or email.endswith(".invalid"):
        out.append(_result("Crossref", None, "未填写联系邮箱"))
    else:
        try:
            r = _get("https://api.crossref.org/works", params={"rows": 0, "mailto": email})
            out.append(_result("Crossref", r.status_code == 200,
                               "可用" if r.status_code == 200 else f"无法使用（HTTP {r.status_code}）"))
        except Exception as exc:
            out.append(_result("Crossref", False, _cannot_reach("Crossref", exc)))
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
        return [_result("保存位置", True, "可写入" + (f"，剩余 {free:.0f} GB" if free is not None else ""))]
    except OSError as exc:
        why = "没有此文件夹的写入权限" if isinstance(exc, PermissionError) else "检查保存位置是否正确"
        return [_result("保存位置", False, f"无法写入：{why}")]


def test_zotero() -> list[dict[str, Any]]:
    try:
        r = _get("http://127.0.0.1:23119/connector/ping", proxies={"http": None, "https": None})
        if r.status_code == 200:
            return [_result("Zotero", True, "Zotero 运行中")]
        return [_result("Zotero", False, f"Zotero 没有响应（HTTP {r.status_code}）")]
    except Exception:
        return [_result("Zotero", False, "Zotero 未运行")]


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
        out.append(_result("校园出口代理", None, "已填写：LitLoop 会跳过学校登录。只填学校提供的代理地址。"))
    found = proxy_check.detect()
    names, paths = found["direct_processes"], proxy_check.litloop_paths()
    programs = "、".join(found["programs"]) or "代理软件"
    if found["mode"] == "unknown":
        out.append(_result("代理软件", None, "暂不支持在此系统上检测"))
    elif found["mode"] == "none":
        out.append(_result("代理软件", True, "未检测到代理软件"))
    elif found["mode"] == "system":
        out.append(_result("代理软件", True, f"{programs} 以系统代理模式运行，LitLoop 会自动直连，无需设置"))
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
                "note": "如果规则未生效，在「扩展配置」中加入 find-process-mode: always。",
            })
        out.append(_result("代理软件", False,
                           f"{programs} 正以 TUN 模式运行，学校认证的流量会经代理出口，学校无法识别你的身份。"
                           "按下方步骤设置直连后，查看下面两项的结果。",
                           guides=guides))
    out += _egress_checks(cfg)
    return out


# A subscription paper whose full-text right says whether publishers accept this
# network's exit (Ceramics International: subscribed by most universities).
EGRESS_SAMPLE_DOI = "10.1016/j.ceramint.2024.08.002"


def _egress_checks(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Where LitLoop's own traffic leaves this computer, and whether the Elsevier
    API serves full text to this key from here.

    The API's 403 "Requestor configuration settings insufficient" is about the
    key's full-text configuration, not proof that publishers refuse this exit:
    on 2026-10-17 ScienceDirect recognized the same exit as the school's and the
    browser route downloaded the paper. So a refusal is a notice, not a failure.
    """
    import requests
    out = []
    try:
        s = requests.Session()
        s.trust_env = False  # LitLoop's own route (proxy programs in TUN mode still apply)
        info = s.get("https://ipinfo.io/json", timeout=10).json()
        where = " ".join(x for x in (info.get("city"), info.get("country")) if x)
        out.append(_result("出口 IP", None, f"{info.get('ip')}（{where}，{info.get('org') or '运营商未知'}）"))
    except Exception:
        out.append(_result("出口 IP", None, "无法查询出口 IP"))
    key = str(cfg.get("elsevier_api_key") or "").strip()
    if not key:
        out.append(_result("Elsevier 全文接口", None, "未填写 Elsevier Key"))
        return out
    from .elsevier_check import ENTITLED, NOT_ENTITLED, _head_probe
    proxy = str(cfg.get("network_proxy") or "").strip()
    code, verdict = _head_probe(EGRESS_SAMPLE_DOI, key, {"http": proxy, "https": proxy} if proxy else None)
    if verdict.startswith(ENTITLED):
        out.append(_result("Elsevier 全文接口", True, "可用：ScienceDirect 的订阅全文可直接通过接口下载"))
    elif verdict.startswith(NOT_ENTITLED):
        out.append(_result("Elsevier 全文接口", None,
                           "此 Key 没有全文权限，ScienceDirect 的文献改由浏览器从官网下载。"
                           "要判断出口是否被学校认可，关闭代理后用浏览器打开一篇 ScienceDirect "
                           "订阅文献，看页面上是否显示学校名称。"))
    else:
        out.append(_result("Elsevier 全文接口", None, f"无法判断（HTTP {code}）" if code else "无法判断：Elsevier 没有响应"))
    return out


def test_screening() -> list[dict[str, Any]]:
    """Translate one sentence with the chosen service."""
    from . import translate
    config = load_config()
    name = translate.provider(config)
    label = {"deepseek": "DeepSeek", "deepl": "DeepL", "agent": "Agent"}[name]
    if name == "agent":
        return [_result("翻译服务", None, "由 Agent 在评分时翻译，无需检测")]
    if not str(config.get(f"{name}_api_key") or "").strip():
        return [_result(label, None, "未填写 Key")]
    try:
        out = translate.translate_text("Amorphous calcium phosphate nanoparticles release doxorubicin at pH 5.5.", config)
    except translate.TranslationError as exc:
        return [_result(label, False, str(exc))]
    return [_result(label, True, f"可用，示例译文：{out}")]


TESTS = {"sources": test_sources, "downloads": test_downloads, "network": test_network, "zotero": test_zotero,
         "screening": test_screening}


def run_test(group: str) -> dict[str, Any]:
    if group not in TESTS:
        raise KeyError(f"no test for {group}")
    t0 = time.time()
    return {"group": group, "results": TESTS[group](), "seconds": round(time.time() - t0, 1)}
