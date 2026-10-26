"""Abstract translation: DeepSeek / DeepL from the app, or the agent when it rates."""

import pytest
import requests
from fastapi.testclient import TestClient

from litloop import config, server
from litloop.desktop import server as app_server, translate
from litloop.search import screening

TOKEN = "t0ken"


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(screening._config, "DATA_DIR", tmp_path)
    values = {"translation_provider": "deepseek", "deepseek_api_key": "sk-x", "abstract_translation": "高"}
    monkeypatch.setattr(translate, "load_config", lambda: values)
    monkeypatch.setattr(config, "load_config", lambda: values)
    return values


@pytest.fixture
def session(cfg):
    return screening.save("q", [{"doi": "10.1/a", "title": "A", "relevance": "低", "reason": "r", "abstract": "Low."},
                                {"doi": "10.1/b", "title": "B", "relevance": "高", "reason": "r", "abstract": "High."}])["session"]


def test_deepseek_request_and_answer(cfg, monkeypatch):
    seen = {}

    def post(url, **kw):
        seen.update(url=url, **kw)
        return Resp(200, {"choices": [{"message": {"content": " 译文 "}}]})
    monkeypatch.setattr(requests, "post", post)
    assert translate.translate_text("Text.") == "译文"
    assert seen["url"].startswith("https://api.deepseek.com/")
    assert seen["headers"]["Authorization"] == "Bearer sk-x"
    assert seen["json"]["messages"][1]["content"] == "Text."


def test_deepl_free_key_goes_to_the_free_endpoint(cfg, monkeypatch):
    cfg.update(translation_provider="deepl", deepl_api_key="abc:fx")
    seen = {}

    def post(url, **kw):
        seen.update(url=url, **kw)
        return Resp(200, {"translations": [{"text": "译文"}]})
    monkeypatch.setattr(requests, "post", post)
    assert translate.translate_text("Text.") == "译文"
    assert seen["url"] == "https://api-free.deepl.com/v2/translate" and seen["json"]["target_lang"] == "ZH-HANS"


@pytest.mark.parametrize("status, words", [(401, "Key 无效"), (402, "余额不足"), (456, "额度已用完"), (500, "HTTP 500")])
def test_errors_are_user_text(cfg, monkeypatch, status, words):
    if status == 456:
        cfg.update(translation_provider="deepl", deepl_api_key="k")
    monkeypatch.setattr(requests, "post", lambda url, **kw: Resp(status, {}))
    with pytest.raises(translate.TranslationError, match=words):
        translate.translate_text("Text.")


def test_missing_key(cfg):
    cfg["deepseek_api_key"] = ""
    with pytest.raises(translate.TranslationError, match="未填写 DeepSeek Key"):
        translate.translate_text("Text.")


def test_scope_values():
    assert screening.translate_wanted({"abstract_translation": "高中"}) == ["高", "中"]
    assert screening.translate_wanted({"abstract_translation": "all"}) == list(screening.RELEVANCE)
    assert screening.translate_wanted({"abstract_translation": "off"}) == []


def test_app_translates_once_and_keeps_it(cfg, session, monkeypatch):
    calls = []
    monkeypatch.setattr(translate, "translate_text", lambda text, config=None: calls.append(text) or "高的译文")
    c = TestClient(app_server.create_app(TOKEN))
    c.headers["X-LitLoop-Token"] = TOKEN
    assert c.get("/api/translation").json() == {"provider": "deepseek", "auto": ["高"], "ready": True}
    assert c.post(f"/api/sessions/{session}/translate", json={"doi": "10.1/b"}).json() == {"abstract_zh": "高的译文"}
    assert c.post(f"/api/sessions/{session}/translate", json={"doi": "10.1/b"}).json() == {"abstract_zh": "高的译文"}
    assert calls == ["High."]  # the second call read the record
    item = next(it for it in screening.load(session)["items"] if it["doi"] == "10.1/b")
    assert item["abstract_zh"] == "高的译文"


def test_app_translate_error_is_shown_text(cfg, session, monkeypatch):
    def fail(text, config=None):
        raise translate.TranslationError("DeepSeek 账户余额不足")
    monkeypatch.setattr(translate, "translate_text", fail)
    c = TestClient(app_server.create_app(TOKEN))
    c.headers["X-LitLoop-Token"] = TOKEN
    r = c.post(f"/api/sessions/{session}/translate", json={"doi": "10.1/b"})
    assert r.status_code == 502 and r.json()["detail"] == "无法翻译：DeepSeek 账户余额不足。"


def test_agent_translations_only_when_the_agent_translates(cfg, session):
    assert screening.untranslated(session) == []  # DeepSeek translates: nothing asked of the agent
    cfg["translation_provider"] = "agent"
    assert [b["doi"] for b in screening.untranslated(session)] == ["10.1/b"]
    out = screening.translate(session, [{"doi": "10.1/B", "abstract_zh": "译文"}, {"doi": "10.1/zzz", "abstract_zh": "x"}])
    assert out["saved"] == 1 and out["not_found"] == ["10.1/zzz"] and out["left"] == []


def test_screening_tool_translate_action(cfg, session):
    cfg["translation_provider"] = "agent"
    out = server.litloop_screening(action="translate", session=session, items=[{"doi": "10.1/b", "abstract_zh": "译文"}])
    assert '"saved": 1' in out
    # a later re-rating keeps the translation
    screening.save("", [{"doi": "10.1/b", "title": "B", "relevance": "中", "reason": "r"}], session=session)
    assert next(it for it in screening.load(session)["items"] if it["doi"] == "10.1/b")["abstract_zh"] == "译文"
