"""Tests for optional component install progress and app update checks."""

from __future__ import annotations

import io
import time

import pytest
import requests
from fastapi.testclient import TestClient

from litloop import __version__, optional
from litloop.desktop import server as app_server
from litloop.search import screening

TOKEN = "t0ken"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(screening._config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_server, "_APP_UPDATE", {"at": 0.0, "data": None})
    c = TestClient(app_server.create_app(TOKEN))
    c.headers["X-LitLoop-Token"] = TOKEN
    return c


@pytest.fixture(autouse=True)
def _reset_optional_status(monkeypatch):
    monkeypatch.setattr(optional, "_STATUS", {})


def test_install_status_unknown_and_idle(monkeypatch):
    with pytest.raises(KeyError):
        optional.install_status("nope")

    monkeypatch.setattr(optional, "installed", lambda key: False)
    st = optional.install_status("layout")
    assert st == {
        "key": "layout",
        "installed": False,
        "state": "idle",
        "stage": "",
        "elapsed": 0.0,
        "line": "",
        "ok": None,
        "log": "",
    }


def test_install_streams_stages_and_line_truncation(tmp_path, monkeypatch):
    monkeypatch.setenv("LITLOOP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(optional, "uv_command", lambda: "uv")

    is_installed = False

    def fake_installed(key):
        return is_installed

    monkeypatch.setattr(optional, "installed", fake_installed)

    long_line = "Downloading " + ("x" * 220)
    snapshots = []

    class FakeStream:
        def __init__(self, lines):
            self._lines = list(lines)

        def __iter__(self):
            for idx, line in enumerate(self._lines):
                if idx == 1:
                    snapshots.append(("before_download", optional.install_status("layout")))
                elif idx == 2:
                    snapshots.append(("during_download", optional.install_status("layout")))
                yield line
            snapshots.append(("during_install", optional.install_status("layout")))

        def close(self):
            pass

    class FakeProc:
        def __init__(self, *args, **kwargs):
            self.stdout = FakeStream([
                "Using Python 3.12\n",
                f"{long_line}\n",
                "Installing collected packages: onnxruntime\n",
            ])

        def wait(self):
            nonlocal is_installed
            is_installed = True
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(optional.subprocess, "Popen", FakeProc)

    res = optional.install("layout")
    assert res["ok"] is True
    assert "Installing collected packages: onnxruntime" in res["log"]

    stages = [snap["stage"] for _, snap in snapshots]
    assert stages == ["准备环境", "正在下载依赖", "正在安装"]
    assert all(snap["state"] == "running" for _, snap in snapshots)
    assert len(snapshots[1][1]["line"]) == 160

    final = optional.install_status("layout")
    assert final["state"] == "done"
    assert final["ok"] is True
    assert final["installed"] is True
    assert final["stage"] == "正在校验"
    assert isinstance(final["elapsed"], float)


def test_install_failure_reasons_in_chinese(tmp_path, monkeypatch):
    monkeypatch.setenv("LITLOOP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(optional, "installed", lambda key: False)

    # 1. Missing uv
    monkeypatch.setattr(optional, "uv_command", lambda: None)
    res_no_uv = optional.install("layout")
    assert res_no_uv["ok"] is False
    assert "找不到 uv" in res_no_uv["log"]
    assert optional.install_status("layout")["state"] == "failed"

    # 2. Network / mirror error
    monkeypatch.setattr(optional, "uv_command", lambda: "uv")

    class NetworkFailProc:
        def __init__(self, *args, **kwargs):
            self.stdout = io.StringIO("error: Failed to fetch: Connection timed out\n")

        def wait(self):
            return 1

        def kill(self):
            pass

    monkeypatch.setattr(optional.subprocess, "Popen", NetworkFailProc)
    res_net = optional.install("layout")
    assert res_net["ok"] is False
    assert "网络或镜像连接失败" in res_net["log"]
    assert "Connection timed out" in res_net["log"]
    assert optional.install_status("layout")["state"] == "failed"

    # 3. Timeout
    class SlowProc:
        def __init__(self, *args, **kwargs):
            self._killed = False
            self.stdout = self._iter_lines()

        def _iter_lines(self):
            yield "Downloading onnxruntime\n"
            while not self._killed:
                time.sleep(0.01)

        def wait(self):
            return -9

        def kill(self):
            self._killed = True

    monkeypatch.setattr(optional.subprocess, "Popen", SlowProc)
    res_timeout = optional.install("layout", timeout=0.05)
    assert res_timeout["ok"] is False
    assert "超时" in res_timeout["log"] or "超过了" in res_timeout["log"]
    assert optional.install_status("layout")["state"] == "failed"


def test_component_status_endpoint(client, monkeypatch):
    monkeypatch.setattr(optional, "installed", lambda key: False)
    resp = client.get("/api/components/layout/status")
    assert resp.status_code == 200
    assert resp.json()["key"] == "layout"
    assert resp.json()["state"] == "idle"

    missing = client.get("/api/components/nope/status")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "没有这个可选组件"


def test_app_update_available_latest_and_network_error(client, monkeypatch):
    calls = []

    class FakeResp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get_update(url, headers=None, timeout=None):
        calls.append((url, timeout))
        return FakeResp(200, {
            "tag_name": "v99.0.0",
            "html_url": "https://github.com/dysprosium231/litloop-paper-agent/releases/tag/v99.0.0",
            "body": "新版本发布",
        })

    monkeypatch.setattr(requests, "get", fake_get_update)
    r1 = client.get("/api/app-update").json()
    assert r1["current"] == __version__
    assert r1["latest"] == "99.0.0"
    assert r1["available"] is True
    assert r1["notes"] == "新版本发布"
    assert r1["error"] == ""

    # Cached within 10 minutes
    assert client.get("/api/app-update").json() == r1
    assert len(calls) == 1

    # Bypass cache with fresh=true -> already latest
    def fake_get_same(url, headers=None, timeout=None):
        calls.append((url, timeout))
        return FakeResp(200, {
            "tag_name": f"v{__version__}",
            "html_url": "https://github.com/dysprosium231/litloop-paper-agent/releases/latest",
            "body": "",
        })

    monkeypatch.setattr(requests, "get", fake_get_same)
    r2 = client.get("/api/app-update?fresh=true").json()
    assert r2["available"] is False
    assert r2["latest"] == __version__
    assert r2["error"] == ""
    assert len(calls) == 2

    # Graceful degradation on network error
    def fake_get_fail(url, headers=None, timeout=None):
        raise requests.ConnectionError("unreachable")

    monkeypatch.setattr(requests, "get", fake_get_fail)
    r3 = client.get("/api/app-update?fresh=true")
    assert r3.status_code == 200
    body = r3.json()
    assert body["available"] is False
    assert body["current"] == __version__
    assert "无法检查更新" in body["error"]


def test_zotero_mcp_isolated_install_and_settings_actions(tmp_path, monkeypatch, client):
    import sys
    from litloop.desktop import settings as app_settings
    from litloop.zotero import client as zotero_client

    monkeypatch.setenv("LITLOOP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(optional, "uv_command", lambda: "uv")
    zotero_client.reset_discovery()

    # Status endpoint works for zotero_mcp
    resp = client.get("/api/components/zotero_mcp/status")
    assert resp.status_code == 200
    assert resp.json()["key"] == "zotero_mcp"
    assert resp.json()["installed"] is False

    # Settings test_components and test_zotero offer one-click install when missing
    monkeypatch.setattr(zotero_client, "discover_zotero_mcp", lambda cfg: None)
    monkeypatch.setattr(app_settings, "_get", lambda *a, **kw: type("R", (), {"status_code": 200})())
    comp_res = {r["name"]: r for r in app_settings.test_components()}
    assert comp_res["Zotero 桥接"]["action"] == {"key": "zotero_mcp", "label": "安装（约 20 MB）"}
    zot_res = {r["name"]: r for r in app_settings.test_zotero()}
    assert zot_res["zotero-mcp"]["action"] == {"key": "zotero_mcp", "label": "一键安装"}

    seen_cmd = []
    reset_calls = []
    monkeypatch.setattr(zotero_client, "reset_discovery", lambda: reset_calls.append(True))

    class FakeProc:
        def __init__(self, cmd, **kwargs):
            seen_cmd.extend(cmd)
            self.stdout = io.StringIO("Resolved 1 package\nInstalling collected packages: zotero-mcp-server\n")

        def wait(self):
            target = optional.component_dir("zotero_mcp")
            (target / "zotero_mcp").mkdir(parents=True, exist_ok=True)
            (target / "zotero_mcp" / "__init__.py").write_text("", encoding="utf-8")
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(optional.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(optional, "_verify_isolated", lambda target, mod: True)

    sys_path_before = list(sys.path)
    out = optional.install("zotero_mcp")
    assert out["ok"] is True
    assert "--target" in seen_cmd
    target_dir = optional.component_dir("zotero_mcp")
    assert seen_cmd[seen_cmd.index("--target") + 1] == str(target_dir)
    assert str(target_dir) not in sys.path
    assert sys.path == sys_path_before
    assert reset_calls == [True]

    # Verification failure in isolated subprocess reports ok=False
    monkeypatch.setattr(optional, "_verify_isolated", lambda target, mod: False)
    fail = optional.install("zotero_mcp")
    assert fail["ok"] is False
    assert "仍无法导入组件" in fail["log"]


def test_uv_install_sets_native_tls_without_overriding_explicit_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LITLOOP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("UV_NATIVE_TLS", raising=False)
    monkeypatch.setattr(optional, "uv_command", lambda: "uv")
    monkeypatch.setattr(optional, "installed", lambda key: True)

    seen_envs = []

    class FakeProc:
        def __init__(self, cmd, **kwargs):
            seen_envs.append(dict(kwargs.get("env") or {}))
            self.stdout = io.StringIO("Installed\n")

        def wait(self):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(optional.subprocess, "Popen", FakeProc)
    assert optional.install("layout")["ok"] is True
    assert seen_envs[-1]["UV_NATIVE_TLS"] == "true"

    monkeypatch.setenv("UV_NATIVE_TLS", "false")
    assert optional.install("layout")["ok"] is True
    assert seen_envs[-1]["UV_NATIVE_TLS"] == "false"


def test_enable_system_certs_behavior(monkeypatch):
    import sys
    import types
    import litloop

    calls = []
    fake_ts = types.SimpleNamespace(inject_into_ssl=lambda: calls.append(True))
    monkeypatch.delenv("LITLOOP_SYSTEM_CERTS", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setitem(sys.modules, "truststore", fake_ts)

    assert litloop._enable_system_certs() is True
    assert calls == [True]

    monkeypatch.setenv("LITLOOP_SYSTEM_CERTS", "0")
    assert litloop._enable_system_certs() is False
    assert calls == [True]

    monkeypatch.delenv("LITLOOP_SYSTEM_CERTS", raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", "/tmp/ca-bundle-merged.pem")
    assert litloop._enable_system_certs() is False

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setitem(sys.modules, "truststore", None)
    assert litloop._enable_system_certs() is False
