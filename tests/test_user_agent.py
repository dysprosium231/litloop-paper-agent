"""Tests for unified User-Agent generation and prevention of hardcoded UA drift."""

from __future__ import annotations

import re
from pathlib import Path

from litloop import __version__
from litloop.network import USER_AGENT, user_agent


def test_user_agent_contains_version() -> None:
    ua = user_agent()
    assert __version__ in ua
    assert ua == f"LitLoop/{__version__}"

    with_suffix = user_agent("(https://github.com/dysprosium231/litloop-paper-agent)")
    assert with_suffix == f"LitLoop/{__version__} (https://github.com/dysprosium231/litloop-paper-agent)"

    with_leading_space = user_agent(" (https://example.org)")
    assert with_leading_space == f"LitLoop/{__version__} (https://example.org)"

    assert USER_AGENT.endswith(ua)


def test_no_hardcoded_user_agent_in_source() -> None:
    src_dir = Path(__file__).resolve().parent.parent / "src" / "litloop"
    ua_pattern = re.compile(r"(?:LitLoop|litloop)/\d+")
    offending: list[str] = []

    for py_file in sorted(src_dir.rglob("*.py")):
        if py_file.name == "network.py" or py_file.name.startswith("test_") or "tests" in py_file.parts:
            continue
        lines = py_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        for idx, line in enumerate(lines, 1):
            if ua_pattern.search(line):
                rel = py_file.relative_to(src_dir.parent.parent).as_posix()
                offending.append(f"{rel}:{idx}: {line.strip()}")

    assert not offending, f"Found hard-coded User-Agent strings:\n" + "\n".join(offending)
