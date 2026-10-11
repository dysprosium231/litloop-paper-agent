import json

import pytest

from litloop import screening


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr(screening._config, "DATA_DIR", tmp_path)
    return tmp_path


ITEMS = [
    {"doi": "10.1/low", "title": "Alloy coating", "relevance": "低", "reason": "是合金不是陶瓷", "cited_by_count": 50},
    {"doi": "10.1/HIGH", "title": "HEC | thermal", "relevance": "high", "reason": "直接测热导率",
     "journal": "J. Adv. Ceram.", "journal_rank": "中科院1区Top", "abstract": "not stored", "cited_by_count": 9},
    {"doi": "10.1/mid", "title": "HE oxide", "relevance": "中", "reason": "只讨论热膨胀", "abstract_missing": True,
     "retracted": True},
]


def test_save_sorts_by_relevance_and_writes_json_and_markdown():
    r = screening.save("高熵陶瓷热导率", ITEMS, criteria=["高熵陶瓷", "热导率"], query="high-entropy ceramics")
    assert r["added"] == 3 and r["by_relevance"] == {"高": 1, "中": 1, "低": 1, "不相关": 0}
    data = screening.load(r["session"])
    assert [it["doi"] for it in data["items"]] == ["10.1/HIGH", "10.1/mid", "10.1/low"]
    assert "abstract" not in data["items"][0]  # abstracts are not stored
    md = open(r["markdown"], encoding="utf-8").read()
    assert "| 1 | 高 | [HEC / thermal](https://doi.org/10.1/HIGH)" in md
    assert "⚠已撤稿 （仅凭标题）只讨论热膨胀" in md and "- 筛选标准：" in md


def test_bad_relevance_is_rejected():
    with pytest.raises(ValueError):
        screening.save("q", [{"doi": "10.1/x", "relevance": "maybe"}])


def test_merge_keeps_decisions_and_appends_search():
    sid = screening.save("q", ITEMS[:2], query="first")["session"]
    screening.decide(sid, selected=["1"])  # item 1 = 10.1/HIGH
    r = screening.save("q", [ITEMS[1] | {"relevance": "中", "reason": "改判"}, ITEMS[2]],
                       query="second", session=sid)
    assert r["added"] == 1 and r["total"] == 3
    data = screening.load(sid)
    high = next(it for it in data["items"] if it["doi"] == "10.1/HIGH")
    assert high["decision"] == "selected" and high["relevance"] == "中"
    assert [s["query"] for s in data["searches"]] == ["first", "second"]


def test_decide_by_number_or_doi_and_queue(monkeypatch, tmp_path):
    sid = screening.save("q", ITEMS)["session"]
    written = {}
    import litloop.pipeline as pl
    monkeypatch.setattr(pl, "write_queue", lambda qe, path: written.setdefault("ids", [q.identifier for q in qe]) and path)
    r = screening.decide(sid, selected=["1", "10.1/MID"], rejected=["3"], out_file=str(tmp_path / "q.json"))
    assert r["selected"] == 2 and set(r["identifiers"]) == {"10.1/HIGH", "10.1/mid"}
    assert set(written["ids"]) == {"10.1/HIGH", "10.1/mid"} and r["unknown"] == []
    assert screening.decide(sid, selected=["99"])["unknown"] == ["99"]
    assert screening.list_sessions()[0]["selected"] == 2


def test_mcp_tool_roundtrip():
    from litloop.server import litloop_screening as tool
    saved = json.loads(tool(action="save", question="q", items=ITEMS[:1]))
    assert saved["total"] == 1
    assert json.loads(tool(action="decide", session=saved["session"], selected=["1"]))["selected"] == 1
    assert "error" in json.loads(tool(action="show"))
    assert "error" in json.loads(tool(action="bogus"))