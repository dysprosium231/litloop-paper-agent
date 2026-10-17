"""Zotero integration via PyZotero API."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .log import get_logger

log = get_logger()


def _get_zotero_config(config: dict[str, Any]) -> tuple[str, str] | None:
    """Get Zotero API key and library info from config."""
    api_key = config.get("zotero_api_key", "")
    library_type = config.get("zotero_library_type", "user")  # "user" or "group"
    library_id = config.get("zotero_library_id", "")

    if not api_key:
        return None
    return api_key, f"{library_type}s/{library_id}" if library_id else ""


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


def _zotero_mcp_python(config: dict[str, Any]) -> Path | None:
    found = discover_zotero_mcp(config)
    return found["python"] if found else None


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


def _run_bridge(found: dict[str, Any], req: dict[str, Any], timeout: int) -> dict[str, Any]:
    """Run zotero_bridge.py with zotero-mcp's interpreter: one JSON request in, one result out."""
    import subprocess

    python = found["python"]
    env_script = found.get("env_script") or ""
    req = {**req, "env_script": str(Path(env_script).expanduser()) if env_script else ""}
    bridge = Path(__file__).with_name("zotero_bridge.py")
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


def push_to_zotero(
    doi: str,
    pdf_path: Path | None,
    config: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    collections: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Push a paper and its PDF to Zotero.

    Uses zotero-mcp when ``zotero_mcp_python`` is set; otherwise the Zotero
    web API with ``zotero_api_key``. Returns dict with success status and
    zotero_key if successful.
    """
    if _zotero_mcp_python(config) is not None:
        return push_via_zotero_mcp(doi, pdf_path, config, collections, tags)

    import requests

    creds = _get_zotero_config(config)
    if not creds:
        return {"success": False, "error": "Zotero API key not configured"}

    api_key, library_path = creds
    if not library_path:
        return {"success": False, "error": "Zotero library_id not configured"}

    base_url = f"https://api.zotero.org/{library_path}"
    headers = {
        "Zotero-API-Key": api_key,
        "Content-Type": "application/json",
    }

    # Build item from metadata or DOI
    if metadata:
        item_data = _metadata_to_zotero_item(metadata, doi)
    else:
        # Use DOI to create a minimal item
        item_data = {
            "itemType": "journalArticle",
            "DOI": doi,
            "url": f"https://doi.org/{doi}",
        }

    # Create item
    try:
        resp = requests.post(
            f"{base_url}/items",
            headers=headers,
            json=[item_data],
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            return {"success": False, "error": f"Zotero API error: {resp.status_code}"}

        result = resp.json()
        if not (result.get("success") or result.get("successful")):
            return {"success": False, "error": "No successful items"}

        zotero_key = _created_object_key(result)
        if not zotero_key:
            return {"success": False, "error": "No key returned"}

        log.info(f"Zotero: created item {zotero_key}")

        # Upload PDF attachment if available
        pdf_attached = False
        if pdf_path and pdf_path.exists():
            pdf_attached = _upload_attachment(base_url, headers, zotero_key, pdf_path)

        return {"success": True, "zotero_key": zotero_key, "pdf_attached": pdf_attached,
                "via": "web-api"}

    except Exception as e:
        return {"success": False, "error": str(e)}


def _metadata_to_zotero_item(msg: dict[str, Any], doi: str) -> dict[str, Any]:
    """Convert Crossref metadata to Zotero item format."""
    item: dict[str, Any] = {
        "itemType": "journalArticle",
        "DOI": doi,
        "url": f"https://doi.org/{doi}",
    }

    # Title
    titles = msg.get("title", [])
    if titles:
        item["title"] = titles[0] if isinstance(titles, list) else titles

    # Authors
    authors = msg.get("author", [])
    if authors:
        creators = []
        for a in authors:
            creators.append({
                "creatorType": "author",
                "firstName": a.get("given", ""),
                "lastName": a.get("family", ""),
            })
        item["creators"] = creators

    # Journal
    container = msg.get("container-title", [])
    if container:
        item["publicationTitle"] = container[0]

    # Year
    parts = msg.get("published-print", msg.get("published-online", msg.get("created", {})))
    date_parts = parts.get("date-parts", [[]]) if isinstance(parts, dict) else [[]]
    if date_parts and date_parts[0]:
        item["date"] = str(date_parts[0][0])

    # Volume, Issue, Pages
    if msg.get("volume"):
        item["volume"] = str(msg["volume"])
    if msg.get("issue"):
        item["issue"] = str(msg["issue"])
    if msg.get("page"):
        item["pages"] = msg["page"]

    # Abstract
    if msg.get("abstract"):
        import re
        item["abstractNote"] = re.sub(r"<[^>]+>", "", msg["abstract"]).strip()

    return item


def _upload_attachment(
    base_url: str,
    headers: dict[str, str],
    parent_key: str,
    pdf_path: Path,
) -> bool:
    """Upload a PDF as an attachment to a Zotero item."""
    import requests

    try:
        # Create attachment item
        attachment_data = {
            "itemType": "attachment",
            "linkMode": "imported",
            "contentType": "application/pdf",
            "filename": pdf_path.name,
            "parentItem": parent_key,
        }

        resp = requests.post(
            f"{base_url}/items",
            headers=headers,
            json=[attachment_data],
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            log.warning(f"Zotero: attachment item creation failed: {resp.status_code}")
            return False

        result = resp.json()
        att_key = _created_object_key(result)
        if not att_key:
            return False

        # Upload the file
        upload_headers = {
            "Zotero-API-Key": headers["Zotero-API-Key"],
            "Content-Type": "application/octet-stream",
            "If-None-Match": "*",
        }
        with pdf_path.open("rb") as f:
            resp = requests.post(
                f"{base_url}/items/{att_key}/file",
                headers=upload_headers,
                data=f,
                timeout=120,
            )

        if resp.status_code in (200, 201, 204):
            log.info(f"Zotero: uploaded PDF {pdf_path.name}")
            return True
        else:
            log.warning(f"Zotero: file upload failed: {resp.status_code}")
            return False

    except Exception as e:
        log.warning(f"Zotero: attachment error: {e}")
        return False


def _created_object_key(result: dict[str, Any]) -> str | None:
    """Extract the Zotero object key from a write response."""
    successful = result.get("successful") or result.get("success") or {}
    if not successful:
        return None

    saved_object = next(iter(successful.values()))
    if isinstance(saved_object, str):
        return saved_object
    if isinstance(saved_object, dict):
        key = saved_object.get("key")
        if isinstance(key, str):
            return key
        data = saved_object.get("data", {})
        if isinstance(data, dict) and isinstance(data.get("key"), str):
            return data["key"]

    return None
