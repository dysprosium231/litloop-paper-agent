"""The app's TypeScript types list every field the backend writes (code review, 2026-10-18)."""

from __future__ import annotations

import re
from pathlib import Path

from litloop import deep_search, store

API_TS = Path(__file__).resolve().parents[1] / "app" / "src" / "lib" / "api.ts"


def _ts_fields(interface: str) -> set[str]:
    text = API_TS.read_text(encoding="utf-8")
    m = re.search(r"export interface " + interface + r" \{\n(.*?)\n\}", text, re.S)
    assert m, f"interface {interface} not found in api.ts"
    return set(re.findall(r"^  (\w+)\??:", m.group(1), re.M))


def test_summary_type_covers_the_deep_search_summary(monkeypatch):
    written = {}
    monkeypatch.setattr(store, "write_json", lambda path, data, **k: written.update(data))
    state = {"session": "s", "question": "q", "pool": {}, "seeds": [], "served": [], "rounds": [{"time": "t", "event": "start"}]}
    deep_search._write_summary(state, {}, {"stop_suggested": False, "stop_reason": ""}, [], [], 0, [])
    missing = set(written) - _ts_fields("Summary")
    assert not missing, f"api.ts Summary lacks {sorted(missing)}"
