"""Tests for zotero_bridge helper functions and run() dispatch operations."""

from __future__ import annotations

import sys
import types

from litloop.zotero import bridge as zotero_bridge


def test_plain_strips_tags_and_unescapes_entities():
    # Strips tags
    assert zotero_bridge._plain("TiO<sub>2</sub> <i>nanoparticles</i>") == "TiO2 nanoparticles"
    # Unescapes &amp; and other HTML entities
    assert zotero_bridge._plain("Black &amp; White") == "Black & White"
    assert zotero_bridge._plain("<b>Rock</b> &amp; <i>Roll</i>") == "Rock & Roll"
    assert zotero_bridge._plain("  Spaces and &gt;  ") == "Spaces and >"
    # None and empty
    assert zotero_bridge._plain(None) == ""
    assert zotero_bridge._plain("") == ""


def test_doi_of_extracts_doi_field_or_extra():
    # DOI field present (lowercased)
    assert zotero_bridge._doi_of({"DOI": "10.1000/182"}) == "10.1000/182"
    assert zotero_bridge._doi_of({"DOI": "10.1000/ABC.DEF"}) == "10.1000/abc.def"
    assert zotero_bridge._doi_of({"DOI": "  10.1000/SPACES  "}) == "10.1000/spaces"

    # DOI empty or missing, extracted from extra
    extra_text = "Some notes\nDOI: 10.1000/from-extra\nPMID: 12345"
    assert zotero_bridge._doi_of({"DOI": "", "extra": extra_text}) == "10.1000/from-extra"
    assert zotero_bridge._doi_of({"extra": "doi: 10.1000/lower-extra"}) == "10.1000/lower-extra"

    # Neither present
    assert zotero_bridge._doi_of({}) == ""
    assert zotero_bridge._doi_of({"extra": "No doi here"}) == ""


def test_check_collection_classifies_items_and_skips_children(monkeypatch):
    rows = [
        # Regular item with stored attachment that has file
        {"key": "ITEM_OK", "title": "Paper <sub>One</sub>", "DOI": "10.1/one",
         "date": "2023-05-01", "publicationTitle": "Nature &amp; Science", "itemType": "journalArticle"},
        # Regular item with linked_file attachment
        {"key": "ITEM_LINKED", "title": "Paper Two", "DOI": "10.1/two",
         "date": "2022", "publicationTitle": "IEEE", "itemType": "journalArticle"},
        # Regular item with missing PDF
        {"key": "ITEM_MISSING", "title": "Paper Three", "DOI": "10.1/three",
         "date": "2021", "publicationTitle": "Science", "itemType": "journalArticle"},
        # Regular item with empty stored attachment
        {"key": "ITEM_EMPTY", "title": "Paper Four", "DOI": "10.1/four",
         "date": "2020", "publicationTitle": "Cell", "itemType": "journalArticle"},
        # Child attachments
        {"key": "ATT1", "parentItem": "ITEM_OK", "contentType": "application/pdf",
         "itemType": "attachment", "linkMode": "imported_file"},
        {"key": "ATT2", "parentItem": "ITEM_LINKED", "contentType": "application/pdf",
         "itemType": "attachment", "linkMode": "linked_file"},
        {"key": "ATT4", "parentItem": "ITEM_EMPTY", "contentType": "application/pdf",
         "itemType": "attachment", "linkMode": "imported_file"},
        # Child non-PDF attachment (should not count as PDF)
        {"key": "ATT_HTML", "parentItem": "ITEM_MISSING", "contentType": "text/html",
         "itemType": "attachment"},
        # Note, annotation, child row with parentItem - must be skipped
        {"key": "NOTE1", "itemType": "note", "note": "my note"},
        {"key": "ANNO1", "itemType": "annotation"},
        {"key": "CHILD_ROW", "itemType": "journalArticle", "parentItem": "ITEM_OK"},
    ]

    client = types.ModuleType("zotero_mcp.client")
    mock_zot = types.SimpleNamespace(
        collection_items=lambda key: key,
        everything=lambda call: [{"data": r} for r in rows],
    )
    client.get_zotero_client = lambda: mock_zot
    monkeypatch.setitem(sys.modules, "zotero_mcp", types.ModuleType("zotero_mcp"))
    monkeypatch.setitem(sys.modules, "zotero_mcp.client", client)

    # Monkeypatch _has_stored_file so ATT1 is True, ATT4 is False
    monkeypatch.setattr(zotero_bridge, "_has_stored_file", lambda key: key == "ATT1")

    res = zotero_bridge._check_collection("COL12345")
    assert res["success"] is True
    items = {it["key"]: it for it in res["items"]}
    assert set(items.keys()) == {"ITEM_OK", "ITEM_LINKED", "ITEM_MISSING", "ITEM_EMPTY"}

    # ITEM_OK: ok, empty_attachments=[]
    assert items["ITEM_OK"]["state"] == "ok"
    assert items["ITEM_OK"]["empty_attachments"] == []
    assert items["ITEM_OK"]["title"] == "Paper One"
    assert items["ITEM_OK"]["journal"] == "Nature & Science"
    assert items["ITEM_OK"]["year"] == 2023

    # ITEM_LINKED: ok, empty_attachments=[]
    assert items["ITEM_LINKED"]["state"] == "ok"
    assert items["ITEM_LINKED"]["empty_attachments"] == []
    assert items["ITEM_LINKED"]["year"] == 2022

    # ITEM_MISSING: missing, empty_attachments=[]
    assert items["ITEM_MISSING"]["state"] == "missing"
    assert items["ITEM_MISSING"]["empty_attachments"] == []

    # ITEM_EMPTY: empty, empty_attachments=["ATT4"]
    assert items["ITEM_EMPTY"]["state"] == "empty"
    assert items["ITEM_EMPTY"]["empty_attachments"] == ["ATT4"]


def test_repair_trashes_and_attaches(monkeypatch):
    deleted = []
    attached = []

    def mock_delete(item_key, ctx):
        deleted.append(item_key)
        return "Item deleted"

    def mock_attach(item_key, file_path, ctx):
        attached.append((item_key, file_path))
        return f"File attached to `{item_key}`: doc.pdf (key `ATTNEW01`)"

    tools = types.ModuleType("zotero_mcp.tools")
    tools.write = types.SimpleNamespace(delete_item=mock_delete, attach_file=mock_attach)
    cli = types.ModuleType("zotero_mcp.cli_standalone")
    cli.CLIContext = lambda: None
    monkeypatch.setitem(sys.modules, "zotero_mcp", types.ModuleType("zotero_mcp"))
    monkeypatch.setitem(sys.modules, "zotero_mcp.tools", tools)
    monkeypatch.setitem(sys.modules, "zotero_mcp.cli_standalone", cli)
    monkeypatch.setattr(zotero_bridge, "_has_stored_file", lambda key: key == "ATTNEW01")

    # Happy path: trash + attach
    res = zotero_bridge._repair({
        "item_key": "ITEM1234",
        "trash": ["OLDATT01", "OLDATT02"],
        "file": "C:/path/doc.pdf",
    })
    assert res == {"success": True, "trashed": ["OLDATT01", "OLDATT02"], "attached": True}
    assert deleted == ["OLDATT01", "OLDATT02"]
    assert attached == [("ITEM1234", "C:/path/doc.pdf")]

    # Delete error stops before further deletes and attach
    deleted.clear()
    attached.clear()

    def mock_delete_err(item_key, ctx):
        if item_key == "OLDATT02":
            return "Error: item locked"
        deleted.append(item_key)
        return "Item deleted"

    tools.write.delete_item = mock_delete_err

    res_err = zotero_bridge._repair({
        "item_key": "ITEM1234",
        "trash": ["OLDATT01", "OLDATT02", "OLDATT03"],
        "file": "C:/path/doc.pdf",
    })
    assert res_err == {"success": False, "error": "Error: item locked", "trashed": ["OLDATT01"]}
    assert deleted == ["OLDATT01"]
    assert attached == []

    # Attach false / no file: only trashes
    deleted.clear()
    tools.write.delete_item = mock_delete
    res_no_file = zotero_bridge._repair({
        "item_key": "ITEM1234",
        "trash": ["OLDATT01"],
        "file": "",
    })
    assert res_no_file == {"success": True, "trashed": ["OLDATT01"], "attached": False}
    assert deleted == ["OLDATT01"]
    assert attached == []

    # Attach fails (missing stored file)
    deleted.clear()
    monkeypatch.setattr(zotero_bridge, "_has_stored_file", lambda key: False)
    res_no_store = zotero_bridge._repair({
        "item_key": "ITEM1234",
        "trash": [],
        "file": "C:/path/doc.pdf",
    })
    assert res_no_store["success"] is False
    assert res_no_store["attached"] is False


def test_run_dispatch_ops(monkeypatch):
    client = types.ModuleType("zotero_mcp.client")
    client.get_write_capabilities = lambda probe=True: {"mode": "local", "server_supports_local_writes": True}
    local_auth = types.ModuleType("zotero_mcp.tools.local_auth")
    local_auth.authorize_local_writes = lambda ctx: "Authorization granted"
    cli = types.ModuleType("zotero_mcp.cli_standalone")
    cli.CLIContext = lambda: None
    tools = types.ModuleType("zotero_mcp.tools")
    tools.local_auth = local_auth

    monkeypatch.setitem(sys.modules, "zotero_mcp", types.ModuleType("zotero_mcp"))
    monkeypatch.setitem(sys.modules, "zotero_mcp.client", client)
    monkeypatch.setitem(sys.modules, "zotero_mcp.tools", tools)
    monkeypatch.setitem(sys.modules, "zotero_mcp.tools.local_auth", local_auth)
    monkeypatch.setitem(sys.modules, "zotero_mcp.cli_standalone", cli)

    # op: write_mode
    res_wm = zotero_bridge.run({"op": "write_mode"})
    assert res_wm == {"success": True, "mode": "local", "local_supported": True}

    # op: authorize (success)
    res_auth = zotero_bridge.run({"op": "authorize"})
    assert res_auth == {"success": True, "mode": "local", "message": "Authorization granted"}

    # op: authorize (mode != local)
    client.get_write_capabilities = lambda probe=True: {"mode": "web", "server_supports_local_writes": False}
    res_auth_fail = zotero_bridge.run({"op": "authorize"})
    assert res_auth_fail == {"success": False, "mode": "web", "message": "Authorization granted"}

    # op: check_collection
    monkeypatch.setattr(zotero_bridge, "_check_collection", lambda key: {"success": True, "items": [{"key": key}]})
    res_check = zotero_bridge.run({"op": "check_collection", "key": "COL12345"})
    assert res_check == {"success": True, "items": [{"key": "COL12345"}]}

    # op: repair
    monkeypatch.setattr(zotero_bridge, "_repair", lambda req: {"success": True, "repaired": req["item_key"]})
    res_repair = zotero_bridge.run({"op": "repair", "item_key": "ITEM1234"})
    assert res_repair == {"success": True, "repaired": "ITEM1234"}

    # op: unknown op
    res_unknown = zotero_bridge.run({"op": "unknown_operation"})
    assert res_unknown == {"success": False, "error": "unknown op unknown_operation"}
