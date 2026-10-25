"""Run inside the zotero-mcp Python (not litloop's) to file a paper.

litloop starts this file with zotero-mcp's interpreter and passes one JSON
request on stdin; it prints one JSON result line on stdout. It imports nothing
from litloop, so it works with only zotero-mcp installed.

The item is added by DOI (or arXiv URL) with attach_mode="none", so CrossRef
metadata is used, then the downloaded PDF is attached with zotero-mcp's own
attach_file, which uploads to WebDAV when ZOTERO_WEBDAV_* is set.

Request: {"op", "env_script", ...}
  op "push" (default): {"identifier", "file", "collections", "tags"}
  op "collections":    list every collection as {"key", "name", "parent", "path"}
  op "ensure_collection": {"path": "LitLoop/My topic"} -> key, creating missing levels
  op "collection_items": {"key"} -> regular items as {"key", "title", "doi", "year"} (no attachment check)
  op "check_collection": {"key"} -> every regular item with the state of its PDF:
                       ok, missing (no PDF attachment), empty (attachments hold no file)
  op "write_mode":     how zotero-mcp would write: local, web, hybrid or none
  op "authorize":      ask Zotero for local-write permission (a dialog in Zotero; waits for it)
  op "repair":         {"item_key", "trash": [attachment keys], "file"} -> moves the
                       empty attachments to the Trash, then attaches file (if given)
"""

from __future__ import annotations

import contextlib
import json
import re
import runpy
import socket
import sys

_KEY_RE = re.compile(r"Item key: `([A-Z0-9]{8})`")
# "File attached to `PARENT`: name.pdf (key `ATTKEY`) ..."
_ATT_KEY_RE = re.compile(r"\(key `([A-Z0-9]{8})`\)")


def _source_for(identifier: str) -> str:
    ident = identifier.strip()
    m = re.match(r"(?i)^(?:arxiv:)?(\d{4}\.\d{4,5}(?:v\d+)?)$", ident)
    if m:
        return f"https://arxiv.org/abs/{m.group(1)}"
    return ident


def _setup(req: dict) -> None:
    if req.get("env_script"):
        # The user's launcher sets ZOTERO_* env vars (decrypting secrets);
        # its serve() call is guarded by __main__, so this only sets env.
        runpy.run_path(req["env_script"], run_name="zotero_env")

    try:
        # Same config the server reads: ~/.config/zotero-mcp/config.json and
        # Claude Desktop's env block. Never overrides variables already set.
        from zotero_mcp.cli import setup_zotero_environment
        setup_zotero_environment()
    except Exception:
        pass


def _collections() -> list[dict]:
    """Every collection with its '/'-path, parents before children."""
    from zotero_mcp.client import get_zotero_client
    zot = get_zotero_client()
    rows = zot.everything(zot.collections())
    by_key = {c["data"]["key"]: c["data"] for c in rows}

    def path(key: str, seen=()) -> str:
        d = by_key[key]
        parent = d.get("parentCollection") or None
        if parent and parent in by_key and parent not in seen:
            return path(parent, seen + (key,)) + "/" + d["name"]
        return d["name"]

    out = [{"key": k, "name": d["name"], "parent": d.get("parentCollection") or None, "path": path(k)}
           for k, d in by_key.items()]
    return sorted(out, key=lambda c: c["path"].lower())


def _ensure_collection(path: str) -> dict:
    """Find the collection at path ('A/B/C'), creating the levels that are missing."""
    from zotero_mcp.cli_standalone import CLIContext
    from zotero_mcp.tools import write
    parts = [p.strip() for p in path.split("/") if p.strip()]
    if not parts:
        return {"success": False, "error": "empty collection path"}
    created, parent = [], None
    for i in range(len(parts)):
        want = "/".join(parts[: i + 1])
        found = next((c for c in _collections() if c["path"] == want), None)
        if found:
            parent = found["key"]
            continue
        out = write.create_collection(name=parts[i], parent_collection=parent, ctx=CLIContext())
        m = re.search(r"Collection key: `([A-Z0-9]{8})`", out or "")
        if not m:
            return {"success": False, "error": (out or "").strip()[:500]}
        parent = m.group(1)
        created.append(want)
    return {"success": True, "key": parent, "path": "/".join(parts), "created": created}


def _plain(text) -> str:
    """Zotero keeps rich-text titles (<sub>2</sub>, &amp;); show them as plain text."""
    import html
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def _doi_of(data: dict) -> str:
    doi = (data.get("DOI") or "").strip()
    if not doi:
        m = re.search(r"(?im)^DOI:\s*(\S+)", data.get("extra") or "")
        doi = m.group(1) if m else ""
    return doi.lower()


def _check_collection(key: str) -> dict:
    """The PDF state of every regular item in the collection (read only)."""
    from zotero_mcp.client import get_zotero_client
    zot = get_zotero_client()
    # One listing holds the items and their child attachments (children carry parentItem).
    rows = [r.get("data", {}) for r in zot.everything(zot.collection_items(key))]
    pdfs_of: dict[str, list] = {}
    for d in rows:
        if d.get("itemType") == "attachment" and d.get("parentItem") and d.get("contentType") == "application/pdf":
            pdfs_of.setdefault(d["parentItem"], []).append(d)
    out = []
    for d in rows:
        if d.get("itemType") in ("attachment", "note", "annotation") or d.get("parentItem"):
            continue
        pdfs = pdfs_of.get(d["key"], [])
        if not pdfs:
            state, empty = "missing", []
        else:
            linked = [a for a in pdfs if a.get("linkMode") == "linked_file"]
            stored = [a for a in pdfs if a.get("linkMode") != "linked_file"]
            empty = [a["key"] for a in stored if not _has_stored_file(a["key"])]
            state = "ok" if linked or len(empty) < len(stored) else "empty"
        year = re.search(r"\d{4}", d.get("date") or "")
        out.append({"key": d["key"], "title": _plain(d.get("title")), "doi": _doi_of(d),
                    "year": int(year.group(0)) if year else None,
                    "journal": _plain(d.get("publicationTitle")) or None, "state": state,
                    "empty_attachments": empty if state == "empty" else []})
    return {"success": True, "items": out}


def _repair(req: dict) -> dict:
    """Trash the empty attachments of one item, then attach the local PDF."""
    from zotero_mcp.cli_standalone import CLIContext
    from zotero_mcp.tools import write
    ctx = CLIContext()
    trashed = []
    for key in req.get("trash") or []:
        out = write.delete_item(item_key=key, ctx=ctx)
        if (out or "").lstrip().startswith("Error"):
            return {"success": False, "error": out.strip()[:500], "trashed": trashed}
        trashed.append(key)
    if not req.get("file"):
        return {"success": True, "trashed": trashed, "attached": False}
    att = write.attach_file(item_key=req["item_key"], file_path=req["file"], ctx=ctx)
    k = _ATT_KEY_RE.search(att)
    ok = not att.lstrip().startswith("Error") and "FAILED" not in att and bool(k) and _has_stored_file(k.group(1))
    return {"success": ok, "trashed": trashed, "attached": ok,
            **({} if ok else {"error": att.strip()[:500]})}


def run(req: dict) -> dict:
    _setup(req)
    op = req.get("op") or "push"
    if op == "collections":
        return {"success": True, "collections": _collections()}
    if op == "ensure_collection":
        return _ensure_collection(req.get("path") or "")
    if op == "write_mode":
        from zotero_mcp.client import get_write_capabilities
        caps = get_write_capabilities(probe=True)
        return {"success": True, "mode": caps.get("mode"), "local_supported": caps.get("server_supports_local_writes")}
    if op == "authorize":
        from zotero_mcp.cli_standalone import CLIContext
        from zotero_mcp.tools.local_auth import authorize_local_writes
        out = authorize_local_writes(ctx=CLIContext())
        from zotero_mcp.client import get_write_capabilities
        mode = get_write_capabilities(probe=True).get("mode")
        return {"success": mode == "local", "mode": mode, "message": (out or "").strip()[:500]}
    if op == "collection_items":
        from zotero_mcp.client import get_zotero_client
        zot = get_zotero_client()
        items = []
        for row in zot.everything(zot.collection_items_top(req["key"])):
            d = row.get("data", {})
            if d.get("itemType") in ("attachment", "note", "annotation"):
                continue
            year = re.search(r"\d{4}", d.get("date") or "")
            items.append({"key": d["key"], "title": _plain(d.get("title")), "doi": _doi_of(d),
                          "year": int(year.group(0)) if year else None})
        return {"success": True, "items": items}
    if op == "check_collection":
        return _check_collection(req["key"])
    if op == "repair":
        return _repair(req)
    if op != "push":
        return {"success": False, "error": f"unknown op {op}"}

    from zotero_mcp.cli_standalone import CLIContext
    from zotero_mcp.tools import write

    if not hasattr(write, "add_item") or not hasattr(write, "attach_file"):
        return {"success": False, "error": "zotero-mcp is too old; "
                "upgrade with: pip install -U zotero-mcp-server"}
    ctx = CLIContext()
    try:
        added = write.add_item(
            source=_source_for(req["identifier"]),
            collections=req.get("collections") or None,
            tags=req.get("tags") or None,
            attach_mode="none",
            if_exists="file",
            ctx=ctx,
        )
    except TypeError as e:  # older signature without if_exists
        return {"success": False, "error": f"zotero-mcp is too old ({e}); "
                "upgrade with: pip install -U zotero-mcp-server"}
    m = _KEY_RE.search(added)
    if not m:
        return {"success": False, "error": added.strip()[:500]}
    result = {
        "success": True,
        "zotero_key": m.group(1),
        "reused": "Already in library:" in added,
        "pdf_attached": False,
    }

    file_path = req.get("file")
    if file_path:
        att = write.attach_file(item_key=m.group(1), file_path=file_path, ctx=ctx)
        ok = not att.lstrip().startswith("Error") and "FAILED" not in att
        k = _ATT_KEY_RE.search(att)
        if ok and k and "already present" in att and not _has_stored_file(k.group(1)):
            # zotero-mcp dedupes by filename only; an upload that was cut off
            # leaves an attachment record with no file behind it.
            ok = False
            att = (f"Attachment `{k.group(1)}` exists but holds no file (an earlier upload "
                   "was interrupted). Move it to the trash and push again.")
        result["pdf_attached"] = ok
        result["attach_message"] = att.strip()[:500]
        if ok and k:
            result["attachment_key"] = k.group(1)
    return result


def _has_stored_file(attachment_key: str) -> bool:
    """Whether the attachment has file content behind it.

    Zotero records an md5 for files it stores itself. A file that went
    straight to WebDAV has no md5 on the item; there the ``<KEY>.prop``
    sidecar, written only after the zip upload completes, is the proof.
    """
    try:
        from zotero_mcp.client import get_zotero_client
        data = get_zotero_client().item(attachment_key).get("data", {})
    except Exception:
        return True  # cannot tell: keep zotero-mcp's verdict
    if data.get("md5"):
        return True
    try:
        import requests
        from zotero_mcp.webdav import get_webdav_config
        config = get_webdav_config()
        if not config:
            return False
        base, user, password = config
        r = requests.head(f"{base}{attachment_key}.prop", auth=(user, password), timeout=15)
    except Exception:
        return True
    return r.status_code != 404


def _localhost_ipv4_only() -> None:
    """Resolve ``localhost`` to 127.0.0.1 only.

    Zotero's local API listens on IPv4; on Windows ``localhost`` tries ::1
    first and every request then waits ~2 s for that to fail, which made a
    single push take 30-60 s.
    """
    orig = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        if host == "localhost":
            host = "127.0.0.1"
        return orig(host, *args, **kwargs)

    socket.getaddrinfo = getaddrinfo


def main() -> int:
    req = json.loads(sys.stdin.read())
    _localhost_ipv4_only()
    # zotero-mcp may print progress; keep stdout for the one result line.
    with contextlib.redirect_stdout(sys.stderr):
        try:
            result = run(req)
        except Exception as e:  # report, never a traceback on stdout
            result = {"success": False, "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
