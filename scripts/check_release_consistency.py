#!/usr/bin/env python3
"""Release consistency check: one version, everywhere.

Asserts that the version is identical across:

- pyproject.toml
- src/litloop/__init__.py
- .codex-plugin/plugin.json
- zcode-plugin/.zcode-plugin/plugin.json
- zcode-plugin/marketplace.json

and that the MCP tool surface is still 20 tools and the skill no longer
references retired tool names. Exit code 1 with a diff report on any mismatch.

Usage (from the repo root):

    python scripts/check_release_consistency.py [--expect X.Y.Z]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Legacy tool names merged away in the v1.11 tool diet; the skill must not
# advertise them.
RETIRED_TOOLS = (
    "litloop_smart_download",
    "litloop_resolve_and_download",
    "litloop_import_bib",
    "litloop_vpnsci_login",
    "litloop_vpnsci_test",
    "litloop_vpnsci_status",
    "litloop_vpnsci_schools",
    "litloop_vpnsci_set_school",
    "litloop_auto_setup",
    "litloop_setup_check",
    "litloop_source_scores",
    "litloop_network_diagnose",
    "litloop_config_get",
    "litloop_config_set",
    "litloop_tor_install",
    "litloop_tor_start",
    "litloop_tor_stop",
    "litloop_browser_login",
    "litloop_browser_status",
    "litloop_browser_import_cookies",
    "litloop_import_browser_cookies",
    "litloop_ezproxy_login",
    "litloop_ezproxy_status",
)

EXPECTED_TOOL_COUNT = 20


def _read_version_sources() -> list[tuple[str, str]]:
    sources = []

    pyproject = ROOT / "pyproject.toml"
    m = re.search(r'^\s*version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M)
    sources.append(("pyproject.toml", m.group(1) if m else ""))

    init = ROOT / "src" / "litloop" / "__init__.py"
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8"))
    sources.append(("src/litloop/__init__.py", m.group(1) if m else ""))

    for rel in (
        ".codex-plugin/plugin.json",
        "zcode-plugin/.zcode-plugin/plugin.json",
    ):
        data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        sources.append((rel, str(data.get("version", ""))))

    marketplace = json.loads((ROOT / "zcode-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    for plugin in marketplace.get("plugins", []):
        if plugin.get("name") == "litloop":
            sources.append(("zcode-plugin/marketplace.json", str(plugin.get("version", ""))))
            break

    return sources


def _check_mcp_tool_count() -> list[str]:
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from litloop.server import mcp_app
    except Exception as exc:  # pragma: no cover
        return [f"MCP tool count: could not import server ({exc})"]
    try:
        import asyncio

        tools = asyncio.run(mcp_app.list_tools())
    except Exception as exc:  # pragma: no cover
        return [f"MCP tool count: list_tools failed ({exc})"]
    names = {t.name for t in tools}
    problems = []
    if len(tools) != EXPECTED_TOOL_COUNT:
        problems.append(f"MCP tool count: {len(tools)} != {EXPECTED_TOOL_COUNT}")
    for retired in RETIRED_TOOLS:
        if retired in names:
            problems.append(f"MCP tool surface: retired tool still exposed: {retired}")
    return problems


def _check_skill_docs() -> list[str]:
    import subprocess
    problems = []
    # The skill docs are built from skills-src/ (scripts/build_skills.py): an edited
    # output or a forgotten rebuild shows up here.
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_skills.py"), "--check"],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        problems.append(r.stdout.strip() or r.stderr.strip())
    for rel in ("skill/SKILL.md", "skills/litloop/SKILL.md", "zcode-plugin/skills/litloop/SKILL.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for retired in RETIRED_TOOLS:
            if retired in text:
                problems.append(f"{rel}: still references retired tool {retired}")
    return problems


def _check_source_hygiene() -> list[str]:
    sys.path.insert(0, str(ROOT / "src"))
    from litloop._source_hygiene import check_source_hygiene

    return [str(problem) for problem in check_source_hygiene(ROOT / "src" / "litloop", repo_root=ROOT)]



def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect", help="Require this exact version (e.g. 1.12.0)")
    args = parser.parse_args()

    problems: list[str] = []
    sources = _read_version_sources()
    versions = {v for _, v in sources}
    if len(versions) != 1:
        for rel, ver in sources:
            problems.append(f"version mismatch: {rel} = {ver!r}")
    if args.expect and versions != {args.expect}:
        problems.append(f"expected version {args.expect}, got {sorted(versions)}")

    problems += _check_mcp_tool_count()
    problems += _check_skill_docs()
    problems += _check_source_hygiene()

    if problems:
        print("RELEASE CONSISTENCY FAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    version = sorted(versions)[0] if len(versions) == 1 else "?"
    print(f"OK: version {version}, {EXPECTED_TOOL_COUNT} MCP tools, skills clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())