#!/usr/bin/env python3
"""Build the skill docs from one source: skills-src/.

    python scripts/build_skills.py          # write the skill docs
    python scripts/build_skills.py --check  # exit 1 if any is out of date

Each template in skills-src/ holds what only its doc says; the parts all of
them share live once in skills-src/shared/ and come in through a line
``<!-- include: shared/<name>.md -->``. Edit the sources, never the outputs.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "skills-src"
TARGETS = {
    "litloop.md": "skills/litloop/SKILL.md",           # the skill deploy.py installs into AI clients
    "zcode-litloop.md": "zcode-plugin/skills/litloop/SKILL.md",  # the ZCode plugin's
    "reference.md": "skill/SKILL.md",                   # the full reference
}
INCLUDE = re.compile(r"(?m)^<!-- include: (shared/[\w.-]+\.md) -->\n\n?")
NOTE = "<!-- 由 skills-src/ 生成：改那里的文件，再运行 python scripts/build_skills.py -->\n"


def render(template: str) -> str:
    text = (SRC / template).read_text(encoding="utf-8").replace("\r\n", "\n")
    text = INCLUDE.sub(lambda m: (SRC / m.group(1)).read_text(encoding="utf-8").replace("\r\n", "\n").rstrip("\n") + "\n\n", text)
    # The note goes right after the front matter: a skill must start with "---".
    m = re.match(r"---\n.*?\n---\n", text, re.S)
    assert m, f"{template}: no front matter"
    return text[:m.end()] + NOTE + text[m.end():]


def main() -> int:
    check = "--check" in sys.argv[1:]
    stale = []
    for template, out in TARGETS.items():
        want = render(template)
        path = ROOT / out
        have = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else None
        if have == want:
            continue
        stale.append(out)
        if not check:
            path.write_text(want, encoding="utf-8", newline="\n")
    if check and stale:
        print("skill docs out of date (edit skills-src/, then run python scripts/build_skills.py): " + ", ".join(stale))
        return 1
    print(("wrote " + ", ".join(stale)) if stale and not check else "skill docs up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
