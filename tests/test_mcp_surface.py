"""MCP surface contract: the tool diet (45 -> 19) must stay lean and dispatchable."""

from __future__ import annotations

import asyncio
import json

from litloop.server import (
    mcp_app,
    litloop_config,
    litloop_find,
    litloop_login,
)

EXPECTED = {
    "litloop_batch_download",
    "litloop_cache_clear",
    "litloop_cancel",
    "litloop_channel_status",
    "litloop_citation",
    "litloop_config",
    "litloop_diagnostics",
    "litloop_download",
    "litloop_elsevier_setup",
    "litloop_deep_search",
    "litloop_find",
    "litloop_glossary",
    "litloop_login",
    "litloop_parse_list",
    "litloop_prepare_queue",
    "litloop_schools",
    "litloop_search",
    "litloop_screening",
    "litloop_springer_setup",
    "litloop_tor",
    "litloop_zotero_push",
}


def test_tool_surface_matches_expected():
    tools = asyncio.run(mcp_app.list_tools())
    assert {t.name for t in tools} == EXPECTED
    assert len(tools) == 21


def test_tool_descriptions_are_diet_sized():
    tools = asyncio.run(mcp_app.list_tools())
    for t in tools:
        assert len(t.description or "") <= 200, (
            f"{t.name}: description grew to {len(t.description)} chars — keep it short, details belong in skills"
        )


def test_tool_docstrings_fit_before_dedent():
    """Python < 3.13 keeps docstring indentation, so the raw text is what
    clients see there; measure it so a 3.13 run catches overruns too."""
    import ast
    import inspect

    import litloop.server as server

    tree = ast.parse(inspect.getsource(server))
    names = {t.name for t in asyncio.run(mcp_app.list_tools())}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            raw = ast.get_docstring(node, clean=False) or ""
            assert len(raw) <= 200, f"{node.name}: {len(raw)} chars before dedent"


def test_login_dispatch_unknown_kind_lists_allowed():
    out = json.loads(litloop_login(kind="nope"))
    assert "error" in out and set(out["allowed"]) == {
        "publisher", "webvpn", "carsi", "ezproxy", "custom", "cookie_import",
    }


def test_find_dispatch_unknown_action_lists_allowed():
    out = json.loads(litloop_find(action="nope"))
    assert "error" in out and set(out["allowed"]) == {"plan", "estimate", "smoke", "calibrate"}


def test_config_read_mode_returns_masked_dict():
    out = json.loads(litloop_config())
    assert isinstance(out, dict) and out  # masked full config
