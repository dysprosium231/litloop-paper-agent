"""The Elsevier API half of the publisher strategy core.

It lives in its own module since 2026-11-05; `_core.py` re-exports it, so both
the code that calls it and these checks go through `_core` as before.
"""

from __future__ import annotations

import builtins
import dis
import types

import pytest

from litloop.publishers.strategies import _core, _elsevier_api

REEXPORTED = [
    "_elsevier_header",
    "_elsevier_response_is_pdf",
    "_elsevier_response_is_xml",
    "_elsevier_response_text",
    "_elsevier_xml_local_name",
    "_elsevier_element_text",
    "_elsevier_looks_like_pdf_eid",
    "_elsevier_article_eid_to_main_pdf",
    "_elsevier_attachment_container",
    "_elsevier_attachment_metadata",
    "_elsevier_attachment_score",
    "_extract_elsevier_pdf_attachment_eids",
    "_elsevier_pdf_page_count",
    "_save_elsevier_pdf_content",
    "_try_elsevier_object_pdf_from_xml",
    "_try_elsevier_text_fallback",
    "try_elsevier_api",
    "_try_elsevier_api",
]


def _functions(code):
    for ins in dis.get_instructions(code):
        if isinstance(ins.argval, types.CodeType):
            yield ins.argval
            yield from _functions(ins.argval)


def _global_loads(code):
    """Every name read as a module global, including inside nested functions."""
    for ins in dis.get_instructions(code):
        if ins.opname == "LOAD_GLOBAL":
            yield ins.argval
        elif isinstance(ins.argval, types.CodeType):
            yield from _global_loads(ins.argval)


def _module_functions(module):
    for name, obj in vars(module).items():
        if isinstance(obj, types.FunctionType) and obj.__module__ == module.__name__:
            yield name, obj


def test_core_still_hands_out_the_same_objects():
    for name in REEXPORTED:
        assert getattr(_core, name) is getattr(_elsevier_api, name), name


def test_every_global_the_moved_code_reads_is_defined():
    """A moved function that forgets an import fails only on an error path."""
    missing = {}
    for name, fn in _module_functions(_elsevier_api):
        for used in _global_loads(fn.__code__):
            if used not in fn.__globals__ and not hasattr(builtins, used):
                missing.setdefault(f"{name}:{used}", used)
    assert missing == {}


def test_the_split_module_does_not_import_core_at_module_level():
    """Importing `_core` imports this module: a module-level import back is a cycle."""
    source = __import__("pathlib").Path(_elsevier_api.__file__).read_text(encoding="utf-8")
    top_level = [line for line in source.splitlines() if line.startswith(("from ._core", "import ._core"))]
    assert top_level == []


def test_a_failed_xml_document_does_not_raise():
    assert _elsevier_api._extract_elsevier_pdf_attachment_eids("<not xml") == []


@pytest.mark.parametrize("name", ["_elsevier_pdf_page_count", "_elsevier_attachment_score"])
def test_helpers_are_reachable_from_core(name):
    assert callable(getattr(_core, name))
