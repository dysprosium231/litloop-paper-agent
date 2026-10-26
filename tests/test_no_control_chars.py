r"""No control characters in the code (2026-10-20: a "\b" written through a shell
became a backspace, and deep_search._boolean_heavy never matched for months)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def test_no_control_characters_in_source():
    files = [p for d in ("src", "tests", "scripts", "app/src") for p in (ROOT / d).rglob("*")
             if p.suffix in (".py", ".ts", ".tsx") and "node_modules" not in p.parts and "__pycache__" not in p.parts]
    bad = []
    for p in files:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if _CONTROL.search(line):
                bad.append(f"{p.relative_to(ROOT)}:{i}")
    assert not bad, f"control characters (an escape written through a shell?): {bad}"


def test_boolean_heavy_counts_whole_operators():
    from litloop.search.deep import _boolean_heavy
    assert _boolean_heavy({"search": "a OR b OR c AND d OR e NOT f OR g"})
    assert not _boolean_heavy({"search": "ORANGE ANDROID NOTE a OR b"})
