"""Source code hygiene rules and scanning.

Scans source code for:
- Hard-coded absolute user / development paths (e.g. D:\\Projects, C:\\Users)
- Hard-coded User-Agent strings drifting away from litloop.network.user_agent()
- Own copies of the text rules litloop.terms.text holds (stop-word lists, dash
  sets, title keys, guessed element symbols); 2026-10-25 there were 15 of them
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

RULE_USER_PATH = "hard-coded user path"
RULE_USER_AGENT = "hard-coded User-Agent"

USER_PATH_PATTERN = re.compile(r"[A-Za-z]:[\\/]+(?:Projects|Users)[\\/]+", re.I)
USER_AGENT_PATTERN = re.compile(r"(?:LitLoop|litloop)/\d+")

UA_EXEMPT_FILENAMES = frozenset({"network.py"})

RULE_OWN_TEXT_RULE = "own copy of a text rule (use litloop.terms.text)"
_DASH_CHAR = r"(?:[‐-―−]|\\u201[0-5]|\\u2212)"
TITLE_KEY_PATTERN = re.compile(r"""\[\^a-z0-9\]\+["']\s*,\s*["']["']""")  # re.sub(r"[^a-z0-9]+", "", title)
DASH_SET_PATTERN = re.compile(rf"\[[^\]\n]*{_DASH_CHAR}[^\]\n]*{_DASH_CHAR}")  # [...] with two kinds of dash
DASH_REPLACE_PATTERN = re.compile(rf"""\.replace\(\s*["']{_DASH_CHAR}""")
ELEMENT_GUESS_PATTERN = re.compile(r"\^\[A-Z\]\[a-z\]\?\$")  # ^[A-Z][a-z]?$
_STOPWORD_SAMPLE = frozenset("a an and the of in on for to with by from at as or".split())
_WORDS_LITERAL = re.compile(r"""["']([a-z]+(?: [a-z]+){5,}) ?["']""")


def _is_text_rules_home(path: Path) -> bool:
    """terms/text.py holds the rules; this file holds the patterns that find copies of them."""
    return (path.name == "text.py" and path.parent.name == "terms") or path.name == "_source_hygiene.py"


def own_text_rule(line: str) -> str:
    """Which rule of litloop.terms.text the line rebuilds, or ""."""
    if TITLE_KEY_PATTERN.search(line):
        return "title key: use text.title_key / text.paper_key"
    if DASH_SET_PATTERN.search(line) or DASH_REPLACE_PATTERN.search(line):
        return "dash set: use text.DASHES / text.ascii_dashes / text.plain"
    if ELEMENT_GUESS_PATTERN.search(line):
        return "element guess: use text.element_symbols / text.is_chemical_system"
    for m in _WORDS_LITERAL.finditer(line):
        if len(set(m.group(1).split()) & _STOPWORD_SAMPLE) >= 5:
            return "stop-word list: use text.STOPWORDS"
    return ""

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent.parent
DEFAULT_SCAN_DIR = PACKAGE_DIR


class HygieneProblem(NamedTuple):
    file: str
    line: int
    rule: str
    detail: str = ""

    def __str__(self) -> str:
        if self.line > 0:
            if self.detail:
                return f"{self.file}:{self.line}: {self.rule}: {self.detail}"
            return f"{self.file}:{self.line}: {self.rule}"
        return f"{self.file}: {self.detail or self.rule}"


def is_test_file(path: Path) -> bool:
    """Return True if path looks like a test file or lives in a tests directory."""
    return path.name.startswith("test_") or "tests" in path.parts


def check_source_hygiene(
    target: Path | str | None = None,
    repo_root: Path | str | None = None,
) -> list[HygieneProblem]:
    """Scan Python source files for hygiene issues.

    Parameters:
        target: A directory to recursively scan or a specific .py file.
                Defaults to src/litloop.
        repo_root: Base directory used to calculate relative file paths.
                   Defaults to the repository root, or target if outside repo.

    Returns:
        A list of HygieneProblem instances.
    """
    if target is None:
        target_path = DEFAULT_SCAN_DIR
        base_dir = Path(repo_root) if repo_root is not None else REPO_ROOT
    else:
        target_path = Path(target)
        if repo_root is not None:
            base_dir = Path(repo_root)
        elif target_path.is_dir():
            base_dir = target_path
        else:
            base_dir = target_path.parent

    if target_path.is_file():
        py_files = [target_path]
    elif target_path.is_dir():
        py_files = sorted(target_path.rglob("*.py"))
    else:
        return []

    problems: list[HygieneProblem] = []
    for py_file in py_files:
        if is_test_file(py_file):
            continue

        try:
            rel = py_file.relative_to(base_dir).as_posix()
        except ValueError:
            rel = py_file.as_posix()

        try:
            lines = py_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError as exc:
            problems.append(
                HygieneProblem(
                    file=rel,
                    line=0,
                    rule="unreadable file",
                    detail=f"could not read file ({exc})",
                )
            )
            continue

        is_network = py_file.name in UA_EXEMPT_FILENAMES
        is_text_home = _is_text_rules_home(py_file)
        for idx, line in enumerate(lines, 1):
            what = "" if is_text_home else own_text_rule(line)
            if what:
                problems.append(HygieneProblem(file=rel, line=idx, rule=RULE_OWN_TEXT_RULE, detail=what))
            if USER_PATH_PATTERN.search(line):
                problems.append(
                    HygieneProblem(
                        file=rel,
                        line=idx,
                        rule=RULE_USER_PATH,
                        detail=line.strip(),
                    )
                )
            if not is_network and USER_AGENT_PATTERN.search(line):
                problems.append(
                    HygieneProblem(
                        file=rel,
                        line=idx,
                        rule=RULE_USER_AGENT,
                        detail=line.strip(),
                    )
                )

    return problems
