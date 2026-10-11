"""Run inside the zotero-mcp Python (not scansci-pdf's) to file a paper.

scansci-pdf starts this file with zotero-mcp's interpreter and passes one JSON
request on stdin; it prints one JSON result line on stdout. It imports nothing
from scansci_pdf, so it works with only zotero-mcp installed.

The item is added by DOI (or arXiv URL) with attach_mode="none", so CrossRef
metadata is used, then the downloaded PDF is attached with zotero-mcp's own
attach_file, which uploads to WebDAV when ZOTERO_WEBDAV_* is set.

Request: {"identifier", "file", "collections", "tags", "env_script"}
"""

from __future__ import annotations

import contextlib
import json
import re
import runpy
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


def run(req: dict) -> dict:
    if req.get("env_script"):
        # The user's launcher sets ZOTERO_* env vars (decrypting secrets);
        # its serve() call is guarded by __main__, so this only sets env.
        runpy.run_path(req["env_script"], run_name="zotero_env")

    from zotero_mcp.cli_standalone import CLIContext
    from zotero_mcp.tools import write

    ctx = CLIContext()
    added = write.add_item(
        source=_source_for(req["identifier"]),
        collections=req.get("collections") or None,
        tags=req.get("tags") or None,
        attach_mode="none",
        if_exists="file",
        ctx=ctx,
    )
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
        result["pdf_attached"] = ok
        result["attach_message"] = att.strip()[:500]
        if ok:
            k = _ATT_KEY_RE.search(att)
            if k:
                result["attachment_key"] = k.group(1)
    return result


def main() -> int:
    req = json.loads(sys.stdin.read())
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
