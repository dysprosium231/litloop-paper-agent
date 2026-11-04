"""The built-in agent's model client: both protocols, one result shape."""

from __future__ import annotations

import json

import pytest

from litloop.agents import client


def _capture(monkeypatch, response: dict):
    seen = {}

    def fake_post(url, headers, body, config, timeout):
        seen.update(url=url, headers=headers, body=body, timeout=timeout)
        return response

    monkeypatch.setattr(client, "_post", fake_post)
    return seen


def test_defaults_to_the_openai_compatible_api():
    cfg = client.model_config({})
    assert cfg["provider"] == "openai" and cfg["protocol"] == "openai"
    assert cfg["base_url"] == "https://api.openai.com/v1"
    assert client.model_config({"builtin_agent_base_url": "http://127.0.0.1:11434/v1/"})["base_url"] == (
        "http://127.0.0.1:11434/v1")
    assert client.model_config({"builtin_agent_provider": "anthropic"})["base_url"] == "https://api.anthropic.com"


def test_anthropic_request_and_reply(monkeypatch):
    seen = _capture(monkeypatch, {"content": [{"type": "text", "text": "好的"},
                                              {"type": "tool_use", "name": "search", "input": {"q": "SiC"}}],
                                  "stop_reason": "tool_use", "usage": {"input_tokens": 12}})
    out = client.chat([{"role": "system", "content": "你是助手"}, {"role": "user", "content": "查 SiC"}],
                      [{"name": "search", "description": "检索", "parameters": {"type": "object"}}],
                      config={"builtin_agent_provider": "anthropic", "builtin_agent_api_key": "sk-a",
                              "builtin_agent_model": "claude-sonnet-4-5"})
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-a" and seen["headers"]["anthropic-version"]
    assert seen["body"]["system"] == "你是助手"
    assert seen["body"]["messages"] == [{"role": "user", "content": "查 SiC"}]
    assert seen["body"]["tools"][0]["input_schema"] == {"type": "object"}
    assert out["text"] == "好的"
    assert out["tool_calls"] == [{"name": "search", "arguments": {"q": "SiC"}}]
    assert out["stop"] == "tool_use"


def test_openai_request_and_reply(monkeypatch):
    seen = _capture(monkeypatch, {"choices": [{"message": {
        "content": "我查一下",
        "tool_calls": [{"function": {"name": "search", "arguments": json.dumps({"q": "PDC"})}}]},
        "finish_reason": "tool_calls"}]})
    out = client.chat([{"role": "user", "content": "查 PDC"}],
                      [{"name": "search", "parameters": {"type": "object"}}],
                      config={"builtin_agent_api_key": "sk-o", "builtin_agent_model": "deepseek-chat",
                              "builtin_agent_base_url": "https://api.deepseek.com"},
                      system="你是助手")
    assert seen["url"] == "https://api.deepseek.com/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer sk-o"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "你是助手"}
    assert seen["body"]["tools"][0]["type"] == "function"
    assert seen["body"]["tools"][0]["function"]["name"] == "search"
    assert out["tool_calls"] == [{"name": "search", "arguments": {"q": "PDC"}}]
    assert out["stop"] == "tool_calls"


def test_bad_tool_arguments_do_not_crash(monkeypatch):
    _capture(monkeypatch, {"choices": [{"message": {"tool_calls": [
        {"function": {"name": "search", "arguments": "{not json"}}]}}]})
    out = client.chat([{"role": "user", "content": "x"}], config={
        "builtin_agent_api_key": "k", "builtin_agent_model": "m"})
    assert out["tool_calls"] == [{"name": "search", "arguments": {}}]


def test_a_missing_model_name_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(client, "_post", lambda *a, **k: pytest.fail("no request expected"))
    assert client.test_model({}) == {"ok": False, "detail": "还没有填模型名"}
    with pytest.raises(ValueError, match="模型名"):
        client.chat([{"role": "user", "content": "x"}], config={})


def test_an_http_error_names_the_status(monkeypatch):
    class Resp:
        status_code = 401
        text = '{"error": "bad key"}'
        reason = "Unauthorized"

    monkeypatch.setattr(client.requests, "post", lambda *a, **k: Resp())
    with pytest.raises(RuntimeError, match="HTTP 401"):
        client.chat([{"role": "user", "content": "x"}],
                    config={"builtin_agent_api_key": "k", "builtin_agent_model": "m"})


def test_a_model_that_only_chats_is_not_usable(monkeypatch):
    _capture(monkeypatch, {"choices": [{"message": {"content": "ping 就是 1"}}]})
    reply = client.test_model({"builtin_agent_api_key": "k", "builtin_agent_model": "m"})
    assert reply["ok"] is False and "没有发起工具调用" in reply["detail"]


def test_a_model_that_calls_the_tool_passes(monkeypatch):
    _capture(monkeypatch, {"choices": [{"message": {"tool_calls": [
        {"function": {"name": "ping", "arguments": '{"n": 1}'}}]}}]})
    reply = client.test_model({"builtin_agent_api_key": "k", "builtin_agent_model": "m"})
    assert reply["ok"] is True and "支持工具调用" in reply["detail"]
