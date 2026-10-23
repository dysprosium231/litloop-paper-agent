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
    (r"DOI is not registered", "这个 DOI 不存在", "检查 DOI 是否抄错"),
    (r"skipped by user", "已跳过", "可在任务结束后重试未完成项"),
    (r"the school does not subscribe", "学校没有订阅：已用学校身份登录出版商网站，仍没有全文",
     "学校认证最终也到同一出版商，已跳过。可通过图书馆文献传递获取"),
    (r"NOT_ENTITLED", "学校未订阅此期刊，或当前不在校园网", "连接校园网或在「设置 → 学校认证」中登录后重试"),
    (r"No valid session|session (expired|invalid)|login required|需要机构登录",
     "学校认证已过期或未登录", "在「设置 → 学校认证」中重新登录"),
    (r"Login failed or timed out", "登录超时", "确认已安装 Chrome 后重试"),
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
    (r"school login not completed", "学校认证的登录没有完成", "重试，并在弹出的窗口中完成登录"),
    (r"Cloudflare|anti-bot|human check", "网站要求人机验证，没有通过", "重试，并在弹出的窗口中完成验证"),
    (r"could not open the publisher page|site unreachable|\bERR_[A-Z_]+", "打不开出版商网页", "检查网络或代理后重试"),
    (r"browser not available", "浏览器无法启动", "确认已安装 Chrome 后重试"),
    (r"no PDF link", "出版商网页上没有 PDF 链接，可能没有权限", "连接校园网或在「设置 → 学校认证」中登录后重试"),
    (r"already being downloaded|\bin progress\b", "此文献正在另一个下载任务中下载", "等待那个任务完成"),
    (r"full_text_extracted_but_no_pdf", "只取得网页全文，没有 PDF", ""),
    (r"PDF file not found on disk", "下载后找不到 PDF 文件", "检查保存位置是否可写入"),
    (r"PDF not found after batch download", "所有来源都没有找到 PDF", ""),
    (r"arXiv PDF not available", "arXiv 上没有这篇的 PDF", ""),
    (r"no OA url|fast lane failed", "未找到开放获取版本", ""),
    (r"\b429\b|rate.?limit|too many requests", "请求过于频繁，已被限流", "稍后重试"),
    (r"\b40[13]\b|forbidden|unauthori[sz]ed", "出版商拒绝访问，可能需要学校认证", "连接校园网或在「设置 → 学校认证」中登录后重试"),
    (r"\b404\b|not found", "出版商处找不到此文献", ""),
    (r"timed? ?out|timeout", "连接超时", "检查网络后重试"),
    (r"Unpaywall.*email|email.*Unpaywall", "Unpaywall 需要你的真实邮箱", "在「设置 → 检索接口」中填写联系邮箱"),
    (r"\bpaywall\b", "需要订阅才能下载", "连接校园网或在「设置 → 学校认证」中登录后重试"),
    (r"download exception|missing result|^invalid$", "下载过程出错", "详情已写入日志，可重试"),
    (r"^no result$|all sources failed|no source|no PDF found", "所有来源都没有找到 PDF", ""),
    (r"unrecognized identifier|invalid DOI", "无法识别的 DOI", "检查 DOI 是否完整"),
]

_COMPILED = [(re.compile(p, re.I), r, n) for p, r, n in _RULES]
_EXCEPTION = re.compile(r"^[A-Z][A-Za-z]+(Error|Exception)\b")


def _match(raw: str) -> tuple[str, str] | None:
    for rx, reason_text, step in _COMPILED:
        if rx.search(raw):
            return reason_text, step
    return None


# Which failure explains a paper best, when several ways failed (most telling first).
_FAILURE_ORDER = ("not_entitled", "login_abandoned", "cloudflare_blocked", "navigate_failed",
                  "timeout", "paywall", "no_pdf_found", "browser_unavailable", "not_found")

# A browser strategy reports (error_type, action) instead of a sentence; this is
# the raw text a failed step records for it, worded so a rule above matches.
_RAW_BY_TYPE = {
    "not_entitled": "NOT_ENTITLED",
    "login_abandoned": "school login not completed",
    "cloudflare_blocked": "Cloudflare anti-bot protection blocked access",
    "navigate_failed": "could not open the publisher page",
    "paywall": "paywall — requires institutional access",
    "no_pdf_found": "no PDF link on the publisher page",
    "browser_unavailable": "browser not available",
}


def raw_for(error_type: str, action: str = "") -> str:
    """Raw reason for a browser strategy's (error_type, action)."""
    if error_type == "not_entitled" and action == "no_subscription":
        return "the school does not subscribe: logged into the publisher with the school's identity, still no full text"
    return _RAW_BY_TYPE.get(error_type, error_type)


def pick_failure(result: dict | None) -> str | None:
    """The raw reason to show for a failed download: the result's own reason when it
    is a known one (skipped, cancelled, not subscribed...), else the most telling of
    the ways it tried (``result["attempts"]``)."""
    if not result or result.get("success"):
        return None
    own = result.get("error") or result.get("reason")
    hit = _match(str(own)) if own else None
    failed = [s for s in result.get("attempts") or [] if not s.get("ok") and s.get("reason")]
    if own and result.get("error_type") in ("skipped", "cancelled"):
        return str(own)
    # The result's own reason, unless one of the ways tried explains it better
    # ("All sources failed" never does; a lane's paywall loses to the school's
    # "not subscribed" whichever came last).
    rank = {t: i for i, t in enumerate(_FAILURE_ORDER)}
    own_rank = rank.get(str(result.get("error_type") or ""), len(rank))
    better = [s for s in failed if rank.get(str(s.get("error_type") or ""), len(rank)) < own_rank]
    if own and hit is not None and hit[0] != "所有来源都没有找到 PDF" and not better:
        return str(own)
    for kind in _FAILURE_ORDER:
        for s in failed:
            if s.get("error_type") == kind:
                return str(s["reason"])
    for s in failed:
        if _match(str(s["reason"])) is not None:
            return str(s["reason"])
    return str(own) if own else None


def steps_for_app(attempts: list | None) -> list[dict] | None:
    """The ways a paper was tried, for the app's download page: the ones that
    took time or opened a site (quick API misses are left out), with the reason
    in plain words."""
    out = []
    for s in attempts or []:
        src = str(s.get("source") or "")
        if s.get("ok") or s.get("route") == "school" or "Browser" in src or float(s.get("seconds") or 0) >= 1:
            out.append({"source": src, "route": s.get("route"), "seconds": s.get("seconds"), "ok": bool(s.get("ok")),
                        "why": None if s.get("ok") else reason(s.get("reason")) if s.get("reason") else None})
    return out or None


def reason(raw: str | None) -> str | None:
    """Short reason for a status line, e.g. '学校未订阅此期刊，或当前不在校园网'."""
    if not raw:
        return None
    hit = _match(str(raw))
    return hit[0] if hit else "出现意外错误"  # docs/UI_COPY.md 7: the raw text goes in the tooltip


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
