"""Raw engine / Zotero errors become the wording of docs/UI_COPY.md section 7."""

import pytest

from litloop import error_text


@pytest.mark.parametrize("raw, want", [
    ("NOT_ENTITLED", "学校未订阅此期刊，或当前不在校园网"),
    ("No valid session. Run litloop_login(identifier='10.1/x')", "学校认证已过期或未登录"),
    ("zotero-mcp not found; install it (pip install zotero-mcp-server)", "未找到 zotero-mcp"),
    ("zotero-mcp bridge timed out", "Zotero 没有响应"),
    ("fast lane failed (no OA url / API miss)", "未找到开放获取版本"),
    ("HTTP 429 Too Many Requests", "请求过于频繁，已被限流"),
    ("HTTPError 403 Forbidden", "出版商拒绝访问，可能需要学校认证"),
    ("ReadTimeout(HTTPSConnectionPool(...))", "连接超时"),
    ("no result", "所有来源都没有找到 PDF"),
    ("cancelled", "已取消"),
    ("zotero-mcp not installed", "未找到 zotero-mcp"),
    ("User denied", "Zotero 未允许本地写入"),
    ("Attachment `ABCD1234` exists but holds no file", "Zotero 里的附件为空"),
    ("something nobody expected", "出现意外错误"),
])
def test_reason(raw, want):
    assert error_text.reason(raw) == want


def test_empty_is_none():
    assert error_text.reason(None) is None and error_text.message("", "下载") is None


def test_message_has_reason_and_next_step():
    assert error_text.message("NOT_ENTITLED", "下载") == (
        "无法下载：学校未订阅此期刊，或当前不在校园网。连接校园网或在「设置 → 学校认证」中登录后重试。")
    assert error_text.message("no result", "下载") == "无法下载：所有来源都没有找到 PDF。"


def test_message_never_shows_the_raw_text():
    assert error_text.message("KeyError: 'doi'", "下载") == "抱歉，下载时出现意外错误。详情已写入日志。"
    assert error_text.message("weird engine text", "推送") == "无法推送：出现意外错误"


def test_cancelled_is_plain():
    assert error_text.message("cancelled", "下载") == "已取消"


def test_app_server_adds_error_text_and_keeps_raw():
    from litloop import app_server
    item = {"error": "NOT_ENTITLED"}
    app_server._explain(item)
    assert item == {"error": "NOT_ENTITLED", "error_text": "学校未订阅此期刊，或当前不在校园网"}
    job = {"error": "zotero-mcp bridge timed out"}
    app_server._explain(job, "推送", full=True)
    assert job["error_text"] == "无法推送：Zotero 没有响应。确认 Zotero 已启动后重试。"
    clean = {"error": None}
    app_server._explain(clean)
    assert "error_text" not in clean
