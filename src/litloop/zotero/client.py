"""Zotero integration through zotero-mcp (github.com/54yyyu/zotero-mcp).

Everything goes through zotero-mcp's own code, run in its interpreter by
zotero_bridge.py: with local writes authorized it writes to the Zotero app on
this computer and Zotero syncs. LitLoop has no Zotero web-API code of its own.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..log import get_logger

log = get_logger()


_PY_NAMES = ("python.exe", "python") if os.name == "nt" else ("python3", "python")
_VENV_BIN = "Scripts" if os.name == "nt" else "bin"
_DISCOVERED: dict[str, Any] | None = None


def _venv_python(bin_dir: Path) -> Path | None:
    for name in _PY_NAMES:
        p = bin_dir / name
        if p.is_file():
            return p
    return None


def _tool_venv_pythons() -> list[Path]:
    """Interpreters of zotero-mcp installed with `uv tool` or pipx."""
    import shutil
    import subprocess

    roots: list[Path] = []
    if shutil.which("uv"):
        try:
            out = subprocess.run(["uv", "tool", "dir"], capture_output=True,
                                 text=True, timeout=10).stdout.strip()
            if out:
                roots.append(Path(out))
        except (OSError, subprocess.SubprocessError):
            pass
    home = Path.home()
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "uv" / "tools")
    roots += [home / ".local" / "share" / "uv" / "tools",
              home / ".local" / "pipx" / "venvs", home / "pipx" / "venvs"]
    if os.environ.get("PIPX_HOME"):
        roots.append(Path(os.environ["PIPX_HOME"]) / "venvs")
    found = []
    for root in roots:
        py = _venv_python(root / "zotero-mcp-server" / _VENV_BIN)
        if py and py not in found:
            found.append(py)
    return found


def _resolve_server_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Map an MCP client's zotero server entry to the Python that runs it."""
    import shutil

    cmd = str(entry.get("command") or "")
    args = [str(a) for a in entry.get("args") or []]
    env = {k: str(v) for k, v in (entry.get("env") or {}).items()}
    base = Path(cmd).name.lower()
    exe = Path(cmd) if Path(cmd).is_absolute() else Path(shutil.which(cmd) or cmd)

    if base.startswith("python") or base == "py.exe":
        if not exe.is_file():
            return None
        # `python launcher.py`: the launcher sets env, as the server would see it.
        script = next((a for a in args if a.lower().endswith(".py")), "")
        return {"python": exe, "env": env, "env_script": script}
    if base.startswith("zotero-mcp"):
        py = _venv_python(exe.parent) if exe.is_file() else None
        if py:
            return {"python": py, "env": env, "env_script": ""}
    if base.startswith(("uv", "uvx", "pipx", "zotero-mcp")):
        for py in _tool_venv_pythons():
            return {"python": py, "env": env, "env_script": ""}
    return None


def _mcp_client_configs() -> list[Path]:
    home = Path.home()
    paths = [home / ".claude.json", Path.cwd() / ".mcp.json"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    paths += [home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
              home / ".config" / "Claude" / "claude_desktop_config.json"]
    return [p for p in paths if p.is_file()]


def _zotero_server_entries(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = [cfg.get("mcpServers") or {}]
    for proj in (cfg.get("projects") or {}).values():
        if isinstance(proj, dict):
            blocks.append(proj.get("mcpServers") or {})
    out = []
    for block in blocks:
        for name, entry in block.items():
            if not isinstance(entry, dict):
                continue
            hay = " ".join([name, str(entry.get("command", ""))]
                           + [str(a) for a in entry.get("args") or []]).lower()
            if "zotero" in hay:
                out.append(entry)
    return out


def discover_zotero_mcp(config: dict[str, Any]) -> dict[str, Any] | None:
    """Find the Python that has zotero-mcp, plus the env its server runs with.

    Order: explicit ``zotero_mcp_python``; zotero-mcp installed alongside
    litloop; the zotero server registered in Claude Code / Claude Desktop;
    `zotero-mcp` on PATH; uv-tool / pipx installs. ``zotero_mcp_python`` set to
    "off" disables the route.
    """
    global _DISCOVERED
    explicit = str(config.get("zotero_mcp_python") or "").strip()
    if explicit.lower() == "off":
        return None
    if explicit and explicit.lower() != "auto":
        path = Path(explicit).expanduser()
        if not path.is_file():
            return None
        return {"python": path, "env": {},
                "env_script": str(config.get("zotero_mcp_env_script") or "").strip()}
    if _DISCOVERED is not None:
        return _DISCOVERED or None

    import importlib.util
    import shutil
    import sys

    found: dict[str, Any] = {}
    if importlib.util.find_spec("zotero_mcp") is not None:
        found = {"python": Path(sys.executable), "env": {}, "env_script": ""}
    if not found:
        for cfg_path in _mcp_client_configs():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for entry in _zotero_server_entries(cfg):
                hit = _resolve_server_entry(entry)
                if hit:
                    found = hit
                    break
            if found:
                break
    if not found and shutil.which("zotero-mcp"):
        hit = _resolve_server_entry({"command": "zotero-mcp"})
        found = hit or {}
    if not found:
        pys = _tool_venv_pythons()
        if pys:
            found = {"python": pys[0], "env": {}, "env_script": ""}
    if found and not found.get("env_script"):
        found["env_script"] = str(config.get("zotero_mcp_env_script") or "").strip()
    _DISCOVERED = found
    if found:
        log.info(f"Zotero: using zotero-mcp at {found['python']}")
    return found or None


def push_via_zotero_mcp(
    identifier: str,
    pdf_path: Path | None,
    config: dict[str, Any],
    collections: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """File the paper with zotero-mcp's own add/attach code (zotero_bridge.py).

    Runs in zotero-mcp's interpreter, so the item gets CrossRef metadata,
    duplicates are reused, and the PDF goes to WebDAV when zotero-mcp is
    configured for it.
    """
    found = discover_zotero_mcp(config)
    if not found:
        return {"success": False, "error": "zotero-mcp not found; install it "
                "(pip install zotero-mcp-server) or set zotero_mcp_python"}
    req = {
        "identifier": identifier,
        "file": str(pdf_path.resolve()) if pdf_path and pdf_path.is_file() else "",
        "collections": collections or [],
        "tags": tags or [],
    }
    # WebDAV uploads of large PDFs are allowed up to 600 s by zotero-mcp.
    result = _run_bridge(found, req, int(config.get("zotero_push_timeout", 900)))
    if result.get("success"):
        log.info(f"Zotero (zotero-mcp): item {result.get('zotero_key')}, "
                 f"pdf_attached={result.get('pdf_attached')}")
    return result


def zotero_collections(config: dict[str, Any]) -> dict[str, Any]:
    """Every collection of the library as {key, name, parent, path} (read only)."""
    found = discover_zotero_mcp(config)
    if not found:
        return {"success": False, "error": "zotero-mcp not found"}
    return _run_bridge(found, {"op": "collections"}, 120)


def ensure_collection(path: str, config: dict[str, Any]) -> dict[str, Any]:
    """The key of the collection at path ('LitLoop/My topic'), creating missing levels."""
    found = discover_zotero_mcp(config)
    if not found:
        return {"success": False, "error": "zotero-mcp not found"}
    return _run_bridge(found, {"op": "ensure_collection", "path": path}, 120)


def collection_dois(collection: str, config: dict[str, Any]) -> dict[str, Any]:
    """DOIs of the items in a Zotero collection, given by '/'-path or key (read only).

    Returns {"success", "key", "path", "dois", "without_doi"}; items without a DOI
    (books, theses, web pages) are counted, not returned.
    """
    found = discover_zotero_mcp(config)
    if not found:
        return {"success": False, "error": "zotero-mcp not found"}
    cols = _run_bridge(found, {"op": "collections"}, 120)
    if not cols.get("success"):
        return cols
    want = collection.strip().strip("/")
    hit = next((c for c in cols["collections"] if c["key"] == want or c["path"] == want), None)
    if hit is None:
        names = [c for c in cols["collections"] if c["name"] == want]
        hit = names[0] if len(names) == 1 else None
    if hit is None:
        return {"success": False, "error": f"no Zotero collection {collection!r} (give its full path, e.g. A/B)"}
    r = _run_bridge(found, {"op": "collection_items", "key": hit["key"]}, 120)
    if not r.get("success"):
        return r
    dois = list(dict.fromkeys(i["doi"] for i in r["items"] if i.get("doi")))
    return {"success": True, "key": hit["key"], "path": hit["path"], "dois": dois,
            "without_doi": sum(1 for i in r["items"] if not i.get("doi"))}


def check_collection(key: str, config: dict[str, Any]) -> dict[str, Any]:
    """Each item of the collection with its PDF state: ok, missing or empty (read only)."""
    found = discover_zotero_mcp(config)
    if not found:
        return {"success": False, "error": "zotero-mcp not found"}
    return _run_bridge(found, {"op": "check_collection", "key": key}, 600)


def repair_attachment(item_key: str, pdf: Path | None, trash: list[str], config: dict[str, Any]) -> dict[str, Any]:
    """Move the item's empty attachments to the Zotero Trash, then attach pdf (when given)."""
    found = discover_zotero_mcp(config)
    if not found:
        return {"success": False, "error": "zotero-mcp not found"}
    req = {"op": "repair", "item_key": item_key, "trash": trash,
           "file": str(pdf.resolve()) if pdf and pdf.is_file() else ""}
    return _run_bridge(found, req, int(config.get("zotero_push_timeout", 900)))


def write_mode(config: dict[str, Any]) -> dict[str, Any]:
    """How zotero-mcp writes: local (recommended), web, hybrid (local reads, web writes) or none."""
    found = discover_zotero_mcp(config)
    if not found:
        return {"success": False, "error": "zotero-mcp not found"}
    return _run_bridge(found, {"op": "write_mode"}, 60)


def authorize_local_writes(config: dict[str, Any]) -> dict[str, Any]:
    """Ask Zotero for local-write permission; Zotero shows a dialog and this waits for the answer."""
    found = discover_zotero_mcp(config)
    if not found:
        return {"success": False, "error": "zotero-mcp not found"}
    return _run_bridge(found, {"op": "authorize"}, 120)


def _run_bridge(found: dict[str, Any], req: dict[str, Any], timeout: int) -> dict[str, Any]:
    """Run zotero_bridge.py with zotero-mcp's interpreter: one JSON request in, one result out."""
    import subprocess

    python = found["python"]
    env_script = found.get("env_script") or ""
    req = {**req, "env_script": str(Path(env_script).expanduser()) if env_script else ""}
    bridge = Path(__file__).with_name("bridge.py")
    # The MCP client's env block for the zotero server (API key, WebDAV...).
    env = dict(os.environ, **found.get("env", {}), PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        proc = subprocess.run(
            [str(python), str(bridge)],
            input=json.dumps(req, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "zotero-mcp bridge timed out"}
    except OSError as e:
        return {"success": False, "error": f"cannot start zotero-mcp python: {e}"}

    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    try:
        result = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        result = None
    if not isinstance(result, dict):
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        return {"success": False, "error": f"zotero-mcp bridge failed (exit {proc.returncode}): {tail}"}
    result["via"] = "zotero-mcp"
    return result
