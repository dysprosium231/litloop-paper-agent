from pathlib import Path

from litloop.zotero import client as zotero


def _fake_zotero_mcp(tmp_path, monkeypatch, reply_line):
    """A fake zotero-mcp python: records the request, prints reply_line."""
    import sys
    seen = tmp_path / "req.json"
    stub = tmp_path / "stub.py"
    stub.write_text(
        "import sys\n"
        f"open({str(seen)!r}, 'w', encoding='utf-8').write(sys.stdin.read())\n"
        "print('progress noise')\n"
        f"print({reply_line!r})\n",
        encoding="utf-8",
    )
    real_run = __import__("subprocess").run

    def fake_run(cmd, **kw):
        # cmd = [python, zotero_bridge.py]; swap the bridge for the stub.
        return real_run([sys.executable, str(stub)], **kw)

    monkeypatch.setattr("subprocess.run", fake_run)
    return seen


def test_push_uses_zotero_mcp_when_configured(tmp_path, monkeypatch):
    import json, sys
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    seen = _fake_zotero_mcp(tmp_path, monkeypatch,
                            '{"success": true, "zotero_key": "ABCD1234", "pdf_attached": true}')

    result = zotero.push_via_zotero_mcp(
        "10.1234/x", pdf, {"zotero_mcp_python": sys.executable},
        collections=["Ceramics"], tags=["litloop"],
    )

    assert result["success"] and result["zotero_key"] == "ABCD1234"
    assert result["via"] == "zotero-mcp"
    req = json.loads(seen.read_text(encoding="utf-8"))
    assert req["identifier"] == "10.1234/x"
    assert Path(req["file"]).is_absolute()
    assert req["collections"] == ["Ceramics"] and req["tags"] == ["litloop"]


def test_zotero_mcp_bridge_garbage_output_is_an_error(tmp_path, monkeypatch):
    import sys
    _fake_zotero_mcp(tmp_path, monkeypatch, "not json")
    result = zotero.push_via_zotero_mcp("10.1234/x", None, {"zotero_mcp_python": sys.executable})
    assert result["success"] is False
    assert "bridge failed" in result["error"]


def test_zotero_bridge_parses_item_and_attachment_keys(monkeypatch):
    import types, sys
    from litloop.zotero import bridge as zotero_bridge

    calls = {}
    write = types.SimpleNamespace(
        add_item=lambda **kw: calls.setdefault("add", kw) and "Added\n\nItem key: `QWER5678`\n",
        attach_file=lambda **kw: calls.setdefault("att", kw) and "File attached to `QWER5678`: a.pdf (key `ZXCV0987`) via WebDAV",
    )
    tools = types.ModuleType("zotero_mcp.tools")
    tools.write = write
    cli = types.ModuleType("zotero_mcp.cli_standalone")
    cli.CLIContext = lambda: None
    monkeypatch.setitem(sys.modules, "zotero_mcp", types.ModuleType("zotero_mcp"))
    monkeypatch.setitem(sys.modules, "zotero_mcp.tools", tools)
    monkeypatch.setitem(sys.modules, "zotero_mcp.cli_standalone", cli)

    out = zotero_bridge.run({"identifier": "2301.00001", "file": "C:/a.pdf"})

    assert out == {"success": True, "zotero_key": "QWER5678", "reused": False,
                   "pdf_attached": True, "attach_message": "File attached to `QWER5678`: a.pdf (key `ZXCV0987`) via WebDAV",
                   "attachment_key": "ZXCV0987"}
    assert calls["add"]["source"] == "https://arxiv.org/abs/2301.00001"
    assert calls["add"]["attach_mode"] == "none" and calls["add"]["if_exists"] == "file"
    assert calls["att"]["item_key"] == "QWER5678"


def test_zotero_bridge_flags_attachment_without_file(monkeypatch):
    """zotero-mcp skips upload when the filename exists; an empty record is not a PDF."""
    import types, sys
    from litloop.zotero import bridge as zotero_bridge

    write = types.SimpleNamespace(
        add_item=lambda **kw: "Already in library:\n\nItem key: `QWER5678`\n",
        attach_file=lambda **kw: "Attachment already present on `QWER5678`: a.pdf (key `EMPTY123`) — not re-uploaded.",
    )
    tools = types.ModuleType("zotero_mcp.tools")
    tools.write = write
    cli = types.ModuleType("zotero_mcp.cli_standalone")
    cli.CLIContext = lambda: None
    client = types.ModuleType("zotero_mcp.client")
    client.get_zotero_client = lambda: types.SimpleNamespace(
        item=lambda key: {"data": {"key": key, "filename": "a.pdf", "md5": None}})
    monkeypatch.setitem(sys.modules, "zotero_mcp", types.ModuleType("zotero_mcp"))
    monkeypatch.setitem(sys.modules, "zotero_mcp.tools", tools)
    monkeypatch.setitem(sys.modules, "zotero_mcp.cli_standalone", cli)
    monkeypatch.setitem(sys.modules, "zotero_mcp.client", client)
    webdav = types.ModuleType("zotero_mcp.webdav")
    webdav.get_webdav_config = lambda: None
    monkeypatch.setitem(sys.modules, "zotero_mcp.webdav", webdav)

    out = zotero_bridge.run({"identifier": "10.1/x", "file": "C:/a.pdf"})

    assert out["pdf_attached"] is False
    assert "EMPTY123" in out["attach_message"] and "attachment_key" not in out


def test_zotero_bridge_accepts_file_stored_on_webdav(monkeypatch):
    """A WebDAV upload leaves no md5 on the item; the .prop sidecar proves the file."""
    import types, sys
    import requests
    from litloop.zotero import bridge as zotero_bridge

    client = types.ModuleType("zotero_mcp.client")
    client.get_zotero_client = lambda: types.SimpleNamespace(item=lambda key: {"data": {"md5": None}})
    webdav = types.ModuleType("zotero_mcp.webdav")
    webdav.get_webdav_config = lambda: ("https://dav.example/zotero/", "u", "p")
    monkeypatch.setitem(sys.modules, "zotero_mcp", types.ModuleType("zotero_mcp"))
    monkeypatch.setitem(sys.modules, "zotero_mcp.client", client)
    monkeypatch.setitem(sys.modules, "zotero_mcp.webdav", webdav)
    seen = {}

    def head(url, **kw):
        seen["url"] = url
        return types.SimpleNamespace(status_code=200 if "FULL" in url else 404)

    monkeypatch.setattr(requests, "head", head)
    assert zotero_bridge._has_stored_file("FULL1234")
    assert seen["url"] == "https://dav.example/zotero/FULL1234.prop"
    assert not zotero_bridge._has_stored_file("EMPTY123")


def _isolate_discovery(monkeypatch, tmp_path):
    import importlib.util
    monkeypatch.setattr(zotero, "_DISCOVERED", None)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("PIPX_HOME", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    real_find = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda n, *a: None if n == "zotero_mcp" else real_find(n, *a))


def _fake_venv(root):
    bin_dir = root / zotero._VENV_BIN
    bin_dir.mkdir(parents=True)
    py = bin_dir / zotero._PY_NAMES[0]
    py.write_bytes(b"")
    return bin_dir, py


def test_discovers_zotero_server_from_claude_code_config(tmp_path, monkeypatch):
    import json
    _isolate_discovery(monkeypatch, tmp_path)
    bin_dir, py = _fake_venv(tmp_path / "venv")
    exe = bin_dir / ("zotero-mcp.exe" if zotero.os.name == "nt" else "zotero-mcp")
    exe.write_bytes(b"")
    (tmp_path / ".claude.json").write_text(json.dumps({"projects": {"/p": {"mcpServers": {
        "zotero": {"command": str(exe), "args": ["serve"], "env": {"ZOTERO_API_KEY": "k"}}}}}}),
        encoding="utf-8")

    found = zotero.discover_zotero_mcp({})

    assert found["python"] == py
    assert found["env"] == {"ZOTERO_API_KEY": "k"}


def test_discovers_python_launcher_script_as_env_script(tmp_path, monkeypatch):
    import json
    _isolate_discovery(monkeypatch, tmp_path)
    _, py = _fake_venv(tmp_path / "venv")
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "refs": {"command": str(py), "args": [str(tmp_path / "run_zotero_mcp.py")]}}}),
        encoding="utf-8")

    found = zotero.discover_zotero_mcp({})

    assert found["python"] == py
    assert found["env_script"].endswith("run_zotero_mcp.py")


def test_discovers_uv_tool_install_and_off_disables(tmp_path, monkeypatch):
    _isolate_discovery(monkeypatch, tmp_path)
    _, py = _fake_venv(tmp_path / ".local" / "share" / "uv" / "tools" / "zotero-mcp-server")

    assert zotero.discover_zotero_mcp({"zotero_mcp_python": "off"}) is None
    assert zotero.discover_zotero_mcp({})["python"] == py


def test_no_zotero_mcp_anywhere_returns_error(tmp_path, monkeypatch):
    _isolate_discovery(monkeypatch, tmp_path)
    assert zotero.discover_zotero_mcp({}) is None
    result = zotero.push_via_zotero_mcp("10.1/x", None, {})
    assert result["success"] is False
    assert "zotero-mcp" in result["error"]


def test_zotero_bridge_resolves_localhost_to_ipv4(monkeypatch):
    import socket
    from litloop.zotero import bridge as zotero_bridge

    seen = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **kw: seen.append(host) or [])
    zotero_bridge._localhost_ipv4_only()
    socket.getaddrinfo("localhost", 23119)
    socket.getaddrinfo("api.zotero.org", 443)
    assert seen == ["127.0.0.1", "api.zotero.org"]
