"""Abstract translation for the app: DeepSeek or DeepL, or left to the agent.

Config:
- ``abstract_translation``: which ratings are translated without asking —
  "高", "高中", "all" or "off" (``screening.translate_wanted``).
- ``translation_provider``: "deepseek", "deepl" or "agent". With an API
  provider the app backend translates a paper when the user opens it, once,
  and stores the result in the record (``abstract_zh``); the agent is not
  asked. With "agent" the agent adds ``abstract_zh`` when it rates.
- ``deepseek_api_key`` / ``deepl_api_key``. A DeepL key ending in ":fx" is a
  free-plan key and goes to the free endpoint.

Errors raise ``TranslationError`` with text the app shows as is.
"""

from __future__ import annotations

from typing import Any

import requests

from ..config import load_config

PROVIDERS = ("deepseek", "deepl", "agent")

# DeepSeek is a chat model: say what to keep, so formulas, units and names survive.
PROMPT = ("把下面的英文论文摘要翻译成简体中文学术语言。化学式、元素符号、缩写、数值和单位、"
          "基因与蛋白名称保持原样；专业术语用通行的中文译名，没有通行译名的保留英文。"
          "不要增删内容，不要解释，只输出译文。")


class TranslationError(Exception):
    pass


def provider(config: dict[str, Any] | None = None) -> str:
    value = str((config or load_config()).get("translation_provider") or "agent")
    return value if value in PROVIDERS else "agent"


def by_api(config: dict[str, Any] | None = None) -> bool:
    """True when the app translates (DeepSeek / DeepL), not the agent."""
    return provider(config) != "agent"


def _key(name: str, config: dict[str, Any]) -> str:
    key = str(config.get(f"{name}_api_key") or "").strip()
    if not key:
        label = "DeepSeek" if name == "deepseek" else "DeepL"
        raise TranslationError(f"未填写 {label} Key。在「设置 → 筛选」中填写")
    return key


def _fail(service: str, r: requests.Response) -> TranslationError:
    if r.status_code in (401, 403):
        return TranslationError(f"{service} Key 无效")
    if r.status_code == 402:
        return TranslationError(f"{service} 账户余额不足")
    if r.status_code == 456:
        return TranslationError("DeepL 本月额度已用完")
    if r.status_code == 429:
        return TranslationError(f"{service} 请求过于频繁，已被限流")
    return TranslationError(f"{service} 返回错误（HTTP {r.status_code}）")


def _deepseek(text: str, config: dict[str, Any]) -> str:
    r = requests.post("https://api.deepseek.com/chat/completions", timeout=60,
                      headers={"Authorization": f"Bearer {_key('deepseek', config)}"},
                      json={"model": config.get("deepseek_model") or "deepseek-chat", "temperature": 1.3,
                            "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": text}]})
    if r.status_code != 200:
        raise _fail("DeepSeek", r)
    return r.json()["choices"][0]["message"]["content"].strip()


def _deepl(text: str, config: dict[str, Any]) -> str:
    key = _key("deepl", config)
    host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
    r = requests.post(f"https://{host}/v2/translate", timeout=60,
                      headers={"Authorization": f"DeepL-Auth-Key {key}"},
                      json={"text": [text], "source_lang": "EN", "target_lang": "ZH-HANS"})
    if r.status_code != 200:
        raise _fail("DeepL", r)
    return r.json()["translations"][0]["text"].strip()


def translate_text(text: str, config: dict[str, Any] | None = None) -> str:
    """English abstract -> Chinese with the configured API provider."""
    config = config or load_config()
    name = provider(config)
    if name == "agent":
        raise TranslationError("翻译服务设为 Agent，由 Agent 在评分时翻译")
    try:
        out = (_deepseek if name == "deepseek" else _deepl)(text, config)
    except requests.Timeout as exc:
        raise TranslationError("连接翻译服务超时。检查网络后重试") from exc
    except requests.RequestException as exc:
        raise TranslationError("翻译服务没有响应。检查网络或代理设置") from exc
    except (KeyError, IndexError, ValueError) as exc:
        raise TranslationError("翻译服务返回了无法识别的结果") from exc
    if not out:
        raise TranslationError("翻译服务返回了空结果")
    return out
