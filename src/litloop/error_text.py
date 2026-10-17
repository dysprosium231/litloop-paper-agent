"""Raw errors (download engine, zotero-mcp, APIs) -> text the app shows.

The one place that turns raw error strings into the wording docs/UI_COPY.md
(section 7) asks for. Files keep the raw text for logs and debugging; the app
backend translates when it hands data to the app, and the app shows the raw
text only in a tooltip.

``reason(raw)`` gives the short reason for a status line ("学校未订阅此期刊…");
``message(raw, action)`` the full sentence "无法<action>：<reason>。<next step>".
Unknown errors fall back to a generic sentence, never to the raw text.
"""

from __future__ import annotations

import re

# (pattern, reason, next step). Matched in order against the raw text,
# case-insensitively; the first hit wins. Keep the most specific first.
_RULES: list[tuple[str, str, str]] = [
    (r"\bcancel", "已取消", ""),
    (r"NOT_ENTITLED", "学校未订阅此期刊，或当前不在校园网", "连接校园网或在「设置 → 学校认证」中登录后重试"),
    (r"No valid session|session (expired|invalid)|login required|需要机构登录",
     "学校认证已过期或未登录", "在「设置 → 学校认证」中重新登录"),
    (r"Login failed or timed out", "学校登录超时", "确认已安装 Chrome 后重试"),
    (r"WebVPN not configured|No WebVPN URL", "未设置 WebVPN", "在「设置 → 学校认证」中填写学校代号"),
    (r"zotero-mcp is too old", "zotero-mcp 版本过旧", "更新 zotero-mcp 后重试"),
    (r"zotero-mcp not (found|installed)|cannot start zotero-mcp", "未找到 zotero-mcp", "在「设置 → Zotero」中填写它的 Python 路径"),
    (r"\bdenied\b|not authori[sz]ed by the user", "Zotero 未允许本地写入", "重新授权，并在 Zotero 弹出的窗口中选择允许"),
    (r"bridge timed out|Zotero is not reachable|Connection refused.*23119|23119.*(refused|timed out)",
     "Zotero 没有响应", "确认 Zotero 已启动后重试"),
    (r"no local PDF", "本机没有此文献的 PDF", "先下载此文献"),
    (r"cannot create the collection|empty collection path|Failed to create collection",
     "无法创建分类", "检查分类路径后重试"),
    (r"holds no file|exists but holds no file", "Zotero 里的附件为空", "在「附件检查」中重新附加"),
    (r"full_text_extracted_but_no_pdf", "只取得网页全文，没有 PDF", ""),
    (r"PDF file not found on disk", "下载后找不到 PDF 文件", "检查保存位置是否可写入"),
    (r"no OA url|fast lane failed", "未找到开放获取版本", ""),
    (r"\b429\b|rate.?limit|too many requests", "请求过于频繁，已被限流", "稍后重试"),
    (r"\b40[13]\b|forbidden|unauthori[sz]ed", "出版商拒绝访问，可能需要学校认证", "连接校园网或在「设置 → 学校认证」中登录后重试"),
    (r"\b404\b|not found", "出版商处找不到此文献", ""),
    (r"timed? ?out|timeout", "连接超时", "检查网络后重试"),
    (r"paywall", "需要订阅才能下载", "连接校园网或在「设置 → 学校认证」中登录后重试"),
    (r"^no result$|all sources failed|no source", "所有来源都没有找到 PDF", ""),
    (r"unrecognized identifier", "无法识别的 DOI", "检查 DOI 是否完整"),
]

_COMPILED = [(re.compile(p, re.I), r, n) for p, r, n in _RULES]
_EXCEPTION = re.compile(r"^[A-Z][A-Za-z]+(Error|Exception)\b")


def _match(raw: str) -> tuple[str, str] | None:
    for rx, reason_text, step in _COMPILED:
        if rx.search(raw):
            return reason_text, step
    return None


def reason(raw: str | None) -> str | None:
    """Short reason for a status line, e.g. '学校未订阅此期刊，或当前不在校园网'."""
    if not raw:
        return None
    hit = _match(str(raw))
    return hit[0] if hit else "出现意外错误"


def message(raw: str | None, action: str) -> str | None:
    """Full sentence: '无法<action>：<reason>。<next step>。' (or the generic one)."""
    if not raw:
        return None
    raw = str(raw)
    hit = _match(raw)
    if hit is None:
        if _EXCEPTION.match(raw):
            return f"抱歉，{action}时出现意外错误。详情已写入日志。"
        return f"无法{action}：出现意外错误"
    reason_text, step = hit
    if reason_text == "已取消":
        return "已取消"
    return f"无法{action}：{reason_text}。" + (f"{step}。" if step else "")
