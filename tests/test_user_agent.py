"""Tests for unified User-Agent generation and prevention of hardcoded UA drift."""

from __future__ import annotations

from pathlib import Path

from litloop import __version__
from litloop._source_hygiene import (
    RULE_USER_AGENT,
    RULE_USER_PATH,
    check_source_hygiene,
)
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
    problems = [p for p in check_source_hygiene() if p.rule == RULE_USER_AGENT]
    assert not problems, f"Found hard-coded User-Agent strings:\n" + "\n".join(str(p) for p in problems)


def test_source_hygiene_detects_hardcoded_user_paths(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad_paths.py"
    bad_file.write_text(
        r'PROJECT_DIR = "D:\Projects\active\x"' + "\n"
        r'USER_DIR = "C:\Users\someone\x"' + "\n",
        encoding="utf-8",
    )
    problems = check_source_hygiene(tmp_path)
    path_problems = [p for p in problems if p.rule == RULE_USER_PATH]
    assert len(path_problems) == 2
    assert path_problems[0].line == 1
    assert r"D:\Projects\active\x" in path_problems[0].detail
    assert path_problems[1].line == 2
    assert r"C:\Users\someone\x" in path_problems[1].detail


def test_source_hygiene_detects_hardcoded_user_agent(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad_ua.py"
    bad_file.write_text(
        r'CUSTOM_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LitLoop/1.0"' + "\n",
        encoding="utf-8",
    )
    problems = check_source_hygiene(tmp_path)
    ua_problems = [p for p in problems if p.rule == RULE_USER_AGENT]
    assert len(ua_problems) == 1
    assert ua_problems[0].line == 1
    assert "Mozilla/5.0" in ua_problems[0].detail
    assert "LitLoop/1.0" in ua_problems[0].detail


def test_source_hygiene_clean_file(tmp_path: Path) -> None:
    clean_file = tmp_path / "clean_module.py"
    clean_file.write_text(
        'def add(a: int, b: int) -> int:\n'
        '    """Normal python function."""\n'
        '    return a + b\n',
        encoding="utf-8",
    )
    problems = check_source_hygiene(tmp_path)
    assert problems == []

